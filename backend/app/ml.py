"""Versioned Isolation Forest training and inference; scores are not probabilities."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging
import math
import os
import tempfile
from threading import RLock

import joblib
import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .config import settings
from .models import AttendanceRecord, ModelMetadata

logger = logging.getLogger(__name__)
MODEL_PATH = Path(os.getenv("ATTENDANCE_MODEL_PATH", Path(__file__).resolve().parent.parent / "models" / "isolation_forest.joblib"))
FEATURE_NAMES = [
    "checkin_minute_sin",
    "checkin_minute_cos",
    "device_sharing_count_15m",
    "campus_distance_km",
    "attendance_count_7d",
    "timing_deviation_minutes",
]
MODEL_TYPE = "sklearn.ensemble.IsolationForest"
MIN_TRAINING_RECORDS = 20

_cache_lock = RLock()
_cached_artifact = None
_cached_identity = None
_training_status = "not_trained"
_training_error = None

class ModelUnavailableError(RuntimeError):
    pass

def _utc(value: datetime) -> datetime:
    """Database timestamps are stored naive UTC; normalize both old and aware values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def _valid_location(record: AttendanceRecord) -> bool:
    return bool(
        record.location_consent
        and record.latitude is not None and record.longitude is not None
        and math.isfinite(record.latitude) and math.isfinite(record.longitude)
        and -90 <= record.latitude <= 90 and -180 <= record.longitude <= 180
    )

def _campus_distance(record: AttendanceRecord) -> float:
    lat1, lon1 = math.radians(settings.campus_latitude), math.radians(settings.campus_longitude)
    lat2, lon2 = math.radians(record.latitude), math.radians(record.longitude)
    dlat, dlon = lat2-lat1, lon2-lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def _circular_deviation(a: float, b: float) -> float:
    return min(abs(a-b), 1440-abs(a-b))

def _feature_matrix(rows):
    """Extract behavior features from records; absent or denied location remains missing."""
    rows = list(rows)
    if not rows:
        return np.empty((0, len(FEATURE_NAMES)), dtype=float)
    timestamps = [_utc(r.checked_in_at) for r in rows]
    minute_values = [t.hour*60+t.minute+t.second/60 for t in timestamps]
    by_student = defaultdict(list)
    by_device = defaultdict(list)
    for i, record in enumerate(rows):
        by_student[record.student_id].append((timestamps[i], i))
        if record.device_identifier:
            by_device[record.device_identifier].append((timestamps[i], i, record.student_id))
    weekly_counts = [0]*len(rows)
    timing_deviations = [np.nan]*len(rows)
    for items in by_student.values():
        items.sort()
        prior_minutes = []
        left = 0
        for position, (stamp, row_index) in enumerate(items):
            while left < position and items[left][0] < stamp-timedelta(days=7):
                left += 1
            weekly_counts[row_index] = position-left+1
            if len(prior_minutes) >= 4:
                radians_ = np.radians(np.asarray(prior_minutes[-50:], dtype=float)*360/1440)
                mean_sin, mean_cos = float(np.sin(radians_).mean()), float(np.cos(radians_).mean())
                baseline = (math.degrees(math.atan2(mean_sin, mean_cos))*4) % 1440
                timing_deviations[row_index] = _circular_deviation(minute_values[row_index], baseline)
            prior_minutes.append(minute_values[row_index])
    device_shares = [0]*len(rows)
    for items in by_device.values():
        items.sort()
        for i, (stamp, row_index, student_id) in enumerate(items):
            device_shares[row_index] = sum(
                1 for other_stamp, other_index, other_student in items
                if other_student != student_id and other_index != row_index
                and abs((other_stamp-stamp).total_seconds()) <= 15*60
            )
    matrix=[]
    for i, record in enumerate(rows):
        angle=minute_values[i]*2*math.pi/1440
        matrix.append([
            math.sin(angle), math.cos(angle), device_shares[i],
            _campus_distance(record) if _valid_location(record) else np.nan,
            weekly_counts[i], timing_deviations[i],
        ])
    return np.asarray(matrix,dtype=float)

def _eligible_records(db: Session):
    source=list(db.scalars(select(AttendanceRecord).order_by(AttendanceRecord.checked_in_at, AttendanceRecord.id)))
    valid=[]
    for row in source:
        if not isinstance(row.checked_in_at, datetime) or row.attendance_date is None or not isinstance(row.student_id, int):
            continue
        try:
            _utc(row.checked_in_at)
            valid.append(row)
        except (TypeError, ValueError, OverflowError):
            logger.warning("Skipping attendance record %s with an invalid timestamp", row.id)
    return valid, len(source)-len(valid)

def _load_artifact(path: Path = MODEL_PATH, force: bool = False):
    global _cached_artifact, _cached_identity
    if not path.is_file():
        return None
    identity=(str(path.resolve()),path.stat().st_mtime_ns,path.stat().st_size)
    with _cache_lock:
        if not force and _cached_artifact is not None and identity==_cached_identity:
            return _cached_artifact
        artifact=joblib.load(path)
        if not isinstance(artifact,dict):
            raise ModelUnavailableError("Saved model artifact has an unrecognized format")
        if artifact.get("format_version")==2:
            if "pipeline" not in artifact or not hasattr(artifact["pipeline"],"decision_function"):
                raise ModelUnavailableError("Saved model preprocessing pipeline is incomplete")
        elif not all(key in artifact for key in ("scaler","model","features")):
            raise ModelUnavailableError("Legacy model artifact is incomplete")
        _cached_artifact=artifact; _cached_identity=identity
        return artifact

def load_model(path: Path = MODEL_PATH, force: bool = False):
    global _training_status, _training_error
    try:
        artifact=_load_artifact(path,force)
        if artifact is None:
            _training_status="not_trained"
        else:
            if _training_status not in ("training","training_failed"):
                _training_status="ready"
            _training_error=None
        return artifact
    except Exception as exc:
        _training_status="load_failed"; _training_error=str(exc)
        raise ModelUnavailableError(f"Could not load saved Isolation Forest artifact: {exc}") from exc

def train_model(db: Session, path: Path = MODEL_PATH):
    global _training_status, _training_error
    with _cache_lock:
        if _training_status=="training":
            raise RuntimeError("Model training is already in progress")
        _training_status="training"; _training_error=None
    try:
        rows,dropped=_eligible_records(db)
        if len(rows)<MIN_TRAINING_RECORDS:
            raise ValueError(f"At least {MIN_TRAINING_RECORDS} valid attendance records are required; found {len(rows)}")
        features=_feature_matrix(rows)
        if not np.isfinite(features).any():
            raise ValueError("Training records contain no usable behavioral features")
        variations=[]
        for column in features.T:
            observed=column[np.isfinite(column)]
            variations.append(float(np.ptp(observed)) if len(observed)>1 else 0.0)
        if not any(value>1e-9 for value in variations):
            raise ValueError("Attendance records have insufficient feature variation to train a useful model")
        pipeline=Pipeline([
            ("imputer",SimpleImputer(strategy="median",keep_empty_features=True)),
            ("scaler",StandardScaler()),
            ("model",IsolationForest(n_estimators=256,contamination=0.05,random_state=42,n_jobs=-1)),
        ])
        pipeline.fit(features)
        prior_versions=[version for version in db.scalars(select(ModelMetadata.version)) if isinstance(version,str) and version.startswith("2.")]
        patch=max([int(v.split(".")[-1]) for v in prior_versions if v.split(".")[-1].isdigit()] or [0])+1
        version=f"2.0.{patch}"
        trained_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")
        artifact={"format_version":2,"model_type":MODEL_TYPE,"model_version":version,"trained_at":trained_at,"training_records":len(rows),"feature_names":FEATURE_NAMES,"pipeline":pipeline}
        path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix=path.name+".",suffix=".tmp",delete=False) as handle:
            temp_path=Path(handle.name)
        try:
            joblib.dump(artifact,temp_path)
            # Verify the serialized artifact before publishing the new model file.
            saved=joblib.load(temp_path)
            if saved.get("model_version")!=version or not hasattr(saved["pipeline"],"decision_function"):
                raise RuntimeError("Serialized model failed artifact verification")
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)
        load_model(path,force=True)
        predictions=pipeline.predict(features)
        anomaly_scores=-pipeline.decision_function(features)
        metrics={"training_records":len(rows),"dropped_records":dropped,"anomaly_count":int((predictions==-1).sum()),"anomaly_rate":float((predictions==-1).mean()),"mean_anomaly_score":float(anomaly_scores.mean()),"evaluation":"unsupervised training-set diagnostic; not a held-out estimate"}
        labeled=[(row.ground_truth,pred==-1) for row,pred in zip(rows,predictions) if row.ground_truth is not None]
        if labeled:
            truth=[int(item[0]) for item in labeled]; predicted=[int(item[1]) for item in labeled]
            metrics.update({"labeled_rows":len(labeled),"precision":float(precision_score(truth,predicted,zero_division=0)),"recall":float(recall_score(truth,predicted,zero_division=0)),"f1":float(f1_score(truth,predicted,zero_division=0))})
        _training_status="ready"
        return {"model_type":MODEL_TYPE,"model_version":version,"trained_at":trained_at,"training_records":len(rows),"feature_names":FEATURE_NAMES,"model_file_path":str(path.resolve()),"training_status":"ready","metrics":metrics}
    except Exception as exc:
        _training_status="training_failed"; _training_error=str(exc)
        raise

def record_training_failure(db: Session, error: str):
    """Persist failed attempts while leaving the last successful artifact untouched."""
    row=ModelMetadata(name=MODEL_TYPE,version="2.0.failed",training_records=0,feature_names=FEATURE_NAMES,model_file_path=str(MODEL_PATH),training_status="training_failed",error_message=error,metrics={"error":error})
    db.add(row); db.commit()

def predict_record(db: Session, record: AttendanceRecord, path: Path | None = None):
    path=Path(path or MODEL_PATH)
    artifact=load_model(path)
    if artifact is None:
        return None
    if artifact.get("format_version")!=2:
        # Continue using an existing saved v1 artifact until it is retrained. This is a real
        # legacy model prediction, not a synthetic score.
        minute=record.checked_in_at.hour*60+record.checked_in_at.minute
        raw=np.asarray([[minute,float(bool(record.device_identifier)),float(_valid_location(record))]],dtype=float)
        transformed=artifact["scaler"].transform(raw)
        prediction=int(artifact["model"].predict(transformed)[0])
        score=float(-artifact["model"].decision_function(transformed)[0])
        return {"anomaly_score":score,"prediction":prediction,"model_version":"1","model_type":MODEL_TYPE}
    stamp=_utc(record.checked_in_at)
    conditions=[AttendanceRecord.checked_in_at.between(stamp.replace(tzinfo=None)-timedelta(days=7),stamp.replace(tzinfo=None)+timedelta(minutes=15))]
    if record.device_identifier:
        conditions.append(AttendanceRecord.device_identifier==record.device_identifier)
    context=list(db.scalars(select(AttendanceRecord).where(or_(*conditions))))
    if record.id is None or all(item.id!=record.id for item in context):
        context.append(record)
    matrix=_feature_matrix(context)
    index=next((i for i,item in enumerate(context) if record.id is not None and item.id==record.id),len(context)-1)
    sample=matrix[index:index+1]
    pipeline=artifact["pipeline"]
    prediction=int(pipeline.predict(sample)[0])
    score=float(-pipeline.decision_function(sample)[0])
    return {"anomaly_score":score,"prediction":prediction,"model_version":artifact["model_version"],"model_type":artifact["model_type"]}

def runtime_status(path: Path = MODEL_PATH):
    try:
        artifact=load_model(path)
        if artifact is None:
            return {"is_trained":False,"is_loaded":False,"model_file_exists":False,"prediction_ready":False,"training_status":_training_status,"error_message":_training_error}
        return {"is_trained":True,"is_loaded":True,"model_file_exists":Path(path).is_file(),"prediction_ready":True,"model_type":artifact.get("model_type",MODEL_TYPE),"training_records":artifact.get("training_records"),"trained_at":artifact.get("trained_at"),"model_version":artifact.get("model_version","1"),"feature_names":artifact.get("feature_names",artifact.get("features",[])),"training_status":_training_status,"error_message":None}
    except ModelUnavailableError as exc:
        return {"is_trained":False,"is_loaded":False,"model_file_exists":Path(path).is_file(),"prediction_ready":False,"model_type":MODEL_TYPE,"training_status":"load_failed","error_message":str(exc)}

def get_training_status():
    return _training_status, _training_error

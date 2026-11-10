from datetime import date, datetime, timedelta
from pathlib import Path
import numpy as np
import pytest
from sqlalchemy import select
from app.ml import train_model, load_model, predict_record, runtime_status, ModelUnavailableError
from app.models import AttendanceRecord, Student, ModelMetadata

def make_rows(db,count,*,varied=True,with_locations=False):
    students=[]
    for i in range(count):
        student=Student(student_code=f"ML{i}",full_name=f"ML {i}",email=f"ml{i}@example.edu")
        db.add(student); db.flush(); students.append(student)
    today=date.today()
    rows=[]
    for i,student in enumerate(students):
        minute=(i*17)%60 if varied else 30
        hour=8+(i%4 if varied else 0)
        when=datetime.combine(today-timedelta(days=i),datetime.min.time()).replace(hour=hour,minute=minute)
        row=AttendanceRecord(student_id=student.id,attendance_date=when.date(),checked_in_at=when,device_identifier=f"device-{i%3}",location_consent=with_locations,latitude=37.4275+(i%5)*0.001 if with_locations else None,longitude=-122.1697 if with_locations else None)
        db.add(row); rows.append(row)
    db.flush(); return rows

def test_training_rejects_fewer_than_twenty(anomaly_db,tmp_path):
    make_rows(anomaly_db,19)
    with pytest.raises(ValueError,match="20 valid attendance records"):
        train_model(anomaly_db,tmp_path/"model.joblib")

def test_training_exactly_twenty_persists_and_reloads(anomaly_db,tmp_path):
    rows=make_rows(anomaly_db,20)
    path=tmp_path/"model.joblib"
    result=train_model(anomaly_db,path)
    assert result["training_records"]==20 and result["training_status"]=="ready"
    assert result["feature_names"] and path.is_file()
    artifact=load_model(path,force=True)
    assert artifact["model_version"]==result["model_version"]
    prediction=predict_record(anomaly_db,rows[-1],path)
    assert prediction["prediction"] in (-1,1)
    assert np.isfinite(prediction["anomaly_score"])
    assert runtime_status(path)["prediction_ready"] is True

def test_training_more_than_twenty_and_missing_location(anomaly_db,tmp_path):
    make_rows(anomaly_db,32,with_locations=False)
    result=train_model(anomaly_db,tmp_path/"model.joblib")
    assert result["training_records"]==32
    assert result["metrics"]["training_records"]==32

def test_insufficient_feature_variation_is_reported(anomaly_db,tmp_path):
    make_rows(anomaly_db,20,varied=False)
    with pytest.raises(ValueError,match="insufficient feature variation"):
        train_model(anomaly_db,tmp_path/"model.joblib")

def test_corrupt_saved_artifact_is_not_reported_as_ready(tmp_path):
    artifact=tmp_path/"broken.joblib"; artifact.write_text("not a joblib archive")
    status=runtime_status(artifact)
    assert status["training_status"]=="load_failed"
    assert status["prediction_ready"] is False
    with pytest.raises(ModelUnavailableError): load_model(artifact,force=True)

def test_absent_model_returns_no_fake_prediction(anomaly_db,tmp_path):
    rows=make_rows(anomaly_db,20)
    assert predict_record(anomaly_db,rows[0],tmp_path/"missing.joblib") is None

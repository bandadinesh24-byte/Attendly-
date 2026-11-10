from datetime import datetime, timedelta, timezone
from math import radians, sin, cos, sqrt, atan2, degrees
import os
import logging
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from .config import settings
from .models import AttendanceRecord, AnomalyEvent, SuspiciousAlert, Student

WEIGHTS = {
    "device_sharing": int(os.getenv("WEIGHT_DEVICE_SHARING", "30")),
    "location": int(os.getenv("WEIGHT_LOCATION", "25")),
    "timing": int(os.getenv("WEIGHT_TIMING", "15")),
    "similarity": int(os.getenv("WEIGHT_SIMILARITY", "20")),
    "other": int(os.getenv("WEIGHT_OTHER", "10")),
}
logger=logging.getLogger(__name__)
def haversine_km(lat1, lon1, lat2, lon2):
    r=6371.0; dlat=radians(lat2-lat1); dlon=radians(lon2-lon1)
    a=sin(dlat/2)**2+cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2*r*atan2(sqrt(a),sqrt(1-a))
def severity(score): return "critical" if score>=75 else "high" if score>=50 else "medium" if score>=30 else "low"
def _utc_minute(value):
    if value is None: return None
    if value.tzinfo is not None: value=value.astimezone(timezone.utc)
    return value.hour*60+value.minute+value.second/60

def analyze_attendance(db: Session, record: AttendanceRecord):
    prior = list(db.scalars(select(AttendanceRecord).where(AttendanceRecord.student_id==record.student_id, AttendanceRecord.id!=record.id).order_by(AttendanceRecord.checked_in_at.desc()).limit(50)))
    signals=[]; evidence={}
    if record.device_identifier:
        peers=list(db.scalars(select(AttendanceRecord).where(AttendanceRecord.device_identifier==record.device_identifier, AttendanceRecord.student_id!=record.student_id, AttendanceRecord.checked_in_at>=record.checked_in_at-timedelta(minutes=15), AttendanceRecord.checked_in_at<=record.checked_in_at+timedelta(minutes=15))))
        if peers:
            signals.append(("device_sharing", WEIGHTS["device_sharing"])); evidence["device_sharing"]={"device_identifier":record.device_identifier,"related_student_ids":sorted({p.student_id for p in peers}),"window_minutes":15}
    valid_location=(record.latitude is not None and record.longitude is not None and -90<=record.latitude<=90 and -180<=record.longitude<=180)
    if record.location_consent and valid_location:
        campus_distance=haversine_km(record.latitude,record.longitude,settings.campus_latitude,settings.campus_longitude)
        historical=[p for p in prior if p.location_consent and p.latitude is not None and p.longitude is not None and -90<=p.latitude<=90 and -180<=p.longitude<=180]
        historical_distance=None
        if len(historical)>=3:
            base_lat=sum(p.latitude for p in historical)/len(historical); base_lon=sum(p.longitude for p in historical)/len(historical)
            historical_distance=haversine_km(record.latitude,record.longitude,base_lat,base_lon)
        campus_anomaly=campus_distance>2
        historical_anomaly=historical_distance is not None and historical_distance>2
        if campus_anomaly or historical_anomaly:
            signals.append(("location",WEIGHTS["location"])); evidence["location"]={"campus_distance_km":round(campus_distance,2),"campus_threshold_km":2,"campus_deviation":campus_anomaly,"historical_distance_km":round(historical_distance,2) if historical_distance is not None else None,"historical_threshold_km":2,"historical_deviation":historical_anomaly,"historical_baseline_samples":len(historical),"personal_baseline_status":"available" if len(historical)>=3 else "insufficient_history","personal_baseline_explanation":None if len(historical)>=3 else f"Only {len(historical)} consented historical location sample(s); at least 3 are required. No personal-location conclusion was made."}
    # Establish a personal baseline from the student's recent check-ins; require at least four samples.
    valid_prior=[p for p in prior if p.checked_in_at is not None]
    if len(valid_prior)>=4:
        minutes=[_utc_minute(p.checked_in_at) for p in valid_prior]
        current=_utc_minute(record.checked_in_at)
        phase=[minute*2*3.141592653589793/1440 for minute in minutes]
        baseline=(degrees(atan2(sum(sin(x) for x in phase)/len(phase),sum(cos(x) for x in phase)/len(phase))*4))%1440
        deviation=min(abs(current-baseline),1440-abs(current-baseline))
        if deviation>150:
            signals.append(("timing",WEIGHTS["timing"])); evidence["timing"]={"baseline_minute_utc":round(baseline),"observed_minute_utc":round(current),"deviation_minutes":round(deviation),"timezone":"UTC (naive database timestamps are interpreted as UTC)","historical_samples":len(valid_prior)}
    # Similarity only compares records assigned to the same recurring class/session group.
    # Calendar overlap without a comparable session key is not evidence of proxy attendance.
    own_session_rows=[p for p in prior if record.session_group and p.session_group==record.session_group]
    if record.session_group and len(own_session_rows)>=5:
        own_dates={p.attendance_date for p in own_session_rows}
        other_rows=db.execute(select(AttendanceRecord.student_id,AttendanceRecord.attendance_date,AttendanceRecord.checked_in_at,AttendanceRecord.device_identifier).where(AttendanceRecord.student_id!=record.student_id,AttendanceRecord.session_group==record.session_group)).all()
        by_student={}
        for sid, day, checked_in_at, device_id in other_rows: by_student.setdefault(sid,[]).append((day,checked_in_at,device_id))
        own_by_day={}
        for row in own_session_rows: own_by_day.setdefault(row.attendance_date,[]).append(row)
        matches=[]
        for sid, rows in by_student.items():
            dates={day for day,_,_ in rows}
            union=own_dates|dates; score=len(own_dates&dates)/len(union) if union else 0
            shared=own_dates&dates
            # Date overlap alone is common for students on the same timetable. Require
            # unusually exact timing on repeated days with separately registered devices.
            mirrored=0
            for day, other_time, other_device in rows:
                if day not in own_by_day: continue
                other_minute=other_time.hour*60+other_time.minute
                for own in own_by_day[day]:
                    own_minute=_utc_minute(own.checked_in_at)
                    deviation=min(abs(own_minute-other_minute),1440-abs(own_minute-other_minute))
                    if deviation<=1 and own.device_identifier!=other_device:
                        mirrored+=1; break
            if score>=0.9 and len(shared)>=5 and mirrored/len(shared)>=0.8: matches.append((sid,round(score,2),mirrored,sorted(day.isoformat() for day in shared)))
        if matches:
            signals.append(("similarity",WEIGHTS["similarity"])); evidence["similarity"]={"session_group":record.session_group,"related_students":[{"student_id":sid,"jaccard":score,"minute_matched_dates":count,"shared_dates":dates} for sid,score,count,dates in matches[:5]],"criteria":"at least five dates in the same recurring session group, Jaccard >= 0.90, check-in times within one minute on at least 80% of shared dates, and distinct device identifiers"}
    # Model score is a genuine saved-model prediction, stored independently from rule points.
    model_output=None
    try:
        from .ml import predict_record
        model_output=predict_record(db,record)
    except Exception as exc:
        logger.warning("Isolation Forest prediction unavailable for attendance %s: %s",record.id,exc)
    if model_output:
        record.model_anomaly_score=model_output["anomaly_score"]
        record.model_prediction=model_output["prediction"]
        record.model_version=model_output["model_version"]
    # Avoid adding a correlated model score to the rule-based event points.
    score=min(100,sum(points for _,points in signals))
    breakdown={k:next((points for category,points in signals if category==k),0) for k in WEIGHTS}
    return signals,evidence,score,breakdown

def generate_alert(db: Session, record: AttendanceRecord):
    signals,evidence,score,breakdown=analyze_attendance(db,record)
    for category,points in signals:
        db.add(AnomalyEvent(attendance_id=record.id,category=category,points=points,evidence=evidence.get(category,{})))
    if score < settings.alert_threshold or not signals: return None
    existing=db.scalar(select(SuspiciousAlert).where(SuspiciousAlert.attendance_id==record.id))
    if existing: return existing
    student=db.get(Student,record.student_id)
    labels={"device_sharing":"device identifier was also used by another student within 15 minutes","location":"consented check-in location exceeded the campus or available personal-location threshold","timing":"UTC check-in time differed substantially from the student's recent baseline","similarity":"attendance dates and minute-level check-in times closely matched another student in the same recurring session group"}
    explanation=f"Attendance activity for {student.student_code if student else record.student_id} was flagged because " + "; ".join(labels[c] for c,_ in signals) + ". This score is a rule-based risk score, not a probability. Manual verification is recommended."
    alert=SuspiciousAlert(attendance_id=record.id,student_id=record.student_id,category=", ".join(c for c,_ in signals),risk_score=score,severity=severity(score),explanation=explanation,recommendation="Confirm the attendance with the student and review the cited evidence.",evidence=evidence,breakdown=breakdown)
    db.add(alert); db.flush(); return alert

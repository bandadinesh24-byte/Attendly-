from datetime import date, datetime, time, timedelta
from app.detection import analyze_attendance
from app.models import AttendanceRecord, Student

def add_student(db,code):
    student=Student(student_code=code,full_name=code,email=f"{code}@example.edu")
    db.add(student); db.flush(); return student

def test_device_sharing_signal(anomaly_db):
    db=anomaly_db; a=add_student(db,"DEVICE-A"); b=add_student(db,"DEVICE-B"); now=datetime.utcnow()
    db.add(AttendanceRecord(student_id=b.id,attendance_date=date.today(),checked_in_at=now-timedelta(minutes=2),device_identifier="same-opt-in-device"))
    current=AttendanceRecord(student_id=a.id,attendance_date=date.today(),checked_in_at=now,device_identifier="same-opt-in-device")
    db.add(current); db.flush()
    signals,evidence,score,_=analyze_attendance(db,current)
    assert ("device_sharing",30) in signals and b.id in evidence["device_sharing"]["related_student_ids"]
    assert score==30

def test_location_signal_requires_consent(anomaly_db):
    db=anomaly_db; s=add_student(db,"LOCATION-A")
    current=AttendanceRecord(student_id=s.id,attendance_date=date.today(),checked_in_at=datetime.utcnow(),latitude=38.0,longitude=-122.1,location_consent=False)
    db.add(current); db.flush()
    assert "location" not in analyze_attendance(db,current)[1]
    current.location_consent=True
    evidence=analyze_attendance(db,current)[1]["location"]
    assert evidence["campus_deviation"] is True
    assert evidence["historical_baseline_samples"]==0
    assert evidence["personal_baseline_status"]=="insufficient_history"
    assert "No personal-location conclusion" in evidence["personal_baseline_explanation"]

def test_personal_timing_baseline_signal(anomaly_db):
    db=anomaly_db; s=add_student(db,"TIMING-A"); today=date.today()
    for offset in range(1,6):
        day=today-timedelta(days=offset)
        db.add(AttendanceRecord(student_id=s.id,attendance_date=day,checked_in_at=datetime.combine(day,time(9,0))))
    current=AttendanceRecord(student_id=s.id,attendance_date=today,checked_in_at=datetime.combine(today,time(2,0)))
    db.add(current); db.flush()
    assert "timing" in analyze_attendance(db,current)[1]

def test_timing_needs_four_samples_and_uses_utc(anomaly_db):
    from datetime import timezone
    db=anomaly_db; s=add_student(db,"TIMING-B"); today=date.today()
    for offset in range(1,4):
        day=today-timedelta(days=offset)
        db.add(AttendanceRecord(student_id=s.id,attendance_date=day,checked_in_at=datetime.combine(day,time(9,0))))
    current=AttendanceRecord(student_id=s.id,attendance_date=today,checked_in_at=datetime.combine(today,time(2,0),tzinfo=timezone.utc))
    db.add(current); db.flush()
    assert "timing" not in analyze_attendance(db,current)[1]

def test_similar_attendance_dates_signal(anomaly_db):
    db=anomaly_db; a=add_student(db,"SIM-A"); b=add_student(db,"SIM-B"); today=date.today()
    for offset in range(1,8):
        day=today-timedelta(days=offset)
        if day.weekday()<5:
            db.add_all([AttendanceRecord(student_id=a.id,attendance_date=day,checked_in_at=datetime.combine(day,time(9,0)),device_identifier="device-a",session_group="CS101"),AttendanceRecord(student_id=b.id,attendance_date=day,checked_in_at=datetime.combine(day,time(9,0)),device_identifier="device-b",session_group="CS101")])
    current=AttendanceRecord(student_id=a.id,attendance_date=today,checked_in_at=datetime.combine(today,time(9,0)),session_group="CS101")
    db.add(current); db.flush()
    signals,evidence,_,_=analyze_attendance(db,current)
    assert "similarity" in evidence
    assert any(x["student_id"]==b.id for x in evidence["similarity"]["related_students"])

def test_shared_schedule_without_session_evidence_is_not_flagged(anomaly_db):
    db=anomaly_db; a=add_student(db,"SCHEDULE-A"); b=add_student(db,"SCHEDULE-B"); today=date.today()
    for offset in range(1,8):
        day=today-timedelta(days=offset)
        if day.weekday()<5:
            db.add_all([AttendanceRecord(student_id=a.id,attendance_date=day,checked_in_at=datetime.combine(day,time(9,0))),AttendanceRecord(student_id=b.id,attendance_date=day,checked_in_at=datetime.combine(day,time(9,0)))])
    current=AttendanceRecord(student_id=a.id,attendance_date=today,checked_in_at=datetime.combine(today,time(9,0)))
    db.add(current); db.flush()
    assert "similarity" not in analyze_attendance(db,current)[1]

def test_normal_shared_session_schedule_not_flagged_for_minute_variation(anomaly_db):
    db=anomaly_db; a=add_student(db,"CLASS-A"); b=add_student(db,"CLASS-B"); today=date.today()
    for offset in range(1,8):
        day=today-timedelta(days=offset)
        if day.weekday()<5:
            db.add_all([AttendanceRecord(student_id=a.id,attendance_date=day,checked_in_at=datetime.combine(day,time(9,0)),session_group="BIO101",device_identifier="phone-a"),AttendanceRecord(student_id=b.id,attendance_date=day,checked_in_at=datetime.combine(day,time(9,5)),session_group="BIO101",device_identifier="phone-b")])
    current=AttendanceRecord(student_id=a.id,attendance_date=today,checked_in_at=datetime.combine(today,time(9,0)),session_group="BIO101")
    db.add(current); db.flush()
    assert "similarity" not in analyze_attendance(db,current)[1]

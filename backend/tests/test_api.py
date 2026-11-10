def test_login_and_admin_authorization(client,admin_headers):
    assert client.get("/api/students",headers=admin_headers).status_code==200
    assert client.get("/api/students").status_code==401

def test_student_cannot_read_other_student(client):
    response=client.post("/api/auth/login",json={"email":"avery@example.edu","password":"student-password-123"})
    token=response.json()["access_token"]
    assert client.get("/api/students/2",headers={"Authorization":"Bearer "+token}).status_code==403
    assert client.get("/api/ai/model-status",headers={"Authorization":"Bearer "+token}).status_code==403

def test_json_login_and_bearer_openapi(client):
    assert client.post("/api/auth/login",json={"email":"admin@example.edu","password":"wrong-password"}).status_code==401
    schema=client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"]=="bearer"
    assert schema["paths"]["/api/analytics/dashboard"]["get"]["security"]==[{"HTTPBearer":[]}]

def test_admin_json_login_returns_jwt_and_missing_credentials_are_rejected(client):
    login=client.post("/api/auth/login",json={"email":"admin@example.edu","password":"test-password-123"})
    assert login.status_code==200
    result=login.json()
    assert result["token_type"]=="bearer" and result["role"]=="admin" and result["access_token"]
    assert client.get("/api/students",headers={"Authorization":f"Bearer {result['access_token']}"}).status_code==200
    assert client.post("/api/auth/login",json={"email":"admin@example.edu","password":"wrong-password"}).status_code==401
    assert client.post("/api/auth/login",json={}).status_code==422
    assert client.post("/api/auth/login").status_code==422

def test_health_endpoint(client):
    assert client.get("/api/health").json()=={"status":"ok"}

def test_local_frontend_origins_can_send_json_and_bearer_requests(client):
    for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
        response=client.options("/api/auth/login",headers={"Origin":origin,"Access-Control-Request-Method":"POST","Access-Control-Request-Headers":"content-type,authorization"})
        assert response.status_code==200
        assert response.headers["access-control-allow-origin"]==origin
        assert response.headers["access-control-allow-credentials"]=="true"

def test_invalid_and_expired_tokens(client):
    assert client.get("/api/students",headers={"Authorization":"Bearer invalid"}).status_code==401
    from datetime import datetime, timedelta
    from jose import jwt
    from app.config import settings
    token=jwt.encode({"sub":"1","role":"admin","exp":datetime.utcnow()-timedelta(seconds=5)},settings.secret_key,algorithm="HS256")
    assert client.get("/api/students",headers={"Authorization":"Bearer "+token}).status_code==401

def test_attendance_and_duplicate_prevention(client,admin_headers):
    session=client.post("/api/attendance/session",headers=admin_headers).json()
    payload={"student_id":1,"session_code":session["session_code"],"device_identifier":"test-device"}
    submitted=client.post("/api/attendance/mark",json=payload,headers=admin_headers)
    assert submitted.status_code==200
    listed=client.get("/api/attendance?limit=10",headers=admin_headers)
    assert listed.status_code==200 and any(row["id"]==submitted.json()["attendance_id"] for row in listed.json())
    assert client.post("/api/attendance/mark",json=payload,headers=admin_headers).status_code==409

def test_attendance_rejects_unknown_student_and_invalid_timestamp(client,admin_headers):
    unknown=client.post("/api/attendance/mark",headers=admin_headers,json={"student_id":9999})
    assert unknown.status_code==404
    invalid=client.post("/api/attendance/mark",headers=admin_headers,json={"student_id":1,"checked_in_at":"not-a-timestamp"})
    assert invalid.status_code==422

def test_student_update_rejects_duplicate_code_or_email(client,admin_headers):
    response=client.put("/api/students/1",headers=admin_headers,json={"student_code":"STU1002","full_name":"Updated Avery","email":"avery@example.edu","department":"Science"})
    assert response.status_code==409
    unchanged=client.get("/api/students/1",headers=admin_headers)
    assert unchanged.status_code==200
    assert unchanged.json()["student_code"]=="STU1001"

def test_student_create_view_update_and_deactivate(client,admin_headers):
    body={"student_code":"STU2001","full_name":"Jordan Example","email":"jordan@example.edu","department":"Science"}
    created=client.post("/api/students",headers=admin_headers,json=body)
    assert created.status_code==200
    student_id=created.json()["id"]
    assert client.post("/api/students",headers=admin_headers,json=body).status_code==409
    details=client.get(f"/api/students/{student_id}",headers=admin_headers)
    assert details.status_code==200 and details.json()["full_name"]=="Jordan Example"
    body["full_name"]="Jordan Updated"
    updated=client.put(f"/api/students/{student_id}",headers=admin_headers,json=body)
    assert updated.status_code==200 and updated.json()["full_name"]=="Jordan Updated"
    deactivated=client.delete(f"/api/students/{student_id}",headers=admin_headers)
    assert deactivated.status_code==200 and deactivated.json()["active"] is False

def test_demo_seed_reports_persisted_counts_and_is_idempotent(client,admin_headers):
    first=client.post("/api/demo/seed",headers=admin_headers)
    assert first.status_code==200
    result=first.json()
    assert result["students_created"]==500
    assert result["attendance_records_created"]>5000
    assert result["alerts_created"]>0
    assert result["demo_students"]==500
    from app.models import SuspiciousAlert
    with client.test_session_factory() as db:
        categories=[alert.category for alert in db.query(SuspiciousAlert).all()]
    assert any("device_sharing" in category for category in categories)
    assert any("location" in category for category in categories)
    assert any("similarity" in category for category in categories)

    dashboard=client.get("/api/analytics/dashboard",headers=admin_headers)
    assert dashboard.status_code==200
    assert dashboard.json()["total_students"]==result["total_students"]
    assert dashboard.json()["total_records"]==result["total_attendance_records"]
    assert dashboard.json()["suspicious_records"]==result["total_alerts"]

    second=client.post("/api/demo/seed",headers=admin_headers)
    assert second.status_code==200
    repeated=second.json()
    assert repeated["students_created"]==0
    assert repeated["attendance_records_created"]==0
    assert repeated["alerts_created"]==0
    assert repeated["total_attendance_records"]==result["total_attendance_records"]

def test_shared_device_generates_alert_and_status_action(client,admin_headers):
    result=client.post("/api/attendance/demo?student_id=1&case=shared_device",headers=admin_headers)
    assert result.status_code==200
    assert result.json()["alert"]["risk_score"]>=30
    alert_id=result.json()["alert"]["id"]
    filtered=client.get("/api/alerts?risk=medium&status=pending&search=STU1001",headers=admin_headers)
    assert filtered.status_code==200 and filtered.json()["total"]==1
    updated=client.post(f"/api/alerts/{alert_id}/review",json={"note":"Reviewed demo evidence"},headers=admin_headers)
    assert updated.status_code==200
    assert updated.json()["status"]=="reviewed"
    detail=client.get(f"/api/alerts/{alert_id}",headers=admin_headers).json()
    action=detail["history"][-1]
    assert action["administrator_id"]==1 and action["previous_status"]=="pending" and action["new_status"]=="reviewed"

def test_attendance_submission_persists_real_model_prediction(client,admin_headers,tmp_path,monkeypatch):
    from datetime import date, datetime, timedelta
    from app import ml
    from app.models import AttendanceRecord
    with client.test_session_factory() as db:
        day=date.today()
        for offset in range(1,21):
            prior=day-timedelta(days=offset)
            db.add(AttendanceRecord(student_id=1,attendance_date=prior,checked_in_at=datetime.combine(prior,datetime.min.time()).replace(hour=8+offset%4,minute=offset%60),device_identifier=f"device-{offset%3}"))
        db.flush()
        path=tmp_path/"integration-model.joblib"
        result=ml.train_model(db,path)
        assert result["training_records"]==20
    monkeypatch.setattr(ml,"MODEL_PATH",path)
    session=client.post("/api/attendance/session",headers=admin_headers).json()
    response=client.post("/api/attendance/mark",headers=admin_headers,json={"student_id":1,"session_code":session["session_code"],"device_identifier":"new-device"})
    assert response.status_code==200
    prediction=response.json()["model_prediction"]
    assert prediction is not None
    assert prediction["prediction"] in (-1,1)
    assert prediction["model_version"]==result["model_version"]
    assert prediction["score_type"]=="Isolation Forest anomaly score, not a probability"

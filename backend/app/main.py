from datetime import datetime, date, timedelta, timezone
from hashlib import blake2b
import csv, io, secrets
from fastapi import FastAPI, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session
from .database import Base, engine, get_db
from .models import User, Student, AttendanceRecord, AttendanceSession, SuspiciousAlert, AdminAction, InvestigationNote, ModelMetadata
from .schemas import LoginRequest, RegisterRequest, StudentCreate, AttendanceCreate, ActionRequest
from .security import hash_password, check_password, issue_token, current_user, require_admin
from .detection import generate_alert, severity
from .config import settings, utc_now_naive, app_today
from jose import jwt, JWTError

Base.metadata.create_all(bind=engine)
app=FastAPI(title="AI Attendance Anomaly Detector",version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
clients=set()
def student_json(s): return {"id":s.id,"student_code":s.student_code,"full_name":s.full_name,"email":s.email,"department":s.department,"active":s.active}
def alert_json(a, db):
    s=db.get(Student,a.student_id); r=db.get(AttendanceRecord,a.attendance_id)
    return {"id":a.id,"attendance_id":a.attendance_id,"student_id":a.student_id,"student_code":s.student_code if s else "","student_name":s.full_name if s else "Unknown","category":a.category,"risk_score":a.risk_score,"severity":a.severity,"explanation":a.explanation,"recommendation":a.recommendation,"evidence":a.evidence,"breakdown":a.breakdown,"status":a.status,"created_at":a.created_at.isoformat(),"checked_in_at":r.checked_in_at.isoformat() if r else None,"model_prediction":{"anomaly_score":r.model_anomaly_score,"prediction":r.model_prediction,"model_version":r.model_version,"score_type":"Isolation Forest anomaly score; not a probability"} if r and r.model_prediction is not None else None}
async def broadcast(message):
    for ws in list(clients):
        try: await ws.send_json(message)
        except Exception: clients.discard(ws)

@app.get("/api/health")
def health(): return {"status":"ok"}
@app.post("/api/auth/register")
def register(body:RegisterRequest,db:Session=Depends(get_db)):
    if body.role != "student": raise HTTPException(400,"Public registration is limited to student accounts; create administrators with scripts/create_admin.py")
    if db.scalar(select(User).where(User.email==body.email)): raise HTTPException(409,"Email already registered")
    student=None
    if body.role=="student":
        if not body.student_code or not body.full_name: raise HTTPException(422,"Student accounts require student_code and full_name")
        student=db.scalar(select(Student).where(Student.student_code==body.student_code))
        if student and student.email.lower()!=body.email.lower(): raise HTTPException(403,"Student code and enrolled email do not match")
        if not student: student=Student(student_code=body.student_code,full_name=body.full_name,email=body.email); db.add(student); db.flush()
    user=User(email=body.email,password_hash=hash_password(body.password),role=body.role,student_id=student.id if student else None); db.add(user); db.commit()
    return {"access_token":issue_token(user),"token_type":"bearer","role":user.role}
@app.post("/api/auth/login")
def login(body:LoginRequest,db:Session=Depends(get_db)):
    user=db.scalar(select(User).where(User.email==body.email))
    if not user or not check_password(body.password,user.password_hash): raise HTTPException(401,"Incorrect email or password")
    return {"access_token":issue_token(user),"token_type":"bearer","role":user.role,"student_id":user.student_id}

@app.get("/api/students")
def students(search:str|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),db:Session=Depends(get_db),_:User=Depends(require_admin)):
    q=select(Student).order_by(Student.id)
    if search: q=q.where(Student.student_code.ilike(f"%{search}%")|Student.full_name.ilike(f"%{search}%"))
    return {"items":[student_json(s) for s in db.scalars(q.offset(offset).limit(limit))],"total":db.scalar(select(func.count()).select_from(Student))}
@app.post("/api/students")
def create_student(body:StudentCreate,db:Session=Depends(get_db),_:User=Depends(require_admin)):
    if db.scalar(select(Student).where((Student.student_code==body.student_code)|(Student.email==body.email))): raise HTTPException(409,"Student code or email already exists")
    s=Student(**body.model_dump()); db.add(s); db.commit(); db.refresh(s); return student_json(s)
@app.get("/api/students/{student_id}")
def get_student(student_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if user.role!="admin" and user.student_id!=student_id: raise HTTPException(403,"Cannot view another student's record")
    s=db.get(Student,student_id)
    if not s: raise HTTPException(404,"Student not found")
    return student_json(s)
@app.put("/api/students/{student_id}")
def update_student(student_id:int,body:StudentCreate,db:Session=Depends(get_db),_:User=Depends(require_admin)):
    s=db.get(Student,student_id)
    if not s: raise HTTPException(404,"Student not found")
    duplicate=db.scalar(select(Student.id).where(Student.id!=student_id,or_(Student.student_code==body.student_code,Student.email==body.email)))
    if duplicate: raise HTTPException(409,"Student code or email already exists")
    for k,v in body.model_dump().items(): setattr(s,k,v)
    db.commit(); return student_json(s)
@app.delete("/api/students/{student_id}")
def deactivate_student(student_id:int,db:Session=Depends(get_db),_:User=Depends(require_admin)):
    s=db.get(Student,student_id)
    if not s: raise HTTPException(404,"Student not found")
    s.active=False; db.commit(); return {"ok":True,"active":False}

@app.post("/api/attendance/session")
def make_session(course_code:str|None=Query(None,max_length=80),db:Session=Depends(get_db),_:User=Depends(require_admin)):
    code=secrets.token_urlsafe(20); session=AttendanceSession(session_code=code,course_code=course_code.strip() if course_code else None,expires_at=utc_now_naive()+timedelta(seconds=settings.qr_ttl_seconds)); db.add(session); db.commit()
    return {"session_code":code,"expires_at":session.expires_at.isoformat(),"ttl_seconds":settings.qr_ttl_seconds}
@app.post("/api/attendance/session/qr")
def make_qr_session(course_code:str|None=Query(None,max_length=80),db:Session=Depends(get_db),_:User=Depends(require_admin)):
    import qrcode
    from fastapi.responses import Response
    code=secrets.token_urlsafe(20); session=AttendanceSession(session_code=code,course_code=course_code.strip() if course_code else None,expires_at=utc_now_naive()+timedelta(seconds=settings.qr_ttl_seconds)); db.add(session); db.commit()
    image=qrcode.make(code); buffer=io.BytesIO(); image.save(buffer,format="PNG")
    return Response(content=buffer.getvalue(),media_type="image/png",headers={"X-Session-Code":code,"X-Expires-At":session.expires_at.isoformat()})
@app.post("/api/attendance/mark")
async def mark_attendance(body:AttendanceCreate,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if user.role!="admin" and user.student_id!=body.student_id: raise HTTPException(403,"Cannot submit attendance for another student")
    if user.role!="admin" and body.source!="qr": raise HTTPException(403,"Students must check in using an active QR session")
    student=db.get(Student,body.student_id)
    if not student or not student.active: raise HTTPException(404,"Active student not found")
    session=None
    if body.source=="qr":
        session=db.scalar(select(AttendanceSession).where(AttendanceSession.session_code==body.session_code)) if body.session_code else None
        if not session or session.expires_at<utc_now_naive(): raise HTTPException(400,"QR session is invalid or expired")
    if db.scalar(select(AttendanceRecord).where(AttendanceRecord.student_id==body.student_id,AttendanceRecord.attendance_date==app_today())): raise HTTPException(409,"Attendance already recorded today")
    if not body.location_consent: body.latitude=None; body.longitude=None
    checked_in_at=(body.checked_in_at if user.role=="admin" else utc_now_naive()) or utc_now_naive()
    if checked_in_at.tzinfo is not None: checked_in_at=checked_in_at.astimezone(timezone.utc).replace(tzinfo=None)
    r=AttendanceRecord(student_id=body.student_id,attendance_date=app_today(),checked_in_at=checked_in_at,device_identifier=body.device_identifier,latitude=body.latitude,longitude=body.longitude,location_consent=body.location_consent,source=body.source,session_group=session.course_code if session else None)
    db.add(r); db.flush(); alert=generate_alert(db,r); db.commit(); db.refresh(r)
    if alert: await broadcast({"type":"alert","alert":alert_json(alert,db)})
    return {"attendance_id":r.id,"status":"present","checked_in_at":r.checked_in_at.isoformat(),"model_prediction":{"anomaly_score":r.model_anomaly_score,"prediction":r.model_prediction,"model_version":r.model_version,"score_type":"Isolation Forest anomaly score, not a probability"} if r.model_prediction is not None else None,"alert":alert_json(alert,db) if alert else None}
@app.post("/api/attendance/demo")
async def demo_attendance(student_id:int,case:str="normal",db:Session=Depends(get_db),_:User=Depends(require_admin)):
    s=db.get(Student,student_id)
    if not s: raise HTTPException(404,"Student not found")
    if db.scalar(select(AttendanceRecord).where(AttendanceRecord.student_id==student_id,AttendanceRecord.attendance_date==app_today())): raise HTTPException(409,"Attendance already recorded today")
    now=utc_now_naive(); device=f"demo-device-{student_id}"; lat=lon=None; consent=False
    if case in ("unusual_timing","similarity"):
        for offset in range(1,7):
            day=app_today()-timedelta(days=offset)
            if day.weekday()<5 and not db.scalar(select(AttendanceRecord).where(AttendanceRecord.student_id==student_id,AttendanceRecord.attendance_date==day)):
                db.add(AttendanceRecord(student_id=student_id,attendance_date=day,checked_in_at=datetime.combine(day,datetime.min.time()).replace(hour=9,minute=10),device_identifier=f"demo-device-{student_id}",source="demo-baseline",ground_truth=False,session_group="DEMO-COORDINATED-101" if case=="similarity" else None))
        db.flush()
    if case in ("shared_device","combined"):
        peer=db.scalar(select(Student).where(Student.id!=student_id))
        if peer:
            if not db.scalar(select(AttendanceRecord).where(AttendanceRecord.student_id==peer.id,AttendanceRecord.attendance_date==app_today())):
                db.add(AttendanceRecord(student_id=peer.id,attendance_date=app_today(),checked_in_at=now-timedelta(minutes=3),device_identifier="demo-shared-device",source="demo",ground_truth=True)); db.flush()
            device="demo-shared-device"
    if case in ("unusual_location","combined"): lat=settings.campus_latitude+0.08; lon=settings.campus_longitude; consent=True
    if case=="unusual_timing": now=now.replace(hour=2,minute=5)
    if case=="similarity":
        peer=db.scalar(select(Student).where(Student.id!=student_id))
        if peer:
            for offset in range(1,7):
                day=app_today()-timedelta(days=offset)
                if day.weekday()<5 and not db.scalar(select(AttendanceRecord).where(AttendanceRecord.student_id==peer.id,AttendanceRecord.attendance_date==day)):
                    db.add(AttendanceRecord(student_id=peer.id,attendance_date=day,checked_in_at=datetime.combine(day,datetime.min.time()).replace(hour=9,minute=10),device_identifier=f"demo-device-{peer.id}",source="demo-baseline",ground_truth=False,session_group="DEMO-COORDINATED-101"))
            db.flush()
    r=AttendanceRecord(student_id=student_id,attendance_date=app_today(),checked_in_at=now,device_identifier=device,latitude=lat,longitude=lon,location_consent=consent,source="demo",ground_truth=case!="normal",session_group="DEMO-COORDINATED-101" if case=="similarity" else None)
    db.add(r); db.flush(); alert=generate_alert(db,r); db.commit(); db.refresh(r)
    if alert: await broadcast({"type":"alert","alert":alert_json(alert,db)})
    return {"attendance_id":r.id,"alert":alert_json(alert,db) if alert else None,"message":"Processed by the active detection engine"}

@app.get("/api/attendance")
def attendance(limit:int=Query(100,ge=1,le=500),offset:int=0,db:Session=Depends(get_db),_:User=Depends(require_admin)):
    return [{"id":r.id,"student_id":r.student_id,"student_code":db.get(Student,r.student_id).student_code,"student_name":db.get(Student,r.student_id).full_name,"date":r.attendance_date.isoformat(),"checked_in_at":r.checked_in_at.isoformat(),"status":r.status,"device_identifier":r.device_identifier} for r in db.scalars(select(AttendanceRecord).order_by(AttendanceRecord.checked_in_at.desc()).offset(offset).limit(limit))]
@app.get("/api/attendance/student/{student_id}")
def student_attendance(student_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if user.role!="admin" and user.student_id!=student_id: raise HTTPException(403,"Cannot view another student's history")
    return [{"id":r.id,"date":r.attendance_date.isoformat(),"checked_in_at":r.checked_in_at.isoformat(),"status":r.status} for r in db.scalars(select(AttendanceRecord).where(AttendanceRecord.student_id==student_id).order_by(AttendanceRecord.checked_in_at.desc()))]
@app.get("/api/attendance/{attendance_id}")
def get_attendance(attendance_id:int,db:Session=Depends(get_db),_:User=Depends(require_admin)):
    r=db.get(AttendanceRecord,attendance_id)
    if not r: raise HTTPException(404,"Attendance record not found")
    return {"id":r.id,"student_id":r.student_id,"checked_in_at":r.checked_in_at.isoformat(),"device_identifier":r.device_identifier,"latitude":r.latitude if r.location_consent else None,"longitude":r.longitude if r.location_consent else None,"location_consent":r.location_consent,"source":r.source,"model_prediction":{"anomaly_score":r.model_anomaly_score,"prediction":r.model_prediction,"model_version":r.model_version,"score_type":"Isolation Forest anomaly score, not a probability"} if r.model_prediction is not None else None}

@app.get("/api/alerts")
def alerts(risk:str|None=None,status:str|None=None,student_id:int|None=None,search:str|None=None,date_from:date|None=None,date_to:date|None=None,limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),db:Session=Depends(get_db),_:User=Depends(require_admin)):
    q=select(SuspiciousAlert).order_by(SuspiciousAlert.created_at.desc())
    if risk: q=q.where(SuspiciousAlert.severity==risk.lower())
    if status: q=q.where(SuspiciousAlert.status==status.lower())
    if student_id: q=q.where(SuspiciousAlert.student_id==student_id)
    if date_from: q=q.where(func.date(SuspiciousAlert.created_at)>=date_from)
    if date_to: q=q.where(func.date(SuspiciousAlert.created_at)<=date_to)
    result=[alert_json(a,db) for a in db.scalars(q.offset(offset).limit(limit))]
    if search: result=[a for a in result if search.lower() in (a["student_name"]+a["student_code"]+a["category"]).lower()]
    return {"items":result,"total":len(result)}
@app.get("/api/alerts/{alert_id}")
def alert_detail(alert_id:int,db:Session=Depends(get_db),_:User=Depends(require_admin)):
    a=db.get(SuspiciousAlert,alert_id)
    if not a: raise HTTPException(404,"Alert not found")
    r=db.get(AttendanceRecord,a.attendance_id); s=db.get(Student,a.student_id); data=alert_json(a,db)
    data.update({"student":student_json(s),"attendance":{"id":r.id,"checked_in_at":r.checked_in_at.isoformat(),"date":r.attendance_date.isoformat(),"device_identifier":r.device_identifier,"location_consent":r.location_consent,"latitude":r.latitude if r.location_consent else None,"longitude":r.longitude if r.location_consent else None,"session_group":r.session_group,"model_prediction":{"anomaly_score":r.model_anomaly_score,"prediction":r.model_prediction,"model_version":r.model_version} if r.model_prediction is not None else None},"notes":[{"note":n.note,"created_at":n.created_at.isoformat()} for n in db.scalars(select(InvestigationNote).where(InvestigationNote.alert_id==a.id))],"history":[{"administrator_id":h.admin_user_id,"action":h.action,"previous_status":h.previous_status,"new_status":h.new_status,"details":h.details,"created_at":h.created_at.isoformat()} for h in db.scalars(select(AdminAction).where(AdminAction.alert_id==a.id).order_by(AdminAction.created_at))]})
    return data
def change_alert(alert_id,action,body,db,user):
    a=db.get(SuspiciousAlert,alert_id)
    if not a: raise HTTPException(404,"Alert not found")
    previous_status=a.status
    a.status="reviewed" if action=="review" else action
    db.add(AdminAction(alert_id=a.id,admin_user_id=user.id,action=action,previous_status=previous_status,new_status=a.status,details={"note":body.note} if body.note else {}))
    if body.note: db.add(InvestigationNote(alert_id=a.id,admin_user_id=user.id,note=body.note))
    db.commit(); return alert_json(a,db)
@app.post("/api/alerts/{alert_id}/review")
def review_alert(alert_id:int,body:ActionRequest,db:Session=Depends(get_db),user:User=Depends(require_admin)): return change_alert(alert_id,"review",body,db,user)
@app.post("/api/alerts/{alert_id}/verify")
def verify_alert(alert_id:int,body:ActionRequest,db:Session=Depends(get_db),user:User=Depends(require_admin)): return change_alert(alert_id,"verified",body,db,user)
@app.post("/api/alerts/{alert_id}/dismiss")
def dismiss_alert(alert_id:int,body:ActionRequest,db:Session=Depends(get_db),user:User=Depends(require_admin)): return change_alert(alert_id,"dismissed",body,db,user)
@app.post("/api/alerts/{alert_id}/escalate")
def escalate_alert(alert_id:int,body:ActionRequest,db:Session=Depends(get_db),user:User=Depends(require_admin)): return change_alert(alert_id,"escalated",body,db,user)

@app.get("/api/analytics/dashboard")
def dashboard(db:Session=Depends(get_db),_:User=Depends(require_admin)):
    total=db.scalar(select(func.count()).select_from(Student)) or 0; records=db.scalar(select(func.count()).select_from(AttendanceRecord)) or 0; n=db.scalar(select(func.count()).select_from(SuspiciousAlert)) or 0
    pending=db.scalar(select(func.count()).select_from(SuspiciousAlert).where(SuspiciousAlert.status=="pending")) or 0; critical=db.scalar(select(func.count()).select_from(SuspiciousAlert).where(SuspiciousAlert.severity=="critical")) or 0
    recent=list(db.scalars(select(SuspiciousAlert).order_by(SuspiciousAlert.created_at.desc()).limit(6)))
    by_day=db.execute(select(AttendanceRecord.attendance_date,func.count(AttendanceRecord.id)).group_by(AttendanceRecord.attendance_date).order_by(AttendanceRecord.attendance_date.desc()).limit(14)).all()
    distribution={x:(db.scalar(select(func.count()).select_from(SuspiciousAlert).where(SuspiciousAlert.severity==x)) or 0) for x in ["low","medium","high","critical"]}
    return {"total_students":total,"total_records":records,"normal_records":records-n,"suspicious_records":n,"pending_alerts":pending,"critical_alerts":critical,"recent_alerts":[alert_json(a,db) for a in recent],"attendance_by_day":[{"date":d.isoformat(),"count":c} for d,c in reversed(by_day)],"risk_distribution":distribution}
@app.get("/api/analytics/anomalies")
def anomalies(db:Session=Depends(get_db),_:User=Depends(require_admin)):
    return {"total":db.scalar(select(func.count()).select_from(SuspiciousAlert)) or 0,"categories":{c:db.scalar(select(func.count()).select_from(SuspiciousAlert).where(SuspiciousAlert.category.ilike(f"%{c}%"))) or 0 for c in ["device_sharing","location","timing","similarity"]}}
@app.get("/api/analytics/risk-distribution")
def risk_dist(db:Session=Depends(get_db),_:User=Depends(require_admin)):
    return {x:db.scalar(select(func.count()).select_from(SuspiciousAlert).where(SuspiciousAlert.severity==x)) or 0 for x in ["low","medium","high","critical"]}
@app.post("/api/ai/analyze/{attendance_id}")
def ai_analyze(attendance_id:int,db:Session=Depends(get_db),_:User=Depends(require_admin)):
    from .detection import analyze_attendance
    r=db.get(AttendanceRecord,attendance_id)
    if not r: raise HTTPException(404,"Attendance record not found")
    signals,evidence,score,breakdown=analyze_attendance(db,r)
    db.commit()
    return {"rule_based_risk_score":score,"severity":severity(score),"signals":[{"category":c,"points":p} for c,p in signals],"evidence":evidence,"score_breakdown":breakdown,"model_prediction":{"anomaly_score":r.model_anomaly_score,"prediction":r.model_prediction,"model_version":r.model_version,"score_type":"Isolation Forest decision-based anomaly score, not a probability"} if r.model_prediction is not None else None,"score_is_probability":False}
@app.get("/api/ai/model-status")
def model_status(db:Session=Depends(get_db),_:User=Depends(require_admin)):
    from .ml import MODEL_PATH, runtime_status, get_training_status, MODEL_TYPE
    status=runtime_status(MODEL_PATH)
    latest=db.scalar(select(ModelMetadata).order_by(ModelMetadata.id.desc()).limit(1))
    in_process_state,error=get_training_status()
    if in_process_state in ("training","training_failed","load_failed") or status.get("training_status")=="not_trained":
        status["training_status"]=in_process_state
    if error: status["error_message"]=error
    status.update({"model_type":status.get("model_type",MODEL_TYPE),"training_records":status.get("training_records") if status.get("training_records") is not None else (latest.training_records if latest else 0),"trained_at":status.get("trained_at") or (latest.trained_at.isoformat() if latest else None),"model_version":status.get("model_version") or (latest.version if latest else None),"feature_names":status.get("feature_names") or (latest.feature_names if latest else []),"model_file_path":str(MODEL_PATH),"last_training_status":latest.training_status if latest else "not_trained"})
    return status
@app.post("/api/ai/train")
def train_ai(db:Session=Depends(get_db),_:User=Depends(require_admin)):
    from .ml import train_model, MODEL_PATH, MODEL_TYPE, FEATURE_NAMES, record_training_failure
    try:
        result=train_model(db,MODEL_PATH)
        metadata=ModelMetadata(name=MODEL_TYPE,version=result["model_version"],training_records=result["training_records"],feature_names=result["feature_names"],model_file_path=result["model_file_path"],training_status="ready",metrics=result["metrics"])
        db.add(metadata); db.commit()
        return {**result,"is_trained":True,"is_loaded":True,"prediction_ready":True,"model_file_exists":MODEL_PATH.is_file()}
    except ValueError as exc:
        db.rollback()
        try: record_training_failure(db,str(exc))
        except Exception: db.rollback()
        raise HTTPException(422,str(exc))
    except Exception as exc:
        db.rollback()
        try: record_training_failure(db,str(exc))
        except Exception: db.rollback()
        raise HTTPException(500,"Isolation Forest training failed; the previous model artifact was preserved")
@app.get("/api/reports/{kind}")
def report(kind:str,format:str="csv",db:Session=Depends(get_db),_:User=Depends(require_admin)):
    if kind not in ("attendance","alerts"): raise HTTPException(404,"Report type must be attendance or alerts")
    out=io.StringIO(); writer=csv.writer(out)
    if kind=="attendance":
        writer.writerow(["id","student_id","student_code","student_name","date","checked_in_at","status","source"])
        for r in db.scalars(select(AttendanceRecord).order_by(AttendanceRecord.checked_in_at.desc())):
            s=db.get(Student,r.student_id); writer.writerow([r.id,r.student_id,s.student_code,s.full_name,r.attendance_date,r.checked_in_at,r.status,r.source])
    else:
        writer.writerow(["id","student_id","category","risk_score","severity","status","created_at","explanation"])
        for a in db.scalars(select(SuspiciousAlert).order_by(SuspiciousAlert.created_at.desc())): writer.writerow([a.id,a.student_id,a.category,a.risk_score,a.severity,a.status,a.created_at,a.explanation])
    if format.lower()=="pdf":
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
        pdf=io.BytesIO(); rows=list(csv.reader(io.StringIO(out.getvalue())))
        doc=SimpleDocTemplate(pdf,pagesize=landscape(letter),rightMargin=24,leftMargin=24,topMargin=24,bottomMargin=24)
        table=Table(rows,repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#12305a")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),0.25,colors.HexColor("#b8c5d6")),("FONTSIZE",(0,0),(-1,-1),7),("VALIGN",(0,0),(-1,-1),"TOP"),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#edf2f7")])]))
        doc.build([table]); pdf.seek(0)
        return StreamingResponse(pdf,media_type="application/pdf",headers={"Content-Disposition":f"attachment; filename={kind}-report.pdf"})
    if format.lower()!="csv": raise HTTPException(400,"Format must be csv or pdf")
    return StreamingResponse(iter([out.getvalue()]),media_type="text/csv",headers={"Content-Disposition":f"attachment; filename={kind}-report.csv"})
@app.websocket("/api/ws/alerts")
async def websocket_alerts(ws:WebSocket):
    token=ws.query_params.get("token","")
    try:
        payload=jwt.decode(token,settings.secret_key,algorithms=["HS256"])
        if payload.get("role")!="admin": raise JWTError("admin role required")
    except JWTError:
        await ws.close(code=1008); return
    await ws.accept(); clients.add(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: clients.discard(ws)

@app.post("/api/demo/seed")
def seed_demo(db:Session=Depends(get_db),_:User=Depends(require_admin)):
    total_students=db.scalar(select(func.count()).select_from(Student)) or 0
    total_records=db.scalar(select(func.count()).select_from(AttendanceRecord)) or 0
    total_alerts=db.scalar(select(func.count()).select_from(SuspiciousAlert)) or 0
    demo_students=list(db.scalars(select(Student).where(Student.email.like("student%@demo.college.edu"))))
    if len(demo_students)>=500:
        return {"students_created":0,"attendance_records_created":0,"alerts_created":0,"demo_students":len(demo_students),"total_students":total_students,"total_attendance_records":total_records,"total_alerts":total_alerts,"message":"Demo cohort already exists; no records were added"}

    students_by_code={student.student_code:student for student in demo_students}
    students_by_email={student.email:student for student in demo_students}
    all_students={}
    students_created=0
    attendance_created=[]
    records_created=0
    start=app_today()-timedelta(days=35)
    weekday_offsets=[offset for offset in range(35) if (start+timedelta(days=offset)).weekday()<5]
    device_offsets={weekday_offsets[5],weekday_offsets[12]}
    location_offset=weekday_offsets[-10]
    timing_offset=weekday_offsets[-8]
    coordinated_offset=weekday_offsets[-1]

    existing_student_ids={student.id for student in demo_students if student.id is not None}
    existing_attendance=set()
    if existing_student_ids:
        existing_attendance={(sid,day) for sid,day in db.execute(select(AttendanceRecord.student_id,AttendanceRecord.attendance_date).where(AttendanceRecord.student_id.in_(existing_student_ids))).all()}

    def synthetic_signature(student_number, day_offset):
        return blake2b(f"attendly-synthetic-v2:{student_number}:{day_offset}".encode(),digest_size=8).digest()

    for i in range(1,501):
        student_code=f"STU{i:04d}"
        email=f"student{i:04d}@demo.college.edu"
        by_code=students_by_code.get(student_code) or db.scalar(select(Student).where(Student.student_code==student_code))
        by_email=students_by_email.get(email) or db.scalar(select(Student).where(Student.email==email))
        if by_code and by_email and by_code.id!=by_email.id:
            raise HTTPException(409,f"Demo student identity collision for {student_code}")
        student=by_code or by_email
        if student and (student.student_code!=student_code or student.email!=email):
            raise HTTPException(409,f"Demo student identity collision for {student_code}")
        if not student:
            student=Student(student_code=student_code,full_name=f"Student {i:04d}",email=email,department=["Engineering","Science","Arts","Business"][i%4])
            db.add(student); db.flush(); students_created+=1
        all_students[i]=student
        students_by_code[student_code]=student
        students_by_email[email]=student

        for day_offset in range(35):
            d=start+timedelta(days=day_offset)
            signature=synthetic_signature(i,day_offset)
            coordinated=i in (4,5)
            paired_device=i in (100,101) and day_offset in device_offsets
            special=(i==101 and day_offset in device_offsets) or (i==2 and day_offset==location_offset) or (i==3 and day_offset==timing_offset) or (i==5 and day_offset==coordinated_offset)
            if d.weekday()>=5 or (not coordinated and not paired_device and not special and signature[0]%8==0):
                continue
            t=datetime.combine(d,datetime.min.time()).replace(hour=8+signature[1]%3,minute=signature[2]%60)
            truth=False
            device=f"device-{i}"
            lat=lon=None
            consent=False
            session_group=None

            if coordinated:
                t=t.replace(hour=9,minute=10)
                session_group="DEMO-COORDINATED-101"
            if i==101 and day_offset in device_offsets:
                peer_signature=synthetic_signature(100,day_offset)
                t=t.replace(hour=8+peer_signature[1]%3,minute=peer_signature[2]%60)
                device="device-100"
                truth=True
            if i==2 and day_offset==location_offset:
                t=t.replace(hour=2,minute=5)
                lat=settings.campus_latitude+0.09
                lon=settings.campus_longitude
                consent=True
                truth=True
            if i==3 and day_offset==timing_offset:
                t=t.replace(hour=2,minute=5)
                truth=True
            if i==5 and day_offset==coordinated_offset:
                lat=settings.campus_latitude+0.09
                lon=settings.campus_longitude
                consent=True
                truth=True

            if (student.id,d) in existing_attendance:
                continue
            record=AttendanceRecord(student_id=student.id,attendance_date=d,checked_in_at=t,device_identifier=device,latitude=lat,longitude=lon,location_consent=consent,source="synthetic",ground_truth=truth,session_group=session_group)
            db.add(record)
            attendance_created.append(record)
            existing_attendance.add((student.id,d))
            records_created+=1

    db.flush()
    alerts_before=db.scalar(select(func.count()).select_from(SuspiciousAlert)) or 0
    for record in attendance_created:
        if record.ground_truth:
            generate_alert(db,record)
    db.commit()

    total_students=db.scalar(select(func.count()).select_from(Student)) or 0
    total_records=db.scalar(select(func.count()).select_from(AttendanceRecord)) or 0
    total_alerts=db.scalar(select(func.count()).select_from(SuspiciousAlert)) or 0
    alerts_created=total_alerts-alerts_before
    return {"students_created":students_created,"attendance_records_created":records_created,"alerts_created":alerts_created,"demo_students":len(all_students),"total_students":total_students,"total_attendance_records":total_records,"total_alerts":total_alerts,"message":"Generated reproducible demo students, attendance, and detector results"}

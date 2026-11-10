from datetime import datetime, date
from sqlalchemy import String, Integer, Float, Boolean, DateTime, Date, ForeignKey, Text, JSON, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base
from .config import utc_now_naive

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="student")
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

class Student(Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    department: Mapped[str] = mapped_column(String(120), default="General")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    identifier: Mapped[str] = mapped_column(String(128), index=True)
    label: Mapped[str] = mapped_column(String(80), default="Registered device")
    consented: Mapped[bool] = mapped_column(Boolean, default=True)

class AttendanceSession(Base):
    __tablename__ = "attendance_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    course_code: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    __table_args__ = (UniqueConstraint("student_id", "attendance_date", name="uq_student_attendance_day"), Index("ix_attendance_device_time", "device_identifier", "checked_in_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    attendance_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    checked_in_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, index=True)
    device_identifier: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20), default="qr")
    status: Mapped[str] = mapped_column(String(20), default="present")
    ground_truth: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    session_group: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    model_anomaly_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_prediction: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(40), nullable=True)

class StudentLocationHistory(Base):
    __tablename__ = "student_location_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

class AnomalyEvent(Base):
    __tablename__ = "anomaly_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    attendance_id: Mapped[int] = mapped_column(ForeignKey("attendance_records.id"), index=True)
    category: Mapped[str] = mapped_column(String(50))
    points: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

class SuspiciousAlert(Base):
    __tablename__ = "suspicious_alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    attendance_id: Mapped[int] = mapped_column(ForeignKey("attendance_records.id"), unique=True, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    category: Mapped[str] = mapped_column(String(80))
    risk_score: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(20))
    explanation: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, index=True)

class AlertEvidence(Base):
    __tablename__ = "alert_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("suspicious_alerts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    details: Mapped[dict] = mapped_column(JSON, default=dict)

class InvestigationNote(Base):
    __tablename__ = "investigation_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("suspicious_alerts.id"), index=True)
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

class AdminAction(Base):
    __tablename__ = "admin_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("suspicious_alerts.id"), index=True)
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    previous_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

class ModelMetadata(Base):
    __tablename__ = "model_metadata"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(40))
    training_records: Mapped[int] = mapped_column(Integer, default=0)
    feature_names: Mapped[list] = mapped_column(JSON, default=list)
    model_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    training_status: Mapped[str] = mapped_column(String(30), default="ready")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    trained_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

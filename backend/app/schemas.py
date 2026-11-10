from datetime import datetime
from pydantic import BaseModel, EmailStr, Field

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
class RegisterRequest(LoginRequest):
    role: str = "student"
    student_code: str | None = None
    full_name: str | None = None
class StudentCreate(BaseModel):
    student_code: str = Field(min_length=2, max_length=40)
    full_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    department: str = "General"
class AttendanceCreate(BaseModel):
    student_id: int
    device_identifier: str | None = Field(default=None, max_length=128)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    location_consent: bool = False
    session_code: str | None = None
    source: str = "qr"
    checked_in_at: datetime | None = None
class ActionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=4000)

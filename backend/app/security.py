from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from .config import settings
from .database import get_db
from .models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False, description="Paste the access_token returned by POST /api/auth/login")
def hash_password(value: str) -> str: return pwd_context.hash(value)
def check_password(value: str, encoded: str) -> bool: return pwd_context.verify(value, encoded)
def issue_token(user: User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": exp}, settings.secret_key, algorithm="HS256")
def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme), db: Session = Depends(get_db)) -> User:
    if credentials is None:
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    try: user_id = int(jwt.decode(credentials.credentials, settings.secret_key, algorithms=["HS256"])["sub"])
    except (JWTError, KeyError, ValueError): raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    user = db.get(User, user_id)
    if not user: raise HTTPException(401, "User not found")
    return user
def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin": raise HTTPException(403, "Administrator access required")
    return user

import argparse
from getpass import getpass
from pathlib import Path
import sys
from sqlalchemy import select

PROJECT_ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT_ROOT/"backend"))
from app.database import Base, engine, SessionLocal
from app.models import User
from app.security import hash_password

parser=argparse.ArgumentParser(description="Create an administrator login")
parser.add_argument("email")
args=parser.parse_args()
password=getpass("Admin password (8+ characters): ")
if len(password)<8: raise SystemExit("Password must be at least 8 characters")
Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    if db.scalar(select(User).where(User.email==args.email)): raise SystemExit("An account with that email already exists")
    db.add(User(email=args.email,password_hash=hash_password(password),role="admin")); db.commit()
print("Administrator account created.")

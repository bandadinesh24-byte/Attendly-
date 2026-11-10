"""Reset an existing administrator password without creating or replacing users."""
import argparse
from getpass import getpass
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from sqlalchemy import func, select
from app.database import SessionLocal
from app.models import User
from app.security import hash_password

MIN_PASSWORD_LENGTH = 8
MAX_BCRYPT_BYTES = 72

def main() -> int:
    parser = argparse.ArgumentParser(description="Reset the password for an existing administrator")
    parser.add_argument("email", help="Email address of the existing administrator account")
    args = parser.parse_args()

    password = getpass("New administrator password: ")
    confirmation = getpass("Confirm new password: ")
    if password != confirmation:
        parser.error("Passwords do not match")
    if len(password) < MIN_PASSWORD_LENGTH:
        parser.error(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password.encode("utf-8")) > MAX_BCRYPT_BYTES:
        parser.error(f"Password must be at most {MAX_BCRYPT_BYTES} UTF-8 bytes for bcrypt")

    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(func.lower(User.email) == args.email.strip().lower()))
        if user is None or user.role != "admin":
            parser.error("No administrator account exists with that email")
        user.password_hash = hash_password(password)

    print("Administrator password updated successfully.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

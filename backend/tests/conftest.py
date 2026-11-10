import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.main import app
from app.models import User, Student
from app.security import hash_password

@pytest.fixture
def client():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession=sessionmaker(bind=engine,autoflush=False,autocommit=False)
    def override_db():
        db=TestSession()
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db]=override_db
    with TestSession() as db:
        first=Student(student_code="STU1001",full_name="Avery Student",email="avery@example.edu")
        second=Student(student_code="STU1002",full_name="Riley Student",email="riley@example.edu")
        db.add_all([first,second]); db.flush()
        db.add(User(email="admin@example.edu",password_hash=hash_password("test-password-123"),role="admin"))
        db.add(User(email="avery@example.edu",password_hash=hash_password("student-password-123"),role="student",student_id=first.id))
        db.commit()
    with TestClient(app) as test_client:
        test_client.test_student_id=1
        test_client.test_session_factory=TestSession
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()

@pytest.fixture
def anomaly_db():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession=sessionmaker(bind=engine,autoflush=False,autocommit=False)
    db=TestSession()
    yield db
    db.close(); Base.metadata.drop_all(engine); engine.dispose()

@pytest.fixture
def admin_headers(client):
    response=client.post("/api/auth/login",json={"email":"admin@example.edu","password":"test-password-123"})
    assert response.status_code==200
    return {"Authorization":"Bearer "+response.json()["access_token"]}

from datetime import datetime
from app.detection import analyze_attendance
from app.models import AttendanceRecord

class EmptyDB:
    def scalars(self, query): return iter([])
    def execute(self, query): return []
    def scalar(self, query): return 0

def test_no_evidence_yields_zero_score():
    record=AttendanceRecord(id=1,student_id=1,attendance_date=datetime.utcnow().date(),checked_in_at=datetime.utcnow(),device_identifier=None,latitude=None,longitude=None,location_consent=False)
    signals,evidence,score,breakdown=analyze_attendance(EmptyDB(),record)
    assert signals==[] and evidence=={} and score==0
    assert sum(breakdown.values())==0

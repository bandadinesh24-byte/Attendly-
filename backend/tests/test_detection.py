from app.detection import haversine_km, severity

def test_haversine_zero_distance():
    assert haversine_km(0,0,0,0)==0
def test_haversine_known_distance():
    assert 110 < haversine_km(0,0,0,1) < 112
def test_risk_severity_boundaries():
    assert severity(29)=="low"
    assert severity(30)=="medium"
    assert severity(50)=="high"
    assert severity(75)=="critical"

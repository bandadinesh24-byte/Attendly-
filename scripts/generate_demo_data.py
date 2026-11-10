"""Generate the reproducible 500-student synthetic cohort through the API."""
import os, requests
base=os.getenv("API_URL","http://localhost:8000")
email=os.environ.get("ADMIN_EMAIL")
password=os.environ.get("ADMIN_PASSWORD")
if not email or not password: raise SystemExit("Set ADMIN_EMAIL and ADMIN_PASSWORD first")
response=requests.post(base+"/api/auth/login",json={"email":email,"password":password}); response.raise_for_status()
token=response.json()["access_token"]
result=requests.post(base+"/api/demo/seed",headers={"Authorization":"Bearer "+token}); result.raise_for_status()
print(result.json())

import os
os.environ.setdefault("GRID_TOKEN", "test-token-for-local-tests")
from fastapi.testclient import TestClient
import coordinator

client = TestClient(coordinator.app)
HEADERS = {"Authorization": "Bearer test-token-for-local-tests"}

def test_auth():
    assert client.get("/nodes").status_code == 401

def test_job_roundtrip():
    node = {"node_id": "test-node", "hostname": "test", "cpu_count": 4,
            "ram_total_gb": 16, "ram_available_gb": 8, "cpu_percent": 10}
    assert client.post("/heartbeat", json=node, headers=HEADERS).status_code == 200
    job = client.post("/jobs", json={"start": 1, "end": 100000}, headers=HEADERS).json()
    claimed = client.post("/workers/test-node/claim", headers=HEADERS).json()["job"]
    assert claimed["id"] == job["id"]
    assert client.post("/jobs/" + job["id"] + "/result", json={
        "node_id": "test-node", "lease_id": claimed["lease_id"], "result": 333328333350000
    }, headers=HEADERS).status_code == 200
    assert client.get("/jobs/" + job["id"], headers=HEADERS).json()["result"] == 333328333350000

def test_invalid_range():
    assert client.post("/jobs", json={"start": 0, "end": 2000000}, headers=HEADERS).status_code == 400

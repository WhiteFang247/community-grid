"""Opt-in worker. Executes only allowlisted deterministic tasks."""
import os, socket, time, uuid, platform
import httpx, psutil

COORDINATOR = os.getenv("GRID_COORDINATOR", "http://127.0.0.1:8765").rstrip("/")
TOKEN = os.environ.get("GRID_TOKEN")
if not TOKEN:
    raise RuntimeError("Set GRID_TOKEN")
NODE_ID = os.getenv("GRID_NODE_ID", str(uuid.uuid5(uuid.NAMESPACE_DNS, socket.gethostname())))
ENABLED = os.getenv("GRID_CONTRIBUTE", "true").lower() == "true"
HEADERS = {"Authorization": "Bearer " + TOKEN}

def compute(job):
    if job["kind"] != "sum_squares":
        raise ValueError("Unsupported task")
    start, end = job["start"], job["end"]
    if not (0 <= start <= end <= 10000000 and end - start <= 1000000):
        raise ValueError("Invalid task bounds")
    return sum(n*n for n in range(start, end))

def tick(client):
    ram = psutil.virtual_memory()
    heartbeat = {"node_id": NODE_ID, "hostname": socket.gethostname(),
        "cpu_count": psutil.cpu_count(logical=True) or 1,
        "ram_total_gb": round(ram.total/1024**3, 2),
        "ram_available_gb": round(ram.available/1024**3, 2),
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "platform": platform.system(), "contribution_enabled": ENABLED}
    client.post("/heartbeat", json=heartbeat).raise_for_status()
    if not ENABLED:
        return
    response = client.post("/workers/" + NODE_ID + "/claim")
    response.raise_for_status()
    job = response.json().get("job")
    if job:
        answer = compute(job)
        client.post("/jobs/" + job["id"] + "/result", json={
            "node_id": NODE_ID, "lease_id": job["lease_id"], "result": answer
        }).raise_for_status()
        print("Completed", job["id"], answer, flush=True)

if __name__ == "__main__":
    with httpx.Client(base_url=COORDINATOR, headers=HEADERS, timeout=10) as client:
        while True:
            try:
                tick(client)
            except (httpx.HTTPError, ValueError) as exc:
                print("Worker error:", exc, flush=True)
            time.sleep(5)

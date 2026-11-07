"""Community Grid coordinator v0.2 — local-first, allowlisted compute jobs."""
import os, time, uuid, secrets
from threading import RLock
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel, Field

app = FastAPI(title="Community Grid", version="0.2.0")
lock = RLock()
nodes, jobs, pairings = {}, {}, {}
TOKEN = os.environ.get("GRID_TOKEN")
if not TOKEN:
    raise RuntimeError("Set GRID_TOKEN to a strong random value before starting the coordinator")
ONLINE_SECONDS, LEASE_SECONDS = 15, 60

def auth(authorization: str | None = Header(default=None)):
    if not authorization or not secrets.compare_digest(authorization, "Bearer " + TOKEN):
        raise HTTPException(401, "Invalid grid token")

class Heartbeat(BaseModel):
    node_id: str = Field(min_length=1, max_length=128)
    hostname: str = Field(min_length=1, max_length=128)
    cpu_count: int = Field(ge=1)
    ram_total_gb: float = Field(gt=0)
    cpu_percent: float = Field(ge=0, le=100)
    ram_available_gb: float = Field(ge=0)
    platform: str = "unknown"
    gpu: str | None = None
    contribution_enabled: bool = True

class JobRequest(BaseModel):
    kind: str = "sum_squares"
    start: int = Field(ge=0, le=10000000)
    end: int = Field(ge=0, le=10000000)

class Result(BaseModel):
    node_id: str
    lease_id: str
    result: int

@app.get("/health")
def health():
    return {"ok": True, "version": "0.2.0"}

@app.post("/heartbeat", dependencies=[Depends(auth)])
def heartbeat(payload: Heartbeat):
    with lock:
        nodes[payload.node_id] = {**payload.model_dump(), "last_seen": time.time()}
    return {"accepted": True}

@app.get("/nodes", dependencies=[Depends(auth)])
def list_nodes():
    with lock:
        now = time.time()
        return {"nodes": [{**n, "online": now - n["last_seen"] < ONLINE_SECONDS} for n in nodes.values()]}

@app.post("/jobs", dependencies=[Depends(auth)])
def create_job(payload: JobRequest):
    if payload.kind != "sum_squares" or payload.end < payload.start or payload.end - payload.start > 1000000:
        raise HTTPException(400, "Unsupported job or invalid range")
    job = {"id": str(uuid.uuid4()), **payload.model_dump(), "status": "queued",
           "node_id": None, "lease_id": None, "leased_at": None, "result": None}
    with lock:
        jobs[job["id"]] = job
    return job

@app.get("/jobs", dependencies=[Depends(auth)])
def list_jobs():
    with lock:
        return {"jobs": list(jobs.values())}

@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
def get_job(job_id: str):
    with lock:
        if job_id not in jobs:
            raise HTTPException(404, "Unknown job")
        return jobs[job_id]

@app.post("/workers/{node_id}/claim", dependencies=[Depends(auth)])
def claim(node_id: str):
    with lock:
        now = time.time()
        node = nodes.get(node_id)
        if not node or now - node["last_seen"] > ONLINE_SECONDS:
            raise HTTPException(403, "Worker offline")
        if not node["contribution_enabled"] or node["cpu_percent"] > 80 or node["ram_available_gb"] < 0.5:
            return {"job": None, "reason": "resources_busy"}
        for job in jobs.values():
            if job["status"] == "queued" or (job["status"] == "running" and now - job["leased_at"] > LEASE_SECONDS):
                job.update(status="running", node_id=node_id, lease_id=secrets.token_hex(16), leased_at=now)
                return {"job": dict(job)}
    return {"job": None}

@app.post("/jobs/{job_id}/result", dependencies=[Depends(auth)])
def submit_result(job_id: str, payload: Result):
    with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        if job["status"] != "running" or job["node_id"] != payload.node_id or not secrets.compare_digest(job["lease_id"], payload.lease_id):
            raise HTTPException(409, "Invalid or expired job lease")
        if time.time() - job["leased_at"] > LEASE_SECONDS:
            raise HTTPException(409, "Job lease expired")
        job.update(status="completed", result=payload.result, completed_at=time.time())
    return {"accepted": True}

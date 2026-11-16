"""Bounded scheduling for explicitly enrolled, authenticated workers."""
from dataclasses import dataclass
from datetime import datetime
import argparse
import json
import http.client
import os
import ipaddress
import re
import shlex
from pathlib import Path
import subprocess
import time
import uuid

from .grid_relay import request
from .inference import BACKEND_REVISION, MODEL_SHA256
from .jobs import PROMPT_LIMIT

TERMINAL = {"completed", "failed", "cancelled", "timed-out"}
MIN_MEMORY = 2 * 1024**3


@dataclass
class Peer:
    request: object
    heartbeat: float = float("-inf")
    capability: dict | None = None
    job: str | None = None


class Coordinator:
    """Single owner with an atomic snapshot before every dispatch."""

    def __init__(self, state, clock=time.monotonic, offline_after=12):
        self.path = Path(state)
        self.clock = clock
        self.offline_after = offline_after
        self.peers = {}
        self.jobs = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="ascii") as stream:
            json.dump(self.jobs, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)

    def enroll(self, name, transport, *, approved=False):
        if not approved:
            raise PermissionError("Explicit worker approval required")
        if name in self.peers:
            raise ValueError("Worker already enrolled")
        assigned = [job["id"] for job in self.jobs.values()
                    if job.get("worker") == name and job["status"] not in TERMINAL]
        if len(assigned) > 1:
            raise ValueError("Invalid persisted worker assignments")
        peer = Peer(transport, job=assigned[0] if assigned else None)
        self._heartbeat(peer)
        self.peers[name] = peer

    def _heartbeat(self, peer):
        code, report = peer.request("GET", "/api/worker")
        if code != 200 or "model_sha256" not in report:
            raise PermissionError("Worker heartbeat was not authenticated")
        peer.capability = report
        peer.heartbeat = self.clock()

    def available(self, name):
        peer = self.peers[name]
        data = peer.capability or {}
        return (
            self.clock() - peer.heartbeat < self.offline_after and peer.job is None
            and data.get("busy") is False
            and data.get("healthy") is True and data.get("model_sha256") == MODEL_SHA256
            and data.get("backend_revision") == BACKEND_REVISION
            and (data.get("memory_available_bytes") or 0) >= MIN_MEMORY
            and data.get("logical_cpus", 0) >= data.get("limits", {}).get("threads", 1)
            and data.get("limits", {}).get("max_running") == 1
        )

    def submit(self, prompt, *, approved=False):
        if not approved:
            raise PermissionError("Operator approval required for submission")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > PROMPT_LIMIT:
            raise ValueError(f"Provide 1–{PROMPT_LIMIT} UTF-8 bytes of prompt text")
        job_id = str(uuid.uuid4())
        self.jobs[job_id] = {"id": job_id, "prompt": prompt, "status": "queued",
                             "submitted_ns": time.time_ns()}
        self.save()
        return job_id

    def cancel(self, job_id, *, approved=False):
        if not approved:
            raise PermissionError("Operator approval required for cancellation")
        job = self.jobs[job_id]
        if job["status"] not in TERMINAL:
            if job.get("worker"):
                job["cancel_requested"] = True
            else:
                job.update(status="cancelled", finished_ns=time.time_ns())
            self.save()

    def _reconcile(self, peer, job):
        path = "/api/jobs/" + job["id"]
        code, row = peer.request("GET", path)
        if code == 404:
            if job.get("cancel_requested") or job.get("accepted"):
                job.update(status="cancelled" if job.get("cancel_requested") else "failed",
                           finished_ns=time.time_ns(), result={"error": "Worker job absent"})
                peer.job = None
                return
            started = time.monotonic_ns()
            code, response = peer.request("POST", "/api/jobs", {"id": job["id"], "prompt": job["prompt"]})
            if code == 202 and response["id"] == job["id"]:
                job.update(accepted=True, dispatch_ms=(time.monotonic_ns() - started) / 1e6)
            elif code not in (429, 503):
                job.update(status="failed", finished_ns=time.time_ns(),
                           result={"error": f"Worker rejected dispatch (HTTP {code})"})
                peer.job = None
            return
        if code != 200 or row["id"] != job["id"]:
            raise ValueError("Unexpected worker job response")
        job.update(status=row["status"], accepted=True, worker_record=row)
        if row.get("started_at"):
            stamp = datetime.fromisoformat
            job["worker_queue_ms"] = (stamp(row["started_at"]) - stamp(row["created_at"])).total_seconds() * 1000
            if row.get("finished_at"):
                job["worker_execution_ms"] = (stamp(row["finished_at"]) - stamp(row["started_at"])).total_seconds() * 1000
        if row["status"] in TERMINAL:
            job.update(result=row["result"], finished_ns=time.time_ns())
            job["end_to_end_ms"] = (job["finished_ns"] - job["submitted_ns"]) / 1e6
            peer.job = None
        elif job.get("cancel_requested"):
            peer.request("POST", path + "/cancel")

    def tick(self):
        for name, peer in self.peers.items():
            try:
                self._heartbeat(peer)
                if peer.job:
                    self._reconcile(peer, self.jobs[peer.job])
                    self.save()
                if not self.available(name):
                    continue
                job = next((item for item in self.jobs.values()
                            if item["status"] == "queued" and "worker" not in item), None)
                if job:
                    peer.job = job["id"]
                    job.update(worker=name, assigned_ns=time.time_ns())
                    self.save()
                    self._reconcile(peer, job)
                    self.save()
            except (OSError, ValueError, KeyError, http.client.HTTPException, subprocess.SubprocessError) as exc:
                peer.heartbeat = float("-inf")
                if peer.job:
                    self.jobs[peer.job]["last_transport_error"] = type(exc).__name__
                    self.save()


def local_transport(port, token):
    return lambda method, path, body=None: request(port, token, method, path, body)


def ssh_transport(address, user, identity_file, known_hosts, staging=None, port=None,
                  *, forced_command=False, ssh_port=22, bind_address=None):
    """Build a fixed noninteractive SSH command from operator configuration."""
    address = str(ipaddress.IPv4Address(address))
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise ValueError("Invalid worker account")
    if not isinstance(ssh_port, int) or not 1 <= ssh_port <= 65535:
        raise ValueError("Invalid SSH port")
    if bind_address is not None:
        bind_address = str(ipaddress.IPv4Address(bind_address))
    if not forced_command:
        if not isinstance(staging, str) or not re.fullmatch(r"/dev/shm/[a-zA-Z0-9_.-]+", staging):
            raise ValueError("Invalid live RAM directory")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Invalid worker loopback port")
    elif staging is not None or port is not None:
        raise ValueError("Forced-command transport cannot select a remote program or port")
    pin = Path(known_hosts).expanduser().resolve(strict=True).as_posix()
    identity = str(Path(identity_file).expanduser().resolve(strict=True))
    null = os.devnull
    argv = ["ssh", "-F", null, "-T", "-p", str(ssh_port), "-i", identity,
            *(["-b", bind_address] if bind_address else []), "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no",
            "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=4",
            "-o", f'UserKnownHostsFile="{pin}"', "-o", f"GlobalKnownHostsFile={null}",
            f"{user}@{address}"]
    remote_command = None if forced_command else (
        f"PYTHONPATH={shlex.quote(staging + '/runtime')} python3 -B -m community_grid.grid_relay "
        f"{port} {shlex.quote(staging + '/operator-token')}"
    )
    def send(method, path, body=None):
        return json.loads(subprocess.run(
            [*argv, *([remote_command] if remote_command else [])],
            input=json.dumps({"method": method, "path": path, "body": body}),
            text=True, capture_output=True, timeout=10, check=True,
        ).stdout)
    return send


def run_batch(coordinator, seconds):
    started = time.monotonic()
    deadline = started + seconds
    while time.monotonic() < deadline:
        coordinator.tick()
        if all(job["status"] in TERMINAL for job in coordinator.jobs.values()):
            break
        time.sleep(0.25)
    elapsed = time.monotonic() - started
    jobs = list(coordinator.jobs.values())
    completed = [job for job in jobs if job["status"] == "completed"]
    tokens = sum(sample["predicted_n"] for job in completed
                 for sample in job["result"]["samples"])
    return {"elapsed_seconds": elapsed, "completed": len(completed), "total": len(jobs),
            "output_tokens": tokens, "aggregate_tokens_per_second": tokens / elapsed,
            "jobs": jobs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--approve-workers", action="store_true", required=True)
    parser.add_argument("--submit", type=Path, help="JSON array of prompts approved for execution")
    parser.add_argument("--cancel", help="Approve cancellation of the specified job UUID")
    args = parser.parse_args()
    coordinator = Coordinator(args.state)
    config = json.loads(args.config.read_text())
    for name, peer in config.items():
        if "address" in peer:
            transport = ssh_transport(**peer)
        else:
            transport = local_transport(peer["port"], Path(peer["token_file"]).read_text().strip())
        coordinator.enroll(name, transport, approved=True)
    if args.submit:
        prompts = json.loads(args.submit.read_text())
        if not isinstance(prompts, list) or not 1 <= len(prompts) <= 32:
            parser.error("Provide 1–32 prompts")
        for prompt in prompts:
            coordinator.submit(prompt, approved=True)
    if args.cancel:
        coordinator.cancel(args.cancel, approved=True)
    print(json.dumps(run_batch(coordinator, 180), indent=2))


if __name__ == "__main__":
    main()

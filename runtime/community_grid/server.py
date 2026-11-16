"""Local-only dashboard with an optional operator-authorized inference API."""
from __future__ import annotations
import json
import hmac
import os
import re
import signal
import sys
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .hardware import _cpu_model, _mem_available_bytes, _mem_total_bytes, discover
from .inference import BACKEND_REVISION, MODEL_SHA256
from .models import recommend
from .jobs import JobQueue
from .operator_auth import read_private_token

PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Community Grid OS</title><style>
:root{color-scheme:dark;font-family:system-ui,Arial;background:#0b1220;color:#e2e8f0}
body{margin:0;max-width:950px;padding:32px 24px}header{display:flex;align-items:center;gap:12px}
.logo{background:#0d9488;border-radius:12px;padding:12px;font-size:24px}h1{font-size:26px;margin:0}
small{color:#94a3b8}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin-top:28px}
article{background:#172235;border:1px solid #334155;border-radius:14px;padding:20px}
h2{font-size:17px;margin:0 0 14px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;color:#cbd5e1}
button{background:#0d9488;border:0;border-radius:8px;padding:10px 14px;color:white;cursor:pointer}
footer{margin-top:25px;color:#94a3b8;font-size:12px}</style>
<header><span class="logo">◈</span><div><h1>Community Grid OS</h1><small>Local-first AI runtime · Preview</small></div></header>
<div class="grid"><article><h2>Hardware</h2><pre id="hardware">Loading...</pre></article>
<article><h2>Local model recommendation</h2><pre id="model">Loading...</pre></article>
<article><h2>Cluster status</h2><p>Standalone mode</p><small>Secure peer enrollment not implemented yet.</small></article>
<article><h2>Local worker</h2><pre id="worker">Loading...</pre></article>
<article><h2>Jobs</h2><label>Operator token <input id="token" type="password" autocomplete="off"></label>
<pre id="jobs">Loading...</pre></article></div>
<footer>Dashboard listens on localhost only. No telemetry or remote access.</footer>
<script>
async function load(id,url,options){try{const r=await fetch(url,options);if(!r.ok)throw Error(r.status);document.getElementById(id).textContent=JSON.stringify(await r.json(),null,2)}
catch(e){document.getElementById(id).textContent='Unavailable: '+e.message}}
load('hardware','/api/hardware');load('model','/api/models/recommend');
function jobs(){const token=document.getElementById('token').value;
load('worker','/api/worker');load('jobs','/api/jobs',token?{headers:{Authorization:'Bearer '+token}}:undefined)}
document.getElementById('token').addEventListener('change',jobs);
jobs();setInterval(jobs,3000);
</script></html>"""

def handle_job(kind, payload):
    if kind == "hardware.discover":
        return discover()
    if kind == "model.recommend":
        return recommend(discover())
    if kind == "system.health":
        return {"status": "ok", "mode": "standalone"}
    raise ValueError("unsupported job kind")

def make_handler(queue: JobQueue, worker=None, token=None, limits=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                return self.reply(200, PAGE.encode(), "text/html; charset=utf-8")
            if path == "/api/health":
                result = {"status": "ok", "mode": "standalone", "version": "0.1.0"}
            elif path == "/api/hardware":
                result = discover()
            elif path == "/api/models/recommend":
                result = recommend(discover())
            elif path == "/api/jobs":
                result = queue.list_jobs()
                if not self.authorized():
                    result = [
                        {key: value for key, value in job.items()
                         if job["kind"] != "inference" or key not in ("payload", "result")}
                        for job in result
                    ]
            elif path == "/api/worker":
                result = {"enabled": worker is not None}
                if worker:
                    if sys.platform == "win32":
                        from .windows_job import memory_status
                        total, available = memory_status()
                    else:
                        total, available = _mem_total_bytes(), _mem_available_bytes()
                    result.update({"healthy": worker.thread.is_alive() and not worker.stop.is_set(),
                                   "limits": limits, "memory_available_bytes": available})
                    if self.authorized():
                        result.update(cpu=_cpu_model(), logical_cpus=os.cpu_count(),
                                      memory_total_bytes=total, model_sha256=MODEL_SHA256,
                                      backend_revision=BACKEND_REVISION, supported_backends=["llama.cpp CPU"],
                                      busy=queue.inference_busy())
            elif re.fullmatch(r"/api/jobs/[a-f0-9-]{36}", path):
                if not self.authorized():
                    return self.reply(401, b'{"error":"operator authorization required"}')
                result = queue.get(path.rsplit("/", 1)[1])
                if result is None:
                    return self.reply(404, b'{"error":"job not found"}')
            else:
                return self.reply(404, b'{"error":"not found"}')
            return self.reply(200, json.dumps(result).encode())

        def reply(self, code, data, content_type="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if worker is None:
                return self.reply(405, b'{"error":"read-only API"}')
            if not self.authorized():
                return self.reply(401, b'{"error":"operator authorization required"}')
            path = urlsplit(self.path).path
            cancellation = re.fullmatch(r"/api/jobs/([a-f0-9-]{36})/cancel", path)
            if cancellation:
                if queue.cancel_inference(cancellation[1]):
                    return self.reply(200, b'{"status":"cancelled"}')
                return self.reply(409, b'{"error":"job is absent or no longer cancellable"}')
            if path != "/api/jobs":
                return self.reply(404, b'{"error":"not found"}')
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096 or self.headers.get_content_type() != "application/json":
                    raise ValueError
                self.connection.settimeout(5)
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict) or set(data) not in ({"prompt"}, {"prompt", "id"}):
                    raise ValueError
                if "id" in data and not isinstance(data["id"], str):
                    raise ValueError
                job_id = queue.submit_inference(data["prompt"], data.get("id"))
            except (ValueError, TimeoutError, RecursionError):
                return self.reply(400, b'{"error":"expected a JSON prompt of at most 512 UTF-8 bytes"}')
            except OverflowError:
                return self.reply(429, b'{"error":"inference queue full"}')
            return self.reply(202, json.dumps({"id": job_id, "status": queue.get(job_id)["status"]}).encode())

        def authorized(self):
            header = self.headers.get("Authorization", "").encode()
            return bool(token and hmac.compare_digest(header, b"Bearer " + token))
    return Handler



def main():
    # Never bind beyond loopback without authenticated transport and authorization.
    port = int(os.environ.get("COMMUNITY_GRID_PORT", "8765"))
    state = os.environ.get("COMMUNITY_GRID_STATE", str(Path.home() / ".local/share/community-grid/jobs.sqlite3"))
    queue = JobQueue(state)
    worker, token, limits, owner = None, None, None, None
    try:
        if os.environ.get("COMMUNITY_GRID_ENABLE_WORKER") == "1":
            from .worker import LocalLlama, Worker

            token_file = os.environ.pop("COMMUNITY_GRID_OPERATOR_TOKEN_FILE", None)
            if token_file and "COMMUNITY_GRID_OPERATOR_TOKEN" in os.environ:
                raise ValueError("Configure exactly one operator token source")
            token = (read_private_token(token_file) if token_file else
                     os.environ.pop("COMMUNITY_GRID_OPERATOR_TOKEN")).encode()
            if len(token) < 32:
                raise ValueError("Set a random operator token of at least 32 bytes")
            threads = int(os.environ.get("COMMUNITY_GRID_THREADS", "2"))
            memory = int(os.environ.get("COMMUNITY_GRID_MEMORY_MIB", "4096"))
            timeout = int(os.environ.get("COMMUNITY_GRID_TIMEOUT", "90"))
            if threads not in (1, 2, 4) or not 2048 <= memory <= 6144 or not 5 <= timeout <= 300:
                raise ValueError("Worker limits out of range")
            owner = open(str(Path(state).resolve()) + ".worker.lock", "a")
            if sys.platform == "win32":
                import msvcrt
                owner.seek(0)
                msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            execute = LocalLlama(os.environ["COMMUNITY_GRID_BACKEND"], os.environ["COMMUNITY_GRID_MODEL"],
                                 os.environ["COMMUNITY_GRID_BACKEND_SHA256"], threads, memory, owner.fileno())
            worker = Worker(queue, execute, timeout)
            limits = {"threads": threads, "address_space_mib": memory, "timeout_seconds": timeout,
                      "max_running": 1, "max_queued": 32}
        with ThreadingHTTPServer(("127.0.0.1", port), make_handler(queue, worker, token, limits)) as server:
            if worker:
                worker.start()
            signal.signal(signal.SIGTERM, lambda *_: Thread(target=server.shutdown, daemon=True).start())
            print(f"Community Grid dashboard: http://127.0.0.1:{port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        if worker and worker.thread.ident is not None:
            worker.close()
        if owner:
            owner.close()

if __name__ == "__main__":
    main()

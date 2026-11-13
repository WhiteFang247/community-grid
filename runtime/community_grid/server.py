"""Local-only Community Grid dashboard and read-only API."""
from __future__ import annotations
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .hardware import discover
from .models import recommend
from .jobs import JobQueue

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
<article><h2>Assistant</h2><p>Coming next: permission-controlled local AI chat.</p>
<small>No AI model or privileged command executor is installed by this preview.</small></article></div>
<footer>Dashboard listens on localhost only. No telemetry or remote access.</footer>
<script>
async function load(id,url){try{const r=await fetch(url);if(!r.ok)throw Error(r.status);document.getElementById(id).textContent=JSON.stringify(await r.json(),null,2)}
catch(e){document.getElementById(id).textContent='Unavailable: '+e.message}}
load('hardware','/api/hardware');load('model','/api/models/recommend');
</script></html>"""

def handle_job(kind, payload):
    if kind == "hardware.discover":
        return discover()
    if kind == "model.recommend":
        return recommend(discover())
    if kind == "system.health":
        return {"status": "ok", "mode": "standalone"}
    raise ValueError("unsupported job kind")

def make_handler(queue: JobQueue):
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
            self.reply(405, b'{"error":"read-only API"}')
    return Handler

def main():
    # Never bind beyond loopback without authenticated transport and authorization.
    port = int(os.environ.get("COMMUNITY_GRID_PORT", "8765"))
    state = os.environ.get("COMMUNITY_GRID_STATE", str(Path.home() / ".local/share/community-grid/jobs.sqlite3"))
    queue = JobQueue(state)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(queue))
    print(f"Community Grid dashboard: http://127.0.0.1:{port}", flush=True)
    server.serve_forever()

if __name__ == "__main__":
    main()

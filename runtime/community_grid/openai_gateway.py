"""Small OpenAI-compatible, loopback-only adapter for one pinned SSH inference worker."""

import argparse
from collections import deque
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
from threading import Event, Lock, Thread
import time
import uuid

from .coordinator import Coordinator, TERMINAL, ssh_transport
from .inference import BACKEND_REVISION, MODEL_SHA256, TOKEN_LIMIT
from .jobs import PROMPT_LIMIT


MODEL_ID = "community-grid-qwen2.5-1.5b-instruct-q4_k_m"
MAX_BODY = 16384
MAX_PROMPT = PROMPT_LIMIT
MAX_MESSAGES = 8
SPECIAL_TOKENS = ("<|im_start|>", "<|im_end|>", "<|endoftext|>")


class GatewayError(Exception):
    def __init__(self, status, message, code, param=None, kind="invalid_request_error", job_id=None):
        super().__init__(message)
        self.status, self.code, self.param, self.kind, self.job_id = status, code, param, kind, job_id


def transport_error(exc, job_id=None):
    """Report a safe failure class without weakening OpenSSH verification."""
    stderr = getattr(exc, "stderr", "") or ""
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    detail = str(stderr).lower()
    if "host key verification failed" in detail or "remote host identification has changed" in detail:
        code, message = "host_key_verification_failed", "Dell SSH host key verification failed"
    elif "permission denied (publickey)" in detail or "too many authentication failures" in detail:
        code, message = "ssh_authentication_failed", "Dell SSH authentication failed"
    elif isinstance(exc, PermissionError):
        code, message = "worker_authentication_failed", "Dell worker relay authentication failed"
    else:
        code, message = "worker_unavailable", "Dell worker is unavailable"
    return GatewayError(503, message, code, kind="api_error", job_id=job_id)


def _unsupported(name):
    raise GatewayError(400, f"Unsupported parameter: {name}", "unsupported_parameter", name)


def chat_prompt(data):
    """Validate the supported chat subset and serialize Qwen ChatML roles."""
    if not isinstance(data, dict):
        raise GatewayError(400, "Expected a JSON object", "invalid_request")
    allowed = {"model", "messages", "stream", "stream_options", "max_tokens", "temperature", "top_p", "n", "session_id", "cache_prompt"}
    for key in data:
        if key not in allowed:
            _unsupported(key)
    if data.get("model") != MODEL_ID:
        raise GatewayError(404, "Unknown model", "model_not_found", "model")
    if "stream" in data and type(data["stream"]) is not bool:
        _unsupported("stream")
    if "stream_options" in data and data["stream_options"] != {"include_usage": True}:
        _unsupported("stream_options")
    if "session_id" in data and (not isinstance(data["session_id"], str)
                                 or len(data["session_id"]) > 128):
        _unsupported("session_id")
    if "cache_prompt" in data and data["cache_prompt"] is not True:
        _unsupported("cache_prompt")
    for name, expected in (("max_tokens", TOKEN_LIMIT), ("n", 1)):
        if name in data and (type(data[name]) is not int or data[name] != expected):
            _unsupported(name)
    for name, expected in (("top_p", 1),):
        if name in data and (type(data[name]) not in (int, float) or data[name] != expected):
            _unsupported(name)
    if "temperature" in data and (type(data["temperature"]) not in (int, float)
                                  or not 0 <= data["temperature"] <= 2):
        _unsupported("temperature")
    messages = data.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        raise GatewayError(400, "Expected 1–8 messages", "invalid_messages", "messages")
    if not isinstance(messages[-1], dict) or messages[-1].get("role") != "user":
        raise GatewayError(400, "Last message must be from user", "invalid_messages", "messages")
    chunks = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise GatewayError(400, "Each message needs role and text content",
                               "invalid_messages", f"messages[{index}]")
        role, content = message["role"], message["content"]
        if role not in ("system", "user", "assistant") or not isinstance(content, str) or not content.strip():
            raise GatewayError(400, "Unsupported role or empty text content",
                               "invalid_messages", f"messages[{index}]")
        if any(token in content for token in SPECIAL_TOKENS):
            raise GatewayError(400, "Chat template delimiters are not allowed in content",
                               "invalid_messages", f"messages[{index}].content")
        chunks.append(f"<|im_start|>{role}\n{content}<|im_end|>\n")
    prompt = "".join(chunks) + "<|im_start|>assistant\n"
    if len(prompt.encode("utf-8")) > MAX_PROMPT:
        raise GatewayError(400, f"Formatted messages exceed {MAX_PROMPT} UTF-8 bytes",
                           "context_too_long", "messages")
    return prompt


def chat_response(job_id, sample):
    text = sample.get("text")
    prompt_tokens, completion_tokens = sample.get("prompt_n"), sample.get("predicted_n")
    if (not isinstance(text, str) or not text or type(prompt_tokens) is not int
            or type(completion_tokens) is not int or prompt_tokens < 0 or completion_tokens < 1):
        raise GatewayError(502, "Worker returned an invalid result", "invalid_worker_result",
                           kind="api_error")
    return {
        "id": "chatcmpl-" + job_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "length" if completion_tokens >= TOKEN_LIMIT else "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    }


class Gateway:
    def __init__(self, config, state, *, deadline_seconds=120):
        peers = json.loads(Path(config).read_text(encoding="utf-8"))
        if (not isinstance(peers, dict) or len(peers) != 1
                or any(not isinstance(peer, dict) or peer.get("forced_command") is not True
                       for peer in peers.values())):
            raise ValueError("Expected exactly one forced-command SSH worker")
        self.name, peer = next(iter(peers.items()))
        self.transport = ssh_transport(**peer)
        self.state = Path(state)
        if not self.state.parent.is_dir():
            raise ValueError("Coordinator state parent directory must exist")
        self.deadline_seconds = deadline_seconds
        self.lock = Lock()
        self.failures = deque(maxlen=16)

    def note_failure(self, error):
        self.failures.append({"at": int(time.time()), "code": error.code})

    def health(self):
        """One authenticated heartbeat; never loads the model for a health check."""
        try:
            coordinator = Coordinator(self.state)
            pending = [job["id"] for job in coordinator.jobs.values()
                       if job.get("status") not in TERMINAL]
        except (OSError, ValueError, TypeError, KeyError):
            return {"status": "OFFLINE", "reason": "coordinator_state_invalid",
                    "api": True, "coordinator": False, "worker": False,
                    "recent_failures": list(self.failures)}
        try:
            coordinator.enroll(self.name, self.transport, approved=True)
            ready = coordinator.available(self.name) and not pending
            report = coordinator.peers[self.name].capability or {}
        except (OSError, ValueError, PermissionError, subprocess.SubprocessError) as exc:
            error = transport_error(exc)
            return {"status": "DEGRADED", "reason": error.code,
                    "api": True, "coordinator": True, "worker": False,
                    "pending_jobs": pending, "recent_failures": list(self.failures)}
        return {"status": "HEALTHY" if ready else "DEGRADED",
                "reason": "ready" if ready else ("worker_busy" if report.get("busy") else "worker_not_ready"),
                "api": True, "coordinator": True, "ssh_relay": True,
                "worker": report.get("healthy") is True,
                "model_ready": report.get("model_sha256") == MODEL_SHA256
                               and report.get("backend_revision") == BACKEND_REVISION,
                "pending_jobs": pending, "recent_failures": list(self.failures)}

    def job_status(self, job_id):
        try:
            row = Coordinator(self.state).jobs.get(str(uuid.UUID(job_id)))
        except (OSError, ValueError, TypeError, KeyError):
            row = None
        if row is None:
            raise GatewayError(404, "Unknown job", "job_not_found", "job_id")
        return {"id": row["id"], "status": row["status"], "worker": row.get("worker"),
                "uncertain": row["status"] not in TERMINAL,
                "last_transport_error": row.get("last_transport_error")}

    def reconcile_once(self):
        """Resume only persisted unfinished work, under the dispatch lock."""
        if not self.lock.acquire(blocking=False):
            return "busy"
        try:
            coordinator = Coordinator(self.state)
            if not any(job.get("status") not in TERMINAL for job in coordinator.jobs.values()):
                return "idle"
            try:
                coordinator.enroll(self.name, self.transport, approved=True)
                coordinator.tick()
            except (OSError, ValueError, PermissionError, subprocess.SubprocessError) as exc:
                self.note_failure(transport_error(exc))
                return "offline"
            return ("pending" if any(job.get("status") not in TERMINAL
                                     for job in coordinator.jobs.values()) else "resolved")
        finally:
            self.lock.release()

    def maintain(self, stop):
        """Bounded background reconciliation; idle checks never load the model."""
        delay = 5
        while not stop.wait(delay):
            outcome = self.reconcile_once()
            delay = min(delay * 2, 60) if outcome == "offline" else (5 if outcome == "pending" else 15)

    def complete(self, prompt, request_key=None):
        if not self.lock.acquire(blocking=False):
            raise GatewayError(429, "Worker is busy", "worker_busy", kind="rate_limit_error")
        try:
            coordinator = Coordinator(self.state)
            previous = next((job for job in coordinator.jobs.values()
                             if request_key and job.get("request_key") == request_key), None)
            if previous and previous["prompt"] != prompt:
                raise GatewayError(409, "Idempotency key was used with a different prompt",
                                   "idempotency_conflict", kind="api_error")
            pending = next((job for job in coordinator.jobs.values()
                            if job.get("status") not in TERMINAL and job is not previous), None)
            if previous and previous["status"] in TERMINAL:
                return self._terminal(previous)
            try:
                coordinator.enroll(self.name, self.transport, approved=True)
            except (OSError, ValueError, PermissionError, subprocess.SubprocessError) as exc:
                raise transport_error(exc, (previous or pending or {}).get("id")) from exc
            if pending:
                coordinator.tick()  # Reconcile its original ID before accepting new work.
                pending = next((job for job in coordinator.jobs.values()
                                if job.get("status") not in TERMINAL and job is not previous), None)
                if pending:
                    raise GatewayError(503, "A previous job needs reconciliation",
                                       "reconciliation_pending", kind="api_error", job_id=pending["id"])
            if not coordinator.available(self.name) and not previous:
                busy = (coordinator.peers[self.name].capability or {}).get("busy") is True
                raise GatewayError(429 if busy else 503,
                                   "Dell worker is busy" if busy else "Dell worker is not ready",
                                   "worker_busy" if busy else "worker_not_ready",
                                   kind="rate_limit_error" if busy else "api_error")
            job_id = previous["id"] if previous else coordinator.submit(
                prompt, approved=True, request_key=request_key)
            until = time.monotonic() + self.deadline_seconds
            delay = 0.25
            while time.monotonic() < until:
                coordinator.tick()
                job = coordinator.jobs[job_id]
                if job["status"] in TERMINAL:
                    return self._terminal(job)
                delay = min(delay * 2, 4) if coordinator.peers[self.name].heartbeat == float("-inf") else 0.25
                time.sleep(min(delay, max(0, until - time.monotonic())))
            # The job may still be running on Dell. Preserve its ID for reconciliation.
            job = coordinator.jobs[job_id]
            job["last_timeout_ns"] = time.time_ns()
            coordinator.save()
            raise GatewayError(504, "Dell result is uncertain; retry with the same session or idempotency key",
                               "worker_timeout", kind="api_error", job_id=job_id)
        finally:
            self.lock.release()

    def _terminal(self, job):
        job_id = job["id"]
        if job["status"] == "timed-out":
            raise GatewayError(504, "Dell worker execution timed out", "worker_timeout",
                               kind="api_error", job_id=job_id)
        if job["status"] != "completed" or job.get("worker") != self.name:
            code = (job.get("result") or {}).get("code")
            if code == "model_context_exceeded":
                raise GatewayError(400, "Model context exceeded", code, "messages", job_id=job_id)
            raise GatewayError(502, "Dell worker did not complete the request",
                               "worker_internal_failure", kind="api_error", job_id=job_id)
        samples = (job.get("result") or {}).get("samples")
        if not isinstance(samples, list) or len(samples) != 1:
            raise GatewayError(502, "Worker returned an invalid result",
                               "invalid_worker_result", kind="api_error", job_id=job_id)
        return job_id, chat_response(job_id, samples[0])


class GatewayHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def make_handler(gateway):
    class Handler(BaseHTTPRequestHandler):
        def send_stream(self, result, job_id):
            base = {"id": result["id"], "object": "chat.completion.chunk",
                    "created": result["created"], "model": MODEL_ID}
            chunks = [
                {**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
                {**base, "choices": [{"index": 0, "delta": {"content": result["choices"][0]["message"]["content"]}, "finish_reason": None}]},
                {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": result["choices"][0]["finish_reason"]}]},
                {**base, "choices": [], "usage": result["usage"]},
            ]
            body = b"".join(b"data: " + json.dumps(chunk, ensure_ascii=False).encode("utf-8") + b"\n\n"
                            for chunk in chunks) + b"data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Community-Grid-Job-ID", job_id)
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status, data, *, job_id=None):
            raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if job_id:
                self.send_header("X-Community-Grid-Job-ID", job_id)
            self.end_headers()
            self.wfile.write(raw)

        def error_json(self, error):
            if hasattr(gateway, "note_failure"):
                gateway.note_failure(error)
            self.send_json(error.status, {"error": {"message": str(error), "type": error.kind,
                                                    "param": error.param, "code": error.code,
                                                    "job_id": error.job_id}}, job_id=error.job_id)

        def do_GET(self):
            if self.path == "/health":
                return self.send_json(200, gateway.health())
            if re.fullmatch(r"/v1/jobs/[a-f0-9-]{36}", self.path):
                try:
                    return self.send_json(200, gateway.job_status(self.path.rsplit("/", 1)[1]))
                except GatewayError as exc:
                    return self.error_json(exc)
            if self.path != "/v1/models":
                return self.error_json(GatewayError(404, "Unknown endpoint", "not_found"))
            self.send_json(200, {"object": "list", "data": [{"id": MODEL_ID, "object": "model",
                                                               "created": 0, "owned_by": "community-grid"}]})

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                return self.error_json(GatewayError(404, "Unknown endpoint", "not_found"))
            try:
                if self.headers.get_content_type() != "application/json":
                    raise GatewayError(415, "Content-Type must be application/json",
                                       "unsupported_media_type")
                raw_length = self.headers.get("Content-Length")
                if raw_length is None or not raw_length.isdecimal() or not 0 < int(raw_length) <= MAX_BODY:
                    raise GatewayError(400, f"Expected a JSON body of at most {MAX_BODY} bytes",
                                       "invalid_request")
                raw = self.rfile.read(int(raw_length))
                try:
                    data = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise GatewayError(400, "Malformed JSON", "invalid_json") from exc
                prompt = chat_prompt(data)
                raw_key = self.headers.get("Idempotency-Key")
                if raw_key is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", raw_key):
                    raise GatewayError(400, "Invalid Idempotency-Key", "invalid_idempotency_key",
                                       "Idempotency-Key")
                if raw_key is not None:
                    request_key = hashlib.sha256(("client:" + raw_key).encode()).hexdigest()
                elif data.get("session_id"):
                    request_key = hashlib.sha256(("session:" + data["session_id"] + "\0" + prompt).encode()).hexdigest()
                else:
                    request_key = None
                job_id, result = gateway.complete(prompt, request_key=request_key)
                if data.get("stream", False):
                    self.send_stream(result, job_id)
                else:
                    self.send_json(200, result, job_id=job_id)
            except GatewayError as exc:
                self.error_json(exc)

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    if args.host != "127.0.0.1":
        parser.error("Only 127.0.0.1 is supported; remote exposure requires authenticated TLS")
    if not 1 <= args.port <= 65535:
        parser.error("Port must be 1–65535")
    gateway = Gateway(args.config, args.state)
    stop = Event()
    maintainer = Thread(target=gateway.maintain, args=(stop,), name="job-reconciler", daemon=True)
    maintainer.start()
    with GatewayHTTPServer((args.host, args.port), make_handler(gateway)) as server:
        print(f"Community Grid OpenAI API: http://{args.host}:{args.port}/v1", flush=True)
        try:
            server.serve_forever()
        finally:
            stop.set()
            maintainer.join(timeout=2)


if __name__ == "__main__":
    main()

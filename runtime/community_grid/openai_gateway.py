"""Small OpenAI-compatible, loopback-only adapter for one pinned SSH inference worker."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
from threading import Lock
import time

from .coordinator import Coordinator, TERMINAL, ssh_transport
from .inference import TOKEN_LIMIT


MODEL_ID = "community-grid-qwen2.5-1.5b-instruct-q4_k_m"
MAX_BODY = 8192
MAX_PROMPT = 512
MAX_MESSAGES = 8
SPECIAL_TOKENS = ("<|im_start|>", "<|im_end|>", "<|endoftext|>")


class GatewayError(Exception):
    def __init__(self, status, message, code, param=None, kind="invalid_request_error"):
        super().__init__(message)
        self.status, self.code, self.param, self.kind = status, code, param, kind


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
        raise GatewayError(400, "Formatted messages exceed 512 UTF-8 bytes",
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

    def complete(self, prompt):
        if not self.lock.acquire(blocking=False):
            raise GatewayError(429, "Worker is busy", "worker_busy", kind="rate_limit_error")
        try:
            coordinator = Coordinator(self.state)
            try:
                coordinator.enroll(self.name, self.transport, approved=True)
            except (OSError, ValueError, PermissionError, subprocess.SubprocessError) as exc:
                raise GatewayError(503, "Dell worker is unavailable", "worker_unavailable",
                                   kind="api_error") from exc
            job_id = coordinator.submit(prompt, approved=True)
            until = time.monotonic() + self.deadline_seconds
            while time.monotonic() < until:
                coordinator.tick()
                job = coordinator.jobs[job_id]
                if job["status"] in TERMINAL:
                    if job["status"] != "completed" or job.get("worker") != self.name:
                        raise GatewayError(502, "Dell worker did not complete the request",
                                           "worker_failed", kind="api_error")
                    samples = job.get("result", {}).get("samples")
                    if not isinstance(samples, list) or len(samples) != 1:
                        raise GatewayError(502, "Worker returned an invalid result",
                                           "invalid_worker_result", kind="api_error")
                    return job_id, chat_response(job_id, samples[0])
                time.sleep(0.25)
            coordinator.cancel(job_id, approved=True)
            raise GatewayError(504, "Dell worker timed out", "worker_timeout", kind="api_error")
        finally:
            self.lock.release()


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
            self.send_json(error.status, {"error": {"message": str(error), "type": error.kind,
                                                    "param": error.param, "code": error.code}})

        def do_GET(self):
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
                    raise GatewayError(400, "Expected a JSON body of at most 8192 bytes",
                                       "invalid_request")
                raw = self.rfile.read(int(raw_length))
                try:
                    data = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise GatewayError(400, "Malformed JSON", "invalid_json") from exc
                prompt = chat_prompt(data)
                job_id, result = gateway.complete(prompt)
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
    with GatewayHTTPServer((args.host, args.port), make_handler(gateway)) as server:
        print(f"Community Grid OpenAI API: http://{args.host}:{args.port}/v1", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()

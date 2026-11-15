"""Opt-in local llama.cpp benchmark. Runs only as an explicit command."""
import argparse
import hashlib
import http.client
import json
import math
import os
import secrets
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import time

MODEL_SHA256 = "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"
MODEL_SIZE = 1117320736
BACKEND_REVISION = "05c0380f2ae5c7193445e03ebc7b6de9ce49f1a6"
PROMPT = (
    "Explain how a community library could organize a small seed exchange. "
    "Cover labeling, seasonal planning, storage and accessibility. "
    "Use clear practical examples. Answer:\n"
)
TOKEN_LIMIT = 64
CONTEXT = 1024


def verified_digest(path, expected=None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    result = digest.hexdigest()
    if expected is not None and result != expected:
        raise ValueError(f"SHA-256 mismatch for {path.name}")
    return result


def completion(port, prompt=PROMPT, include_text=False, api_key=None):
    """Measure first nonempty text and require native timing counters."""
    body = json.dumps({
        "prompt": prompt, "stream": True, "cache_prompt": False,
        "n_predict": TOKEN_LIMIT, "temperature": 0, "seed": 42, "ignore_eos": not include_text,
    })
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    first = None
    chunks = []
    started = time.monotonic()
    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        connection.request("POST", "/completion", body, headers)
        with connection.getresponse() as response:
            if response.status != 200 or response.headers.get_content_type() != "text/event-stream":
                raise ValueError(f"Unexpected completion HTTP response ({response.status})")
            for line in response:
                elapsed = time.monotonic() - started
                if elapsed > 120:
                    raise TimeoutError("Completion exceeded its sample deadline")
                if not line.startswith(b"data: "):
                    continue
                event = json.loads(line[6:])
                if event.get("content") and first is None:
                    first = elapsed
                if include_text:
                    chunks.append(event.get("content", ""))
                if event.get("stop") is True:
                    timings = event.get("timings", {})
                    count = timings.get("predicted_n")
                    if first is None or type(count) is not int or not 1 <= count <= TOKEN_LIMIT or (
                        not include_text and count != TOKEN_LIMIT
                    ):
                        raise ValueError("Completion did not produce valid output token timing")
                    result = {"ttft_ms": first * 1000, "elapsed_ms": elapsed * 1000}
                    for key in ("prompt_n", "prompt_per_second", "predicted_per_second"):
                        value = timings.get(key)
                        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                            raise ValueError(f"Missing or invalid timing: {key}")
                        result[key] = value
                    if include_text:
                        result["text"] = "".join(chunks)
                    return {**result, "predicted_n": count}
    finally:
        connection.close()
    raise ValueError("Stream ended without final timing counters")


def wait_ready(process, port, api_key=None):
    deadline = time.monotonic() + 120
    while process.poll() is None and time.monotonic() < deadline:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            connection.request("GET", "/health", headers=headers)
            with connection.getresponse() as response:
                if response.status == 200 and json.load(response).get("status") == "ok":
                    return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(0.25)
    raise RuntimeError(f"Backend failed readiness (exit status {process.poll()})")


def run_threads(backend, model, threads, sample_count=3, warmup=True, prompt=None):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = [
        str(backend), "--model", str(model), "--host", "127.0.0.1", "--port", str(port),
        "--threads", str(threads), "--threads-batch", str(threads), "--ctx-size", str(CONTEXT),
        "--n-gpu-layers", "0", "--parallel", "1",
    ]
    environment = {key: value for key, value in os.environ.items() if not key.startswith("LLAMA_ARG_")}
    api_key = None
    if prompt is not None:
        api_key = secrets.token_urlsafe(32)
        environment["LLAMA_API_KEY"] = api_key
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=sys.stderr, env=environment)
    try:
        wait_ready(process, port, api_key)
        if warmup:
            completion(port)
        samples = [completion(port) if prompt is None else completion(port, prompt, include_text=True, api_key=api_key)
                   for _ in range(sample_count)]
        if sys.platform == "win32":
            from .windows_job import peak_rss_kib
            peak = peak_rss_kib(process.pid)
        else:
            status = Path(f"/proc/{process.pid}/status").read_text()
            peak = int(next(line for line in status.splitlines() if line.startswith("VmHWM:")).split()[1])
        if process.poll() is not None:
            raise RuntimeError(f"Backend exited unexpectedly ({process.returncode})")
        return {
            "threads": threads, "samples": samples, "stable": True,
            "peak_rss_kib": peak,
            "medians": {key: statistics.median(item[key] for item in samples)
                        for key in ("ttft_ms", "prompt_per_second", "predicted_per_second")},
        }
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="Run one measured request at two threads")
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--backend-sha256", required=True)
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.enable:
        parser.error("Inference is disabled by default; pass --enable for this run")
    if sys.platform != "linux":
        parser.error("The hardware benchmark requires Linux process RSS accounting")
    backend = args.backend.resolve(strict=True)
    model = args.model.resolve(strict=True)
    if model.stat().st_size != MODEL_SIZE:
        parser.error("Model size differs from pinned Qwen GGUF")
    model_digest = verified_digest(model, MODEL_SHA256)
    backend_digest = verified_digest(backend, args.backend_sha256.lower())
    version = subprocess.run([str(backend), "--version"], capture_output=True, text=True,
                             check=True, timeout=10)
    version_text = (version.stdout + version.stderr).strip()
    if BACKEND_REVISION[:7] not in version_text:
        parser.error("Backend version does not match the pinned llama.cpp revision")
    results = []
    for threads in ((2,) if args.smoke else (1, 2, 4)):
        try:
            results.append(run_threads(backend, model, threads, 1 if args.smoke else 3, not args.smoke))
        except (OSError, RuntimeError, ValueError, http.client.HTTPException) as exc:
            results.append({"threads": threads, "stable": False, "error": str(exc)})
    print(json.dumps({
        "backend_revision": BACKEND_REVISION, "backend_version": version_text,
        "backend_sha256": backend_digest, "model_sha256": model_digest,
        "context": CONTEXT, "output_tokens": TOKEN_LIMIT, "results": results,
    }, indent=2))
    return int(any(not result["stable"] for result in results))


if __name__ == "__main__":
    sys.exit(main())

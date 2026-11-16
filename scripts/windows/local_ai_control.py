"""User-level Community Grid API supervisor and status command for Windows."""

import argparse
import json
import msvcrt
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from threading import Thread
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[4]
REPO = Path(__file__).resolve().parents[2]
WORK = ROOT / "work"
CONFIG = ROOT / "outputs" / "Dell-Inference-Coordinator.json"
STATE = WORK / "gateway-state.json"
LOCK = WORK / "community-grid-api-supervisor.lock"
PIDFILE = WORK / "community-grid-api-supervisor.pid"
STOPFILE = WORK / "community-grid-api-supervisor.stop"
LOG = WORK / "community-grid-api-supervisor.log"
API_LOG = WORK / "community-grid-api.log"
ODYSSEUS_LOG = WORK / "odysseus-supervisor.log"
ODYSSEUS_CONFIG = ROOT / "outputs" / "Odysseus-Local-Config.json"
PORT = 8787
MODEL = "community-grid-qwen2.5-1.5b-instruct-q4_k_m"
PROVIDER = f"http://127.0.0.1:{PORT}/v1"


def log(message):
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")


def fetch(url, timeout=3):
    with urlopen(url, timeout=timeout) as response:
        return json.load(response)


def listener_present():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=2):
            return True
    except OSError:
        return False


def api_identity_matches():
    try:
        data = fetch(f"http://127.0.0.1:{PORT}/v1/models")
        return any(row.get("id") == MODEL for row in data.get("data", []))
    except (OSError, ValueError, TypeError):
        return False


def stop_requested(pid):
    try:
        return STOPFILE.read_text(encoding="ascii").strip() == str(pid)
    except OSError:
        return False


def pause(seconds, pid):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if stop_requested(pid):
            return False
        time.sleep(min(0.5, until - time.monotonic()))
    return True


def odysseus_loop(pid):
    """Supervise the existing isolated Windows checkout without editing it."""
    failures = []
    while not stop_requested(pid):
        try:
            if fetch("http://127.0.0.1:9700/api/auth/status").get("configured") is True:
                if not pause(10, pid):
                    return
                continue
        except (OSError, ValueError, TypeError):
            pass
        if not ODYSSEUS_CONFIG.is_file():
            log("Odysseus config missing; retrying in 30 seconds")
            if not pause(30, pid):
                return
            continue
        config = json.loads(ODYSSEUS_CONFIG.read_text(encoding="utf-8"))
        app = Path(config["app"])
        private = Path(config["private"])
        overlay = Path(config["overlay"])
        if not (app / "app.py").is_file() or not private.is_file() or not (overlay / "sqlalchemy").is_dir():
            log("Odysseus app, credential reference, or verified overlay missing")
            if not pause(30, pid):
                return
            continue
        now = time.monotonic()
        failures = [stamp for stamp in failures if now - stamp < 600]
        if len(failures) >= 5:
            log("Five Odysseus exits in ten minutes; cooling down for five minutes")
            if not pause(300, pid):
                return
            failures.clear()
            continue
        credentials = json.loads(private.read_text(encoding="utf-8"))
        env = {key: value for key, value in os.environ.items()
               if not any(name in key.upper() for name in
                          ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"))}
        env.update({
            "ODYSSEUS_ADMIN_USER": credentials["username"],
            "ODYSSEUS_ADMIN_PASSWORD": credentials["password"],
            "ODYSSEUS_DATA_DIR": str(app / "data-stage8d"),
            "DATABASE_URL": "sqlite:///" + (app / "data-stage8d" / "app.db").as_posix(),
            "APP_BIND": "127.0.0.1", "APP_PORT": "9700", "AUTH_ENABLED": "true",
            "LOCALHOST_BYPASS": "false", "ODYSSEUS_STARTUP_WARMUPS": "0",
            "ODYSSEUS_MODEL_KEEPALIVE": "0", "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(overlay),
        })
        del credentials
        started = time.monotonic()
        with ODYSSEUS_LOG.open("a", encoding="utf-8") as output:
            child = subprocess.Popen([str(app / ".venv" / "Scripts" / "python.exe"), "app.py"],
                                     cwd=app, env=env, stdin=subprocess.DEVNULL,
                                     stdout=output, stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            log(f"Odysseus started pid={child.pid}")
            while child.poll() is None and not stop_requested(pid):
                time.sleep(0.5)
            if stop_requested(pid) and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            code = child.returncode
        if stop_requested(pid):
            return
        uptime = time.monotonic() - started
        failures = [] if uptime >= 120 else failures + [time.monotonic()]
        delay = min(2 ** (len(failures) - 1), 60)
        log(f"Odysseus exited code={code} after {uptime:.1f}s; retrying in {delay}s")
        if not pause(delay, pid):
            return


def odysseus_guard(pid):
    while not stop_requested(pid):
        try:
            odysseus_loop(pid)
            return
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            log(f"Odysseus supervisor error: {type(exc).__name__}; retrying in 30 seconds")
            if not pause(30, pid):
                return


def supervise():
    WORK.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return  # A live supervisor already owns this user-level lock.
        pid = os.getpid()
        PIDFILE.write_text(str(pid), encoding="ascii")
        log(f"Supervisor started pid={pid}")
        odysseus = Thread(target=odysseus_guard, args=(pid,), name="odysseus-supervisor", daemon=True)
        odysseus.start()
        child = None
        failures = []
        try:
            while not stop_requested(pid):
                if listener_present():
                    if not api_identity_matches():
                        log("Port 8787 has an unrecognized listener; leaving it alone")
                    if not pause(10, pid):
                        break
                    continue
                if not REPO.is_dir() or not CONFIG.is_file():
                    log("Repository or pinned peer config missing; retrying in 30 seconds")
                    if not pause(30, pid):
                        break
                    continue
                now = time.monotonic()
                failures = [stamp for stamp in failures if now - stamp < 600]
                if len(failures) >= 5:
                    log("Five API exits in ten minutes; cooling down for five minutes")
                    if not pause(300, pid):
                        break
                    failures.clear()
                    continue
                env = os.environ.copy()
                env["PYTHONPATH"] = str(REPO / "runtime")
                env["PYTHONDONTWRITEBYTECODE"] = "1"
                command = [sys.executable, "-m", "community_grid.openai_gateway",
                           "--config", str(CONFIG), "--state", str(STATE),
                           "--host", "127.0.0.1", "--port", str(PORT)]
                started = time.monotonic()
                try:
                    with API_LOG.open("a", encoding="utf-8") as output:
                        child = subprocess.Popen(command, cwd=REPO, env=env,
                                                 stdin=subprocess.DEVNULL, stdout=output,
                                                 stderr=subprocess.STDOUT,
                                                 creationflags=subprocess.CREATE_NO_WINDOW)
                        log(f"API started pid={child.pid}")
                        while child.poll() is None and not stop_requested(pid):
                            time.sleep(0.5)
                        if stop_requested(pid) and child.poll() is None:
                            child.terminate()
                            try:
                                child.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                child.kill()
                                child.wait()
                        code = child.returncode
                except OSError as exc:
                    code = type(exc).__name__
                    log(f"API launch failed: {code}")
                child = None
                if stop_requested(pid):
                    break
                uptime = time.monotonic() - started
                failures = [] if uptime >= 120 else failures + [time.monotonic()]
                delay = min(2 ** (len(failures) - 1), 60)
                log(f"API exited code={code} after {uptime:.1f}s; retrying in {delay}s")
                if not pause(delay, pid):
                    break
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
            odysseus.join(timeout=12)
            if PIDFILE.exists() and PIDFILE.read_text(encoding="ascii").strip() == str(pid):
                PIDFILE.unlink()
            log("Supervisor stopped")
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def status():
    try:
        health = fetch(f"http://127.0.0.1:{PORT}/health", timeout=12)
    except (OSError, ValueError):
        print("OFFLINE api_unreachable")
        return 2
    state = health.get("status", "OFFLINE")
    reason = health.get("reason", "unknown")
    try:
        configured = fetch("http://127.0.0.1:9700/api/auth/status").get("configured") is True
    except (OSError, ValueError):
        configured = False
    provider_ready = False
    if configured:
        try:
            app = Path(json.loads(ODYSSEUS_CONFIG.read_text(encoding="utf-8"))["app"])
            database = app / "data-stage8d" / "app.db"
            with sqlite3.connect("file:" + database.as_posix() + "?mode=ro", uri=True, timeout=2) as db:
                provider_ready = db.execute(
                    "SELECT 1 FROM model_endpoints WHERE base_url=? AND is_enabled=1 LIMIT 1",
                    (PROVIDER,)).fetchone() is not None
        except (OSError, ValueError, KeyError, sqlite3.Error):
            provider_ready = False
    if state == "HEALTHY" and not configured:
        state, reason = "DEGRADED", "odysseus_unavailable"
    elif state == "HEALTHY" and not provider_ready:
        state, reason = "DEGRADED", "odysseus_provider_missing"
    print(f"{state} {reason} api=127.0.0.1:{PORT} odysseus={'ready' if configured else 'offline'} "
          f"provider={'ready' if provider_ready else 'missing'} "
          f"pending={len(health.get('pending_jobs', []))} "
          f"recent_failures={len(health.get('recent_failures', []))}")
    return {"HEALTHY": 0, "DEGRADED": 1}.get(state, 2)


def start():
    if STOPFILE.exists():
        STOPFILE.unlink()
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise RuntimeError("Python windowless executable not found")
    subprocess.Popen([str(pythonw), str(Path(__file__).resolve()), "supervise"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                     close_fds=True)
    print("Supervisor launch requested; use 'status' to verify readiness")
    return 0


def stop():
    if not PIDFILE.is_file():
        print("Supervisor is not registered as running")
        return 1
    try:
        health = fetch(f"http://127.0.0.1:{PORT}/health", timeout=12)
        if health.get("pending_jobs"):
            print("Refusing stop while a job awaits reconciliation")
            return 1
    except (OSError, ValueError):
        pass
    pid = PIDFILE.read_text(encoding="ascii").strip()
    STOPFILE.write_text(pid, encoding="ascii")
    until = time.monotonic() + 20
    while PIDFILE.exists() and time.monotonic() < until:
        time.sleep(0.25)
    if PIDFILE.exists():
        print("Supervisor stop was requested but did not finish within 20 seconds")
        return 1
    print("Supervisor stopped")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("supervise", "start", "stop", "restart", "status"))
    action = parser.parse_args().action
    if action == "supervise":
        supervise()
        return 0
    if action == "status":
        return status()
    if action == "start":
        return start()
    if action == "stop":
        return stop()
    if stop() != 0:
        return 1
    return start()


if __name__ == "__main__":
    sys.exit(main())

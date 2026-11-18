"""Prototype desktop launcher: persistent offline backend, no cloud sync.

Usage from repository root: python -m desktop_runtime.run_local
Requires installed mongod and a built frontend. No test fixtures are imported.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
import webbrowser

from .local_mongo import (
    LocalMongoError, app_data_dir, loopback_port_available,
    start_owned_mongo, stop_owned_process,
)

ROOT = Path(__file__).resolve().parents[1]


def create_private_file(path: Path, contents: str) -> None:
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as f:
        f.write(contents)


def credentials(root: Path) -> tuple[str, str]:
    secret_file = root / "auth.secret"
    try:
        create_private_file(secret_file, secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    password = secret_file.read_text(encoding="utf-8").strip()
    if len(password) < 32:
        raise LocalMongoError("Invalid desktop auth secret; refuse startup")
    return "desktop", password


def device_identity(root: Path) -> str:
    device_file = root / "device.id"
    try:
        create_private_file(device_file, str(uuid.uuid4()))
    except FileExistsError:
        pass
    value = device_file.read_text(encoding="utf-8").strip()
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise LocalMongoError("Corrupt desktop device identity") from exc


def wait_api(url: str, process: subprocess.Popen, seconds: float = 30) -> None:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if process.poll() is not None:
            raise LocalMongoError("FastAPI stopped during startup; inspect local-api.log")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise LocalMongoError("FastAPI did not pass health check")


def main() -> int:
    root = app_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    # Never start with an unknown process on a fixed port: avoid connecting to
    # a different workspace or uploading production data by mistake.
    mongo_port = int(os.environ.get("GESTIONALE_DESKTOP_MONGO_PORT", "27028"))
    api_port = int(os.environ.get("GESTIONALE_DESKTOP_API_PORT", "18085"))
    if not loopback_port_available(api_port):
        raise LocalMongoError(f"FastAPI port {api_port} is occupied")
    mongod = shutil.which("mongod")
    if not mongod:
        raise LocalMongoError("mongod executable not found in PATH; install MongoDB Community Server first")
    if not (ROOT / "frontend" / "build" / "index.html").is_file():
        raise LocalMongoError("Missing frontend build; run yarn build in frontend/ first")
    username, password = credentials(root)
    device_id = device_identity(root)
    mongo_proc = None
    api_proc = None
    try:
        if os.environ.get("GESTIONALE_DESKTOP_SECURE_PREVIEW") == "1":
            from .secure_runtime import start_secure_owned_mongo
            mongo_proc, uri = start_secure_owned_mongo(
                mongod, mongo_port, root, device_id)
        else:
            mongo_proc, uri = start_owned_mongo(mongod, mongo_port, root)
        env = os.environ.copy()
        env.update({
            "MONGO_URL": uri,
            "DB_NAME": "gestionale_desktop",
            "GESTIONALE_DESKTOP_STRICT_MODE": "1",
            "GESTIONALE_DESKTOP_DEVICE_ID": device_id,
            "ENVIRONMENT": "local",
            "APP_USERNAME": username,
            "APP_PASSWORD": password,
            "AGENT_INTEGRATION_ENABLED": "false",
            "ENABLE_LEGACY_EXCEL_IMPORT": "false",
            "AUTO_ORDER_3_1_ENABLED": "0",
            "CORS_ORIGINS": f"http://127.0.0.1:{api_port}",
        })
        with (root / "local-api.log").open("ab") as log:
            api_proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "backend.server:app",
                 "--host", "127.0.0.1", "--port", str(api_port)],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT,
            )
        url = f"http://127.0.0.1:{api_port}/api/"
        wait_api(url, api_proc)
        print("LOCAL DESKTOP PROTOTYPE (no cloud sync / not a packaged installer)", flush=True)
        print(f"Open http://127.0.0.1:{api_port}/", flush=True)
        print("Login username: desktop", flush=True)
        print(f"Login password: {password}", flush=True)
        print(f"Persistent data directory: {root}", flush=True)
        print("Close with Ctrl-C. Do NOT use for production operations.", flush=True)
        if os.environ.get("GESTIONALE_DESKTOP_NO_BROWSER") != "1":
            webbrowser.open(f"http://127.0.0.1:{api_port}/")
        while api_proc.poll() is None and mongo_proc.poll() is None:
            time.sleep(0.4)
        raise LocalMongoError("One of the local runtime processes exited unexpectedly")
    except KeyboardInterrupt:
        return 0
    finally:
        if api_proc:
            stop_owned_process(api_proc)
        if mongo_proc:
            stop_owned_process(mongo_proc)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (LocalMongoError, ValueError, OSError) as exc:
        print(f"Desktop startup blocked: {exc}", file=sys.stderr)
        sys.exit(1)

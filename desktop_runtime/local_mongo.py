"""Manage an owned, persistent loopback-only MongoDB single-node replica set.

Prototype only: mongod must be installed separately. Local MongoDB does NOT yet
have database authentication; do not treat this as a hardened installer.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any

from pymongo import MongoClient
from pymongo.errors import PyMongoError


class LocalMongoError(RuntimeError):
    pass


def app_data_dir() -> Path:
    override = os.environ.get("GESTIONALE_DESKTOP_DATA_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "GestionaleTabacchiDesktop"
    elif os.name == "nt":
        path = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "GestionaleTabacchiDesktop"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "gestionale-tabacchi-desktop"
    return path


def loopback_port_available(port: int) -> bool:
    if not (1024 <= port <= 65535):
        raise LocalMongoError("Port must be between 1024 and 65535")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        try:
            connection.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _wait_ping(client: MongoClient[Any], process: subprocess.Popen[Any], seconds: float = 25) -> None:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if process.poll() is not None:
            raise LocalMongoError("mongod exited before becoming ready; inspect desktop/mongod.log")
        try:
            client.admin.command("ping")
            return
        except PyMongoError:
            time.sleep(0.25)
    raise LocalMongoError("Timeout waiting for local mongod")


def wait_primary(client: MongoClient[Any], seconds: float = 35) -> None:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        try:
            hello = client.admin.command("hello")
            if hello.get("isWritablePrimary") and hello.get("setName") == "rsGestionaleDesktop":
                return
            if hello.get("setName") and hello["setName"] != "rsGestionaleDesktop":
                raise LocalMongoError("Unexpected MongoDB replica set")
        except PyMongoError:
            pass
        time.sleep(0.3)
    raise LocalMongoError("Local MongoDB did not become a writable replica-set primary")


def check_transactions(client: MongoClient[Any], database: str) -> None:
    """Actually commit a temporary insert/delete in a real multi-document transaction."""
    hello = client.admin.command("hello")
    if not hello.get("setName") or hello.get("logicalSessionTimeoutMinutes") is None:
        raise LocalMongoError("Transactions unavailable: MongoDB is not a replica set")
    db = client[database]
    # A namespace must exist before use inside a transaction on older MongoDB.
    collection = db["desktop_health_probes"]
    collection.create_index("token", unique=True)
    token = os.urandom(16).hex()
    with client.start_session() as session:
        with session.start_transaction():
            collection.insert_one({"token": token}, session=session)
            collection.delete_one({"token": token}, session=session)
    if collection.count_documents({"token": token}):
        raise LocalMongoError("Transaction probe left an unexpected document")


def start_owned_mongo(mongod: str, port: int, root: Path) -> tuple[subprocess.Popen[Any], str]:
    if not loopback_port_available(port):
        raise LocalMongoError(f"MongoDB port {port} already used: refusing to attach to unknown process")
    dbpath = root / "mongo-data"
    dbpath.mkdir(parents=True, exist_ok=True)
    logfile = root / "mongod.log"
    uri = f"mongodb://127.0.0.1:{port}/?replicaSet=rsGestionaleDesktop&directConnection=true"
    try:
        log = logfile.open("ab")
        try:
            proc = subprocess.Popen(
                [mongod, "--dbpath", str(dbpath), "--bind_ip", "127.0.0.1",
                 "--port", str(port), "--replSet", "rsGestionaleDesktop",
                 "--logpath", str(logfile), "--logappend"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            )
        finally:
            log.close()
    except OSError as exc:
        raise LocalMongoError(f"Cannot launch mongod: {exc}") from exc

    # Direct connection allows initiating a replica set before primary election.
    client = MongoClient(f"mongodb://127.0.0.1:{port}/?directConnection=true",
                         serverSelectionTimeoutMS=500, connectTimeoutMS=500)
    try:
        _wait_ping(client, proc)
        try:
            status = client.admin.command("replSetGetStatus")
            configured = status.get("set") == "rsGestionaleDesktop"
        except PyMongoError as exc:
            if getattr(exc, "code", None) != 94:  # NotYetInitialized
                raise LocalMongoError(f"Cannot inspect replica set: {exc}") from exc
            configured = False
        if not configured:
            client.admin.command("replSetInitiate", {
                "_id": "rsGestionaleDesktop",
                "members": [{"_id": 0, "host": f"127.0.0.1:{port}"}],
            })
        wait_primary(client)
        check_transactions(client, "gestionale_desktop")
        return proc, uri
    except Exception:
        stop_owned_process(proc)
        raise
    finally:
        client.close()


def stop_owned_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=12)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)

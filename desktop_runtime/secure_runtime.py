"""Gate 4B secure *preview* desktop Mongo profile for NEW installs only.

Never upgrades an existing unauthenticated data directory. macOS/Windows
native credential-store integration is required; there is no plaintext
fallback. Actual OS installers and platform smoke tests remain pending.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any
from uuid import UUID

from pymongo import MongoClient

from .local_mongo import LocalMongoError, _wait_ping, check_transactions,     loopback_port_available, stop_owned_process
from .mongo_auth import (
    MongoSecurityError, authenticated_uri, bootstrap_first_users,
    create_or_validate_keyfile, verify_authenticated_access,
)


REPLICA = "rsGestionaleDesktopSecure"
DATABASE = "gestionale_desktop"
SERVICE = "GestionaleTabacchiDesktopMongoPreview"


def _store_name(device_id: str, role: str) -> str:
    if str(UUID(device_id)) != device_id or role not in {"admin", "api"}:
        raise MongoSecurityError("Invalid desktop identity or account role")
    return f"{device_id}/{role}"


def _native_store(override: Any = None, *,
                  platform_id: str | None = None, backend_name: str | None = None) -> Any:
    platform_id = platform_id or sys.platform
    if override is None:
        try:
            import keyring
            override = keyring
            backend_name = keyring.get_keyring().__class__.__module__
        except Exception as exc:
            raise MongoSecurityError("Secure OS keychain unavailable; refusing local Mongo startup") from exc
    backend_name = backend_name or ""
    prefixes = {"darwin": "keyring.backends.macOS",
                "win32": "keyring.backends.Windows"}
    expected = prefixes.get(platform_id)
    if expected is None or not backend_name.startswith(expected):
        raise MongoSecurityError("Native macOS Keychain or Windows Credential Manager required")
    return override


def prepare_secure_profile(root: Path, device_id: str, *,
                           store: Any = None,
                           platform_id: str | None = None,
                           backend_name: str | None = None) -> tuple[Path, str, str, bool]:
    """Return data dir, admin secret, limited app secret, and new-install flag.

    A partial installation or an old insecure DB is never silently migrated or
    recreated. Admin and app secrets survive only in the native OS keychain.
    """
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise MongoSecurityError("Desktop data root must be an existing regular directory")
    if (root / "mongo-data").exists():
        raise MongoSecurityError("Legacy unauthenticated database present; explicit migration required")
    native = _native_store(store, platform_id=platform_id, backend_name=backend_name)
    admin_name, app_name = _store_name(device_id, "admin"), _store_name(device_id, "api")
    try:
        admin_secret = native.get_password(SERVICE, admin_name)
        app_secret = native.get_password(SERVICE, app_name)
    except Exception as exc:
        raise MongoSecurityError("Native keychain read failed") from exc
    path = root / "secure-mongo-data"
    keyfile = root / "mongo-internal.key"
    exists = path.exists()
    if exists:
        if (not path.is_dir() or path.is_symlink() or not keyfile.is_file()
            or not admin_secret or not app_secret):
            raise MongoSecurityError("Incomplete secure installation: manual recovery required")
        create_or_validate_keyfile(keyfile)
        if len(admin_secret) < 32 or len(app_secret) < 32:
            raise MongoSecurityError("Invalid stored MongoDB credentials")
        return path, admin_secret, app_secret, False
    if admin_secret or app_secret or keyfile.exists() or keyfile.is_symlink():
        raise MongoSecurityError("Partial secure installation: refusing reset or implicit migration")
    admin_secret = secrets.token_urlsafe(48)
    app_secret = secrets.token_urlsafe(48)
    try:
        native.set_password(SERVICE, admin_name, admin_secret)
        native.set_password(SERVICE, app_name, app_secret)
    except Exception as exc:
        raise MongoSecurityError("Native keychain write failed; intervention required") from exc
    # If provisioning stops after this point, the existing keys must be
    # inspected manually; no auto-repair or destructive cleanup.
    create_or_validate_keyfile(keyfile)
    path.mkdir(mode=0o700)
    return path, admin_secret, app_secret, True


def start_secure_owned_mongo(mongod: str, port: int, root: Path,
                             device_id: str) -> tuple[subprocess.Popen, str]:
    """Start an AUTH-required local mongod, owning only the created process."""
    if not loopback_port_available(port):
        raise LocalMongoError(f"Secure MongoDB port {port} is occupied")
    path, admin_secret, app_secret, fresh = prepare_secure_profile(root, device_id)
    keyfile = Path(root) / "mongo-internal.key"
    logpath = Path(root) / "secure-mongod.log"
    try:
        proc = subprocess.Popen([
            mongod, "--dbpath", str(path), "--bind_ip", "127.0.0.1",
            "--port", str(port), "--replSet", REPLICA,
            "--auth", "--keyFile", str(keyfile),
            "--logpath", str(logpath), "--logappend",
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise LocalMongoError("Failed to launch authenticated MongoDB") from exc
    anonymous = MongoClient(
        f"mongodb://127.0.0.1:{port}/?directConnection=true",
        serverSelectionTimeoutMS=500, connectTimeoutMS=500)
    try:
        _wait_ping(anonymous, proc)
        if fresh:
            uri = bootstrap_first_users(
                anonymous, replica_set=REPLICA, database=DATABASE,
                admin_password=admin_secret, app_password=app_secret,
                member_host=f"localhost:{port}",
            )
        else:
            uri = authenticated_uri(port, DATABASE, app_secret, REPLICA)
        verify_authenticated_access(uri, expected_replica=REPLICA, database=DATABASE)
        authenticated = MongoClient(uri, serverSelectionTimeoutMS=3000)
        try:
            check_transactions(authenticated, DATABASE)
        finally:
            authenticated.close()
        return proc, uri
    except Exception:
        stop_owned_process(proc)
        raise
    finally:
        anonymous.close()

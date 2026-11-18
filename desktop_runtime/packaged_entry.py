"""Gate 5B: bundled FastAPI runtime, gated to an EXISTING authenticated Mongo.

This entry NEVER starts mongod, migrates data or contacts Atlas. It is an
experimental process managed by the Tauri shell. A native credential store and
a separately provisioned local secure replica set are prerequisites.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys

DB_PORT = 27028
API_PORT = 18085
DB_NAME = "gestionale_desktop"
REPLICA = "rsGestionaleDesktopSecure"
SERVICE_HTTP = "GestionaleTabacchiDesktopHTTPPreview"


class SidecarPreflightError(RuntimeError):
    pass


def bundle_root() -> Path:
    # PyInstaller sets __file__ for bundled modules under sys._MEIPASS;
    # backend.server expects ../frontend/build relative to backend/__file__.
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def preflight_assets(root: Path) -> dict:
    index = root / "frontend" / "build" / "index.html"
    if not index.is_file() or not (root / "frontend" / "build" / "static").is_dir():
        raise SidecarPreflightError("Bundled React production build is missing")
    if importlib.util.find_spec("backend.server") is None:
        raise SidecarPreflightError("Bundled FastAPI backend is missing")
    if importlib.util.find_spec("uvicorn") is None:
        raise SidecarPreflightError("Uvicorn dependency missing")
    return {"assets": "ok", "backend_import": "ok", "production_writes": False}


def prepare_existing_secure_mongo(root: Path, device_id: str) -> str:
    """Read existing secrets from native keychain; NEVER create/migrate Mongo."""
    from .secure_runtime import REPLICA as SECURE_REPLICA
    from .secure_runtime import _native_store
    from .mongo_auth import authenticated_uri, verify_authenticated_access
    from .local_mongo import loopback_port_available

    if SECURE_REPLICA != REPLICA:
        raise SidecarPreflightError("Replica-set contract mismatch")
    if not (root / "secure-mongo-data").is_dir() or (root / "mongo-data").exists():
        raise SidecarPreflightError("Authenticated desktop MongoDB must be provisioned separately")
    # A populated port is mandatory. We only connect to a known authenticated
    # replica set. Do not start, stop, or attach to an unknown Mongo process.
    if loopback_port_available(DB_PORT):
        raise SidecarPreflightError("Authenticated desktop MongoDB is not running")
    store = _native_store()
    password = store.get_password("GestionaleTabacchiDesktopMongoPreview", f"{device_id}/api")
    if not password:
        raise SidecarPreflightError("Native keychain has no desktop database credentials")
    uri = authenticated_uri(DB_PORT, DB_NAME, password, REPLICA)
    verify_authenticated_access(uri, expected_replica=REPLICA, database=DB_NAME)
    return uri


def prepare_http_auth(device_id: str) -> tuple[str, str]:
    from .secure_runtime import _native_store
    import secrets

    store = _native_store()
    account = f"{device_id}/http-api"
    password = store.get_password(SERVICE_HTTP, account)
    if password is None:
        # A dedicated access password, not the MongoDB login.
        password = secrets.token_urlsafe(42)
        store.set_password(SERVICE_HTTP, account, password)
    if not isinstance(password, str) or len(password) < 32:
        raise SidecarPreflightError("Invalid HTTP credential in native keychain")
    return "desktop", password


def run_service() -> int:
    from .local_mongo import app_data_dir, loopback_port_available
    from .run_local import device_identity

    # Do not trust an inherited data-directory override or remote DB settings.
    os.environ.pop("GESTIONALE_DESKTOP_DATA_DIR", None)
    root = app_data_dir()
    if root.is_symlink() or not root.is_dir():
        raise SidecarPreflightError("Existing desktop user data directory required")
    if not loopback_port_available(API_PORT):
        raise SidecarPreflightError("FastAPI port already occupied; refusing to attach")
    preflight_assets(bundle_root())
    device = device_identity(root)
    mongo_uri = prepare_existing_secure_mongo(root, device)
    http_user, http_password = prepare_http_auth(device)
    os.environ.update({
        "MONGO_URL": mongo_uri, "DB_NAME": DB_NAME,
        "ENVIRONMENT": "local",
        "GESTIONALE_DESKTOP_STRICT_MODE": "1",
        "GESTIONALE_DESKTOP_DEVICE_ID": device,
        "APP_USERNAME": http_user, "APP_PASSWORD": http_password,
        "AGENT_INTEGRATION_ENABLED": "false",
        "ENABLE_LEGACY_EXCEL_IMPORT": "false",
        "AUTO_ORDER_3_1_ENABLED": "0",
        "CORS_ORIGINS": f"http://127.0.0.1:{API_PORT}",
    })
    # Import *after* all fail-closed config is set.
    from backend.server import app
    import uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=API_PORT,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    server.run()
    return 0 if server.started else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Experimental secure-local FastAPI sidecar")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--self-test", action="store_true")
    choice.add_argument("--serve", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            preflight_assets(bundle_root())
            print("DESKTOP SIDECAR ASSETS PASS — no network, no DB access")
            return 0
        return run_service()
    except Exception as exc:
        # Never log mongo URI, OS keychain credentials, or exception payload.
        print(f"Sidecar startup blocked ({type(exc).__name__}). Check local secure preflight.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

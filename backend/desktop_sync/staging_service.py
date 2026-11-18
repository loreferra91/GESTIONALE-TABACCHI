"""Independent opt-in staging API. Not mounted in backend.server or Render.

Run only in a dedicated test environment with explicit staging-only MongoDB.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import os
import secrets
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient

from .staging_receiver import StagingConflict, StagingReceiver, canonical_uuid


@dataclass(frozen=True)
class StagingConfig:
    mongo_url: str
    database: str
    workspace_id: str
    device_id: str
    token: str

    @classmethod
    def from_env(cls) -> "StagingConfig":
        if os.environ.get("DESKTOP_SYNC_STAGING_ENABLED") != "1":
            raise RuntimeError("Staging service disabled: explicit opt-in required")
        url = os.environ.get("DESKTOP_SYNC_STAGING_MONGO_URL", "")
        database = os.environ.get("DESKTOP_SYNC_STAGING_DB", "")
        workspace_id = canonical_uuid(os.environ.get("DESKTOP_SYNC_STAGING_WORKSPACE_ID"))
        device_id = canonical_uuid(os.environ.get("DESKTOP_SYNC_STAGING_DEVICE_ID"))
        token = os.environ.get("DESKTOP_SYNC_STAGING_TOKEN", "")
        if len(token) < 32:
            raise RuntimeError("Staging token must be at least 32 characters")
        if not database.startswith("desktop_sync_staging_") or database == os.environ.get("DB_NAME"):
            raise RuntimeError("Staging must use a separate namespaced database")
        parsed = urlsplit(url)
        if parsed.scheme not in {"mongodb", "mongodb+srv"} or not parsed.hostname:
            raise RuntimeError("Invalid staging MongoDB URI")
        if parsed.hostname not in {"localhost", "127.0.0.1"} and os.environ.get("DESKTOP_SYNC_STAGING_ALLOW_REMOTE") != "1":
            raise RuntimeError("Remote staging MongoDB requires explicit opt-in")
        if parsed.hostname not in {"localhost", "127.0.0.1"} and "tls=false" in parsed.query.lower():
            raise RuntimeError("Remote MongoDB TLS cannot be disabled")
        return cls(url, database, workspace_id, device_id, token)


def build_app(*, receiver: StagingReceiver | None = None, token: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        mongo_client = None
        try:
            if receiver is None:
                cfg = StagingConfig.from_env()
                mongo_client = AsyncIOMotorClient(cfg.mongo_url, serverSelectionTimeoutMS=4000)
                active = StagingReceiver(mongo_client[cfg.database], mongo_client,
                                         workspace_id=cfg.workspace_id, device_id=cfg.device_id)
                secret = cfg.token
            else:
                active = receiver
                secret = token
            if not isinstance(secret, str) or len(secret) < 32:
                raise RuntimeError("Missing staging auth token")
            await active.prepare()
            app.state.receiver = active
            app.state.secret = secret
            yield
        finally:
            if mongo_client:
                mongo_client.close()

    api = FastAPI(title="Desktop Sync Staging Receipt API", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    def authorize(request: Request, supplied_device: str | None, supplied_token: str | None) -> StagingReceiver:
        expected = request.app.state.receiver
        if not supplied_device or not supplied_token or not (
            secrets.compare_digest(supplied_device, expected.device_id) and
            secrets.compare_digest(supplied_token, request.app.state.secret)
        ):
            raise HTTPException(401, "Staging authentication required")
        return expected

    @api.post("/staging/sync/v1/push")
    async def push(request: Request,
                   x_desktop_device_id: str | None = Header(default=None),
                   x_staging_sync_key: str | None = Header(default=None)):
        active = authorize(request, x_desktop_device_id, x_staging_sync_key)
        if int(request.headers.get("content-length", "0") or "0") > 32768:
            raise HTTPException(413, "Staging event exceeds 32KB")
        body = await request.body()
        if len(body) > 32768:
            raise HTTPException(413, "Staging event exceeds 32KB")
        try:
            raw = json.loads(body)
            result = await active.stage(raw)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(422, f"Invalid staging event: {exc}") from exc
        except StagingConflict as exc:
            return JSONResponse(status_code=409, content={
                "status": "CONFLICT", "reason": exc.reason, "business_applied": False,
            }, headers={"Cache-Control": "no-store"})
        return JSONResponse(status_code=200 if result["replayed"] else 202, content=result,
                            headers={"Cache-Control": "no-store"})

    @api.get("/staging/sync/v1/receipts/{operation_id}")
    async def receipt(operation_id: str, request: Request,
                      x_desktop_device_id: str | None = Header(default=None),
                      x_staging_sync_key: str | None = Header(default=None)):
        active = authorize(request, x_desktop_device_id, x_staging_sync_key)
        try:
            found = await active.receipt(operation_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if found is None:
            raise HTTPException(404, "Unknown staging receipt")
        return JSONResponse(content=found, headers={"Cache-Control": "no-store"})

    @api.get("/staging/sync/v1/status")
    async def status(request: Request,
                     x_desktop_device_id: str | None = Header(default=None),
                     x_staging_sync_key: str | None = Header(default=None)):
        active = authorize(request, x_desktop_device_id, x_staging_sync_key)
        return JSONResponse(content=await active.status(), headers={"Cache-Control": "no-store"})

    return api


app = build_app()

"""Least-privilege, read-only API for the external Gestionale Supervisor."""
from collections import defaultdict, deque
from datetime import datetime, timezone
import asyncio
import hashlib
import logging
import os
import secrets
import time
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query, Request


def create_agent_integration_router(*, get_database):
    router = APIRouter(prefix="/agent/v1", tags=["agent-read-only"])
    attempts = defaultdict(deque)
    attempts_lock = asyncio.Lock()

    def enabled() -> bool:
        return str(os.environ.get("AGENT_INTEGRATION_ENABLED") or "").lower() in {
            "1", "true", "yes", "on",
        }

    def configured_limit() -> int:
        try:
            return max(1, min(int(os.environ.get("AGENT_INTEGRATION_RATE_LIMIT", "30")), 300))
        except (TypeError, ValueError) as exc:
            raise HTTPException(503, "Integration rate limit is invalid") from exc

    def configured_timeout() -> float:
        try:
            return max(
                0.05,
                min(float(os.environ.get("AGENT_INTEGRATION_TIMEOUT_SECONDS", "3")), 10.0),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(503, "Integration timeout is invalid") from exc

    async def bounded(operation):
        try:
            timeout = configured_timeout()
        except HTTPException:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            raise
        try:
            return await asyncio.wait_for(operation, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, "Integration database query timed out") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "Integration database query failed") from exc

    async def authorize(request: Request) -> None:
        if not enabled():
            raise HTTPException(404, "Integration not enabled")
        configured = os.environ.get("AGENT_INTEGRATION_TOKEN") or ""
        if len(configured) < 32:
            raise HTTPException(503, "Integration authentication unavailable")
        expires_at = str(os.environ.get("AGENT_INTEGRATION_TOKEN_EXPIRES_AT") or "").strip()
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise HTTPException(503, "Integration token expiry is invalid") from exc
            if datetime.now(timezone.utc) >= expiry.astimezone(timezone.utc):
                raise HTTPException(401, "Service credential expired", headers={"WWW-Authenticate": "Bearer"})
        authorization = request.headers.get("Authorization", "")
        scheme, _, supplied = authorization.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(supplied, configured):
            raise HTTPException(401, "Service authentication required", headers={"WWW-Authenticate": "Bearer"})

        limit = configured_limit()
        now = time.monotonic()
        bucket_id = hashlib.sha256(configured.encode("utf-8")).hexdigest()
        async with attempts_lock:
            bucket = attempts[bucket_id]
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(bucket) >= limit:
                raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "60"})
            bucket.append(now)
        logging.info(
            "Agent integration request authorized: method=%s path=%s",
            request.method,
            request.scope["path"],
        )

    def envelope(data: Dict[str, Any], anomalies: List[Dict[str, Any]] | None = None):
        return {
            "classification": "DATO_VERIFICATO",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
            "possible_anomalies": anomalies or [],
            "recommended_actions": [],
            "unauthorized_actions": [
                "write_data", "delete_data", "move_money", "deploy", "run_shell",
            ],
        }

    @router.get("/status")
    async def status(request: Request):
        await authorize(request)
        database = get_database()
        await bounded(database.command("ping"))
        return envelope({"application": "available", "database": "available", "mode": "read_only"})

    @router.get("/inventory")
    async def inventory(request: Request, limit: int = Query(100, ge=1, le=500)):
        await authorize(request)
        database = get_database()
        async def read_inventory():
            return await asyncio.gather(
                database.prodotti.find(
                    {"giacenza_negozio": {"$lt": 0}},
                    {"_id": 0, "codice": 1, "giacenza_negozio": 1, "giacenza_vending": 1},
                ).limit(limit).to_list(limit),
                database.prodotti.count_documents({"giacenza_negozio": {"$lte": 0}}),
                database.prodotti.count_documents({}),
            )
        negative, exhausted, total = await bounded(read_inventory())
        anomalies = [{
            "type": "NEGATIVE_SHOP_STOCK",
            "code": str(row.get("codice") or ""),
            "shop_stock": int(row.get("giacenza_negozio") or 0),
            "vending_stock": int(row.get("giacenza_vending") or 0),
        } for row in negative]
        return envelope({"products": total, "exhausted_shop_products": exhausted}, anomalies)

    @router.get("/vending")
    async def vending(request: Request, limit: int = Query(100, ge=1, le=500)):
        await authorize(request)
        database = get_database()
        async def read_vending():
            return await asyncio.gather(
                database.vending.find(
                    {"$expr": {"$or": [
                        {"$lt": ["$giacenza", 0]},
                        {"$gt": ["$giacenza", "$capacita_max"]},
                    ]}},
                    {"_id": 0, "id": 1, "colonna": 1, "codice": 1,
                     "giacenza": 1, "capacita_max": 1},
                ).limit(limit).to_list(limit),
                database.vending.count_documents({}),
            )
        rows, total = await bounded(read_vending())
        anomalies = [{
            "type": "VENDING_STOCK_OUT_OF_BOUNDS",
            "column": str(row.get("colonna") or ""),
            "code": str(row.get("codice") or ""),
            "stock": int(row.get("giacenza") or 0),
            "capacity": int(row.get("capacita_max") or 0),
        } for row in rows]
        return envelope({"columns": total}, anomalies)

    @router.get("/orders")
    async def orders(request: Request, limit: int = Query(100, ge=1, le=500)):
        await authorize(request)
        database = get_database()
        async def read_orders():
            return await asyncio.gather(
                database.storico_ordini.find(
                    {"annullato": {"$ne": True}, "smart_venue_stato": "DA_INSERIRE"},
                    {"_id": 0, "id": 1, "codice": 1, "quantita": 1,
                     "created_at": 1, "smart_venue_stato": 1},
                ).sort("created_at", -1).limit(limit).to_list(limit),
                database.storico_ordini.count_documents({"annullato": {"$ne": True}}),
            )
        pending, active = await bounded(read_orders())
        anomalies = [{
            "type": "SMART_VENUE_CONFIRMATION_PENDING",
            "order_id": str(row.get("id") or ""),
            "code": str(row.get("codice") or ""),
            "quantity": int(row.get("quantita") or 0),
            "created_at": row.get("created_at"),
        } for row in pending]
        return envelope({"active_rows": active, "pending_smart_venue": len(pending)}, anomalies)

    return router

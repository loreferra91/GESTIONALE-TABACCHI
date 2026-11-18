"""Explicitly gated staging receipt sender. Never automatically syncs business data.

It sends immutable local outbox records to a disposable loopback staging
service, then marks local status STAGED (NOT ACKNOWLEDGED). Staging does not
modify cloud stock, sales, accounting or cash.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import os
from urllib.parse import urlsplit

import httpx
from motor.motor_asyncio import AsyncIOMotorClient

from .staging_receiver import validate_event, canonical_uuid


_FIELDS = ("operation_id", "device_id", "entity_kind", "entity_id",
           "kind", "payload_hash", "event_payload", "schema_version")


def validate_staging_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
        or not parsed.port or parsed.username or parsed.password
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Only a clean loopback HTTP staging receiver URL is allowed")
    return f"http://{parsed.hostname}:{parsed.port}"


def check_source_url(url: str, database_name: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost"}
        or not parsed.port or parsed.username or parsed.password
        or database_name != "gestionale_desktop"):
        raise ValueError("Only the dedicated local desktop MongoDB database may be read")


async def stage_pending(db, http: httpx.AsyncClient, *, device: str,
                        token: str, dry_run: bool = True, limit: int = 10) -> dict:
    device = canonical_uuid(device)
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("Missing secure staging token")
    if not 1 <= limit <= 25:
        raise ValueError("Limit must be between 1 and 25")
    records = await db.desktop_outbox.find(
        {"device_id": device, "status": "PENDING"},
    ).sort("_id", 1).limit(limit).to_list(limit)

    result = {"considered": len(records), "staged": 0, "dry_run": dry_run,
              "failures": [], "business_applied": False}
    for row in records:
        identifier = row.get("operation_id")
        try:
            wire = {field: row[field] for field in _FIELDS}
            validate_event(wire, device)
        except (KeyError, TypeError, ValueError) as exc:
            result["failures"].append({"operation_id": identifier, "reason": f"invalid outbox: {exc}"})
            # Fail closed: do not advance invalid records.
            continue

        if dry_run:
            continue
        try:
            response = await http.post(
                "/staging/sync/v1/push", json=wire,
                headers={"X-Desktop-Device-ID": device, "X-Staging-Sync-Key": token},
                timeout=10,
            )
            if response.status_code not in (200, 202):
                result["failures"].append({"operation_id": identifier,
                                           "reason": f"staging HTTP {response.status_code}"})
                continue
            receipt = response.json()
            if (receipt.get("operation_id") != identifier or
                receipt.get("status") != "STAGED" or
                receipt.get("business_applied") is not False):
                result["failures"].append({"operation_id": identifier,
                                           "reason": "untrusted or inconsistent staging receipt"})
                continue
            # A single-document CAS is atomic. Never change the actual sale,
            # inventory or local outbox payload; do not mark ACKNOWLEDGED.
            written = await db.desktop_outbox.update_one(
                {"operation_id": identifier, "device_id": device,
                 "payload_hash": row["payload_hash"], "status": "PENDING"},
                {"$set": {"status": "STAGED",
                          "staging_receipt_at": datetime.now(timezone.utc)}},
            )
            if written.modified_count != 1:
                result["failures"].append({"operation_id": identifier,
                                           "reason": "local state changed before receipt commit"})
            else:
                result["staged"] += 1
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            result["failures"].append({"operation_id": identifier,
                                       "reason": f"transport or response error: {type(exc).__name__}"})
    return result


async def _run(args) -> int:
    source_url = os.environ.get("DESKTOP_LOCAL_MONGO_URL", "")
    check_source_url(source_url, os.environ.get("DESKTOP_LOCAL_DB_NAME", ""))
    device = canonical_uuid(os.environ.get("DESKTOP_SYNC_STAGING_DEVICE_ID"))
    token = os.environ.get("DESKTOP_SYNC_STAGING_TOKEN", "")
    base_url = validate_staging_url(os.environ.get("DESKTOP_SYNC_STAGING_BASE_URL", ""))
    if args.send and os.environ.get("DESKTOP_SYNC_STAGING_ALLOW_SEND") != "1":
        raise RuntimeError("To send, explicitly set DESKTOP_SYNC_STAGING_ALLOW_SEND=1")
    client = AsyncIOMotorClient(source_url, serverSelectionTimeoutMS=3000)
    try:
        hello = await client.admin.command("hello")
        if hello.get("setName") != "rsGestionaleDesktop" or not hello.get("isWritablePrimary"):
            raise RuntimeError("Local desktop replica set not writable or unexpected")
        async with httpx.AsyncClient(base_url=base_url) as http:
            summary = await stage_pending(client["gestionale_desktop"], http,
                device=device, token=token, dry_run=not args.send, limit=args.limit)
        print(f"STAGING ONLY: {summary}")
        return 0 if not summary["failures"] else 2
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Desktop outbox staging-only diagnostic sender")
    parser.add_argument("--send", action="store_true", help="Explicitly transmit to isolated loopback staging")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        print(f"Sender blocked: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

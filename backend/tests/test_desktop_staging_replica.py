"""Real staging-only receipt and API integration against disposable rsDesktopCert."""
import asyncio
import os
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import HTTPException
import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from backend.desktop_sync.local_outbox import payload_hash
from backend.desktop_sync.staging_receiver import StagingReceiver, StagingConflict
from backend.desktop_sync.staging_service import build_app
from backend.desktop_sync.staging_sender import stage_pending


def _safe_target() -> str:
    url = os.environ.get("CERT_MONGO_URL", "")
    if os.environ.get("CERT_DESKTOP_ALLOW_TEST") != "1" or not url:
        pytest.skip("requires explicitly enabled isolated MongoDB replica set")
    parsed = urlsplit(url)
    if parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port:
        pytest.fail("refusing non-loopback certification target")
    if parsed.username or parsed.password:
        pytest.fail("refusing authenticated certification target")
    return url


def sample_event(device):
    core = {
        "data": "2026-11-18", "codice": "TEST001", "quantita": 2,
        "importo_cents": 1200, "canale": "NEGOZIO",
        "pagamento": "CONTANTI", "colonna": None,
    }
    sale_id = str(uuid4())
    return {
        "operation_id": str(uuid4()), "device_id": device,
        "entity_kind": "sale", "entity_id": sale_id,
        "kind": "sale.create", "schema_version": 1,
        "payload_hash": payload_hash(core),
        "event_payload": {**core, "sale_id": sale_id, "created_at": "2026-11-18T11:00:00+00:00"},
    }


async def scenario(url):
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=4000)
    db_name = "desktop_sync_staging_cert_" + uuid4().hex
    sender_db_name = "desktop_sender_cert_" + uuid4().hex
    db = client[db_name]
    sender_db = client[sender_db_name]
    try:
        hello = await client.admin.command("hello")
        if hello.get("setName") != "rsDesktopCert" or not hello.get("isWritablePrimary"):
            pytest.fail("Expected rsDesktopCert; do not run on another replica set")
        await db.sync_metadata.insert_one({"_id": "purpose", "value": "desktop-sync-staging"})
        device, workspace, secret = str(uuid4()), str(uuid4()), "T" * 48
        receiver = StagingReceiver(db, client, workspace_id=workspace, device_id=device)
        app = build_app(receiver=receiver, token=secret)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as api:
                headers = {"X-Desktop-Device-ID": device, "X-Staging-Sync-Key": secret}
                event = sample_event(device)
                endpoint = "/staging/sync/v1/push"
                denied = await api.post(endpoint, json=event)
                assert denied.status_code == 401
                fresh = await api.post(endpoint, headers=headers, json=event)
                assert fresh.status_code == 202, fresh.text
                assert fresh.json() == {
                    "operation_id": event["operation_id"], "status": "STAGED",
                    "business_applied": False, "replayed": False,
                }
                replay = await api.post(endpoint, headers=headers, json=event)
                assert replay.status_code == 200
                assert replay.json()["replayed"] is True

                # Mutation of metadata outside payload_hash must not count as replay.
                metadata_changed = {**event, "event_payload": {
                    **event["event_payload"], "created_at": "2026-11-18T12:01:00+00:00",
                }}
                tampered_replay = await api.post(endpoint, headers=headers, json=metadata_changed)
                assert tampered_replay.status_code == 409
                assert tampered_replay.json()["reason"] == "OPERATION_ID_REUSED_WITH_DIFFERENT_CONTENT"

                edited = {**event, "event_payload": {**event["event_payload"], "quantita": 3}}
                mismatch = await api.post(endpoint, headers=headers, json=edited)
                assert mismatch.status_code == 422  # digest mismatch

                changed_core = {k: v for k, v in edited["event_payload"].items()
                                if k not in {"sale_id", "created_at"}}
                edited["payload_hash"] = payload_hash(changed_core)
                conflicting = await api.post(endpoint, headers=headers, json=edited)
                assert conflicting.status_code == 409
                assert conflicting.json()["reason"] == "OPERATION_ID_REUSED_WITH_DIFFERENT_CONTENT"

                collision = sample_event(device)
                collision["entity_id"] = event["entity_id"]
                collision["event_payload"]["sale_id"] = event["entity_id"]
                other_op = await api.post(endpoint, headers=headers, json=collision)
                assert other_op.status_code == 409
                assert other_op.json()["reason"] == "SALE_ID_ALREADY_USED_BY_ANOTHER_OPERATION"

                receipt = await api.get("/staging/sync/v1/receipts/" + event["operation_id"], headers=headers)
                assert receipt.status_code == 200
                status = await api.get("/staging/sync/v1/status", headers=headers)
                assert status.json() == {"staged": 1, "business_applied": 0, "mode": "RECEIPT_ONLY"}

                assert await db.sync_receipts.count_documents({}) == 1
                assert await db.prodotti.count_documents({}) == 0
                assert await db.vendite.count_documents({}) == 0
                stored = await db.sync_receipts.find_one({})
                assert stored["event_payload"]["importo_cents"] == 1200

                # End-to-end on the isolated replica set: local PENDING -> signed
                # receipt-only receiver -> local STAGED. Never ACKNOWLEDGED.
                sender_event = sample_event(device)
                await sender_db.desktop_outbox.insert_one({**sender_event, "status": "PENDING"})
                result = await stage_pending(sender_db, api, device=device, token=secret, dry_run=False)
                assert result["staged"] == 1, result
                assert not result["failures"]
                local_receipt = await sender_db.desktop_outbox.find_one({
                    "operation_id": sender_event["operation_id"],
                })
                assert local_receipt["status"] == "STAGED"
                assert local_receipt["event_payload"] == sender_event["event_payload"]
                assert await db.sync_receipts.count_documents({}) == 2
                assert await db.prodotti.count_documents({}) == 0
                assert await db.vendite.count_documents({}) == 0
                assert await db.cassa.count_documents({}) == 0
    finally:
        await client.drop_database(db_name)
        await client.drop_database(sender_db_name)
        client.close()


def test_staging_receipt_is_durable_idempotent_and_never_mutates_business_state():
    asyncio.run(scenario(_safe_target()))

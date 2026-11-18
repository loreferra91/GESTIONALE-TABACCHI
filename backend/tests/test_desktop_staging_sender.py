"""Staging sender unit tests: no network or real DB."""
from uuid import uuid4
import httpx
import pytest
from mongomock_motor import AsyncMongoMockClient

from backend.desktop_sync.local_outbox import payload_hash
from backend.desktop_sync.staging_sender import stage_pending, validate_staging_url, check_source_url


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _record(device):
    sale = str(uuid4())
    core = {"data": "2026-11-18", "codice": "TEST001", "quantita": 2,
            "importo_cents": 1200, "canale": "NEGOZIO", "pagamento": "CONTANTI",
            "colonna": None}
    return {
        "operation_id": str(uuid4()), "device_id": device, "entity_kind": "sale",
        "entity_id": sale, "kind": "sale.create", "payload_hash": payload_hash(core),
        "event_payload": {**core, "sale_id": sale, "created_at": "2026-11-18T11:00:00+00:00"},
        "schema_version": 1, "status": "PENDING", "attempts": 0,
    }


def test_sender_refuses_external_targets():
    for url in ("https://prod.example.com", "http://evil.invalid:18086",
                "http://127.0.0.1:18086/other", "http://user:pw@localhost:18086"):
        with pytest.raises(ValueError):
            validate_staging_url(url)
    with pytest.raises(ValueError):
        check_source_url("mongodb://prod.example.com:27017", "gestionale_desktop")
    with pytest.raises(ValueError):
        check_source_url("mongodb://127.0.0.1:27028", "production")
    assert validate_staging_url("http://127.0.0.1:18086") == "http://127.0.0.1:18086"


@pytest.mark.anyio
async def test_sender_dry_run_has_no_transport_or_db_writes():
    device = str(uuid4())
    db = AsyncMongoMockClient()["local_sender_dry"]
    record = _record(device)
    await db.desktop_outbox.insert_one(dict(record))
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(202, json={"operation_id": record["operation_id"],
                                          "status": "STAGED", "business_applied": False})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://localhost") as http:
        result = await stage_pending(db, http, device=device, token="s"*40)
    assert result["considered"] == 1 and result["staged"] == 0
    assert not calls
    assert (await db.desktop_outbox.find_one({"operation_id": record["operation_id"]}))["status"] == "PENDING"


@pytest.mark.anyio
async def test_sender_advances_only_verified_receipt_not_ack():
    device = str(uuid4())
    db = AsyncMongoMockClient()["local_sender_receipt"]
    record = _record(device)
    await db.desktop_outbox.insert_one(dict(record))
    def handler(request):
        assert request.headers["X-Desktop-Device-ID"] == device
        return httpx.Response(202, json={"operation_id": record["operation_id"],
                                          "status": "STAGED", "business_applied": False})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://localhost") as http:
        result = await stage_pending(db, http, device=device, token="s"*40, dry_run=False)
    assert result["staged"] == 1 and not result["failures"]
    row = await db.desktop_outbox.find_one({"operation_id": record["operation_id"]})
    assert row["status"] == "STAGED" and row["payload_hash"] == record["payload_hash"]
    assert row["event_payload"] == record["event_payload"]
    assert await db.vendite.count_documents({}) == 0


@pytest.mark.anyio
async def test_sender_rejects_mismatched_receipt_and_leaves_pending():
    device = str(uuid4())
    db = AsyncMongoMockClient()["local_sender_untrusted"]
    record = _record(device)
    await db.desktop_outbox.insert_one(dict(record))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(202, json={
            "operation_id": str(uuid4()), "status": "STAGED", "business_applied": False})),
        base_url="http://localhost",
    ) as http:
        result = await stage_pending(db, http, device=device, token="s"*40, dry_run=False)
    assert result["staged"] == 0 and len(result["failures"]) == 1
    assert (await db.desktop_outbox.find_one({"operation_id": record["operation_id"]}))["status"] == "PENDING"


@pytest.mark.anyio
async def test_sender_rejects_tampered_outbox_without_network():
    device = str(uuid4())
    db = AsyncMongoMockClient()["local_sender_tampered"]
    record = _record(device)
    record["event_payload"]["quantita"] = 999
    await db.desktop_outbox.insert_one(dict(record))
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(500)),
        base_url="http://localhost",
    ) as http:
        result = await stage_pending(db, http, device=device, token="s"*40, dry_run=False)
    assert result["staged"] == 0 and result["failures"] and not calls

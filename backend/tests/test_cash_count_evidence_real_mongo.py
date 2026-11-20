"""Certify cash-count evidence on a disposable loopback MongoDB replica set.

Tests never touch Atlas, Render, booked cash, stock or live sales.
"""
import asyncio
from datetime import datetime, timezone
import os
from types import SimpleNamespace
from urllib.parse import urlsplit

from motor.motor_asyncio import AsyncIOMotorClient
import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from backend.cash_count_evidence_router import (
    CashCountEvidenceIn,
    create_cash_count_evidence_router,
)


pytestmark = pytest.mark.certification


async def _fixture(fn):
    url = os.environ.get("CERT_MONGO_URL", "")
    if not url:
        pytest.skip("Requires an explicit disposable MongoDB URL")
    parsed = urlsplit(url)
    name = os.environ.get("CERT_DB_NAME", "")
    if (
        parsed.scheme != "mongodb" or
        parsed.hostname not in {"localhost", "127.0.0.1"} or
        not parsed.port or not name.startswith("gestionale_cert_cash_")
    ):
        pytest.fail("Refusing a non-disposable cash certification target")
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=6000)
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName") and hello.get("logicalSessionTimeoutMinutes")
        await client.drop_database(name)
        db = client[name]

        async def transactional(operation):
            async with await client.start_session() as session:
                return await session.with_transaction(operation)

        router = create_cash_count_evidence_router(
            get_database=lambda: db,
            run_transactionally=transactional,
            request_operator=lambda request: "cert-operator",
            enabled=True,
        )
        append = next(
            route.endpoint for route in router.routes
            if route.path == "/contabilita/conteggi-fisici" and "POST" in route.methods
        )
        req = SimpleNamespace(state=SimpleNamespace(authenticated_user="cert-operator"))
        await fn(db, append, req)
    finally:
        await client.drop_database(name)
        client.close()


def _body(key, denominations=None):
    return CashCountEvidenceIn(
        idempotency_key=key,
        observed_at=datetime.now(timezone.utc),
        kind="closing",
        denominations=denominations or {"1": 1, "0.50": 1},
        source_reference="cert-signed-count-not-real",
        note="Disposable certification only",
    )


def test_cash_evidence_concurrent_retries_are_idempotent_and_never_mutate_booked_cash():
    async def scenario(db, append, request):
        body = _body("cert-cash-count-key-0001")
        results = await asyncio.gather(
            *[append(body, request) for _ in range(4)],
            return_exceptions=True,
        )
        assert any(isinstance(r, dict) for r in results), results
        first = await append(body, request)
        replay = await append(body, request)
        assert first["id"] == replay["id"]
        assert first["total_cents"] == 150
        assert replay["replayed"] is True
        assert first["is_financial_baseline"] is False
        assert first["changes_booked_cash"] is False
        assert await db.cash_count_evidence.count_documents({}) == 1
        assert await db.audit_events.count_documents({"azione": "REGISTRATO_CONTEGGIO_FISICO"}) == 1
        for col in ["cassa", "vendite", "prodotti", "accounting_baselines"]:
            assert await db[col].count_documents({}) == 0

        altered = _body("cert-cash-count-key-0001", {"20": 1})
        with pytest.raises(HTTPException) as error:
            await append(altered, request)
        assert error.value.status_code == 409
        assert await db.cash_count_evidence.count_documents({}) == 1
    asyncio.run(_fixture(scenario))


def test_cash_count_audit_conflict_aborts_observation_atomically():
    async def scenario(db, append, request):
        key = "cert-cash-count-failure-001"
        # Simulate audit insert failing after the evidence insert.
        await db.audit_events.insert_one({
            "_id": f"cash-count-audit:{key}", "id": "collision",
        })
        with pytest.raises(DuplicateKeyError):
            await append(_body(key), request)
        assert await db.cash_count_evidence.count_documents({}) == 0
        assert await db.audit_events.count_documents({}) == 1
        for col in ["cassa", "vendite", "prodotti", "accounting_baselines"]:
            assert await db[col].count_documents({}) == 0

        await db.audit_events.delete_one({"_id": f"cash-count-audit:{key}"})
        recorded = await append(_body(key), request)
        assert recorded["replayed"] is False
        assert await db.cash_count_evidence.count_documents({}) == 1
        assert await db.audit_events.count_documents({}) == 1
    asyncio.run(_fixture(scenario))

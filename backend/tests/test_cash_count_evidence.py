"""Staging-only physical cash count: evidence, not an accounting adjustment."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient
from pydantic import ValidationError

from backend.cash_count_evidence_router import (
    CashCountEvidenceIn, create_cash_count_evidence_router,
)


def _body(key=None, denominations=None, observed_at=None):
    return {
        "idempotency_key": key or str(uuid4()),
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
        "kind": "closing",
        "denominations": denominations or {"1": 1, "0.50": 2, "0.01": 3},
        "source_reference": "verifica-fisica-firma",
        "note": "Synthetic unit test",
    }


def _app(database, enabled=True):
    app = FastAPI()
    router = create_cash_count_evidence_router(
        get_database=lambda: database,
        run_transactionally=lambda operation: operation(None),
        request_operator=lambda request: "operator-test",
        enabled=enabled,
    )
    app.include_router(router)
    return TestClient(app)


def test_cash_count_evidence_writes_only_append_only_observation_and_audit():
    database = AsyncMongoMockClient()["physical_cert"]
    client = _app(database)
    request = _body()
    response = client.post("/contabilita/conteggi-fisici", json=request)
    assert response.status_code == 201
    result = response.json()
    assert result["replayed"] is False
    assert result["total_cents"] == 203
    assert result["is_financial_baseline"] is False
    assert result["changes_booked_cash"] is False
    assert result["operator"] == "operator-test"
    listing = client.get("/contabilita/conteggi-fisici")
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["total_cents"] == 203

    async def check():
        assert await database.cash_count_evidence.count_documents({}) == 1
        assert await database.audit_events.count_documents({
            "azione": "REGISTRATO_CONTEGGIO_FISICO"
        }) == 1
        for name in ("cassa", "vendite", "prodotti", "accounting_baselines"):
            assert await database[name].count_documents({}) == 0
    asyncio.run(check())


def test_cash_count_retry_is_idempotent_and_mismatched_payload_is_blocked():
    database = AsyncMongoMockClient()["physical_cert_idempotency"]
    client = _app(database)
    request = _body()
    first = client.post("/contabilita/conteggi-fisici", json=request)
    replay = client.post("/contabilita/conteggi-fisici", json=request)
    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["replayed"] is True
    assert first.json()["id"] == replay.json()["id"]
    altered = {**request, "denominations": {"50": 1}}
    assert client.post("/contabilita/conteggi-fisici", json=altered).status_code == 409

    async def check():
        assert await database.cash_count_evidence.count_documents({}) == 1
        assert await database.audit_events.count_documents({}) == 1
    asyncio.run(check())


@pytest.mark.parametrize("change", [
    {"denominations": {"1": True}},
    {"denominations": {"1": -1}},
    {"denominations": {"1": 1.2}},
    {"denominations": {"1": 10001}},
    {"denominations": {"1000": 1}},
    {"denominations": {}},
    {"observed_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()},
    {"observed_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
    {"observed_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()},
    {"idempotency_key": "insecure key?"},
    {"source_reference": "       "},
])
def test_invalid_or_unverified_cash_evidence_is_rejected(change):
    request = {**_body(), **change}
    with pytest.raises(ValidationError):
        CashCountEvidenceIn.model_validate(request)


def test_cash_count_router_is_not_available_in_production():
    database = AsyncMongoMockClient()["production_cert_disabled"]
    client = _app(database, enabled=False)
    assert client.post("/contabilita/conteggi-fisici", json=_body()).status_code == 404
    assert client.get("/contabilita/conteggi-fisici").status_code == 404

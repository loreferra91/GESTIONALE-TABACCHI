"""Certify real sale transactions on a disposable, loopback-only MongoDB replica set.

NEVER point CERT_MONGO_URL at Atlas, staging or production. Every test drops
CERT_DB_NAME both before and after its run. This module is not part of the
default pytest suite unless an explicit disposable target is configured.
"""
import asyncio
import os
from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from backend import server


pytestmark = pytest.mark.certification


def _cert_target():
    url = os.environ.get("CERT_MONGO_URL", "")
    if not url:
        pytest.skip("Requires disposable CERT_MONGO_URL")
    parsed = urlsplit(url)
    if parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("CERT_MONGO_URL must use a loopback-only MongoDB service")
    db_name = os.environ.get("CERT_DB_NAME", "gestionale_cert_sales_staging")
    if not db_name.startswith("gestionale_cert_sales_"):
        pytest.fail("Refusing any non-disposable CERT_DB_NAME")
    return url, db_name


async def _with_db(monkeypatch, fn, *, available=12):
    url, name = _cert_target()
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
    database = client[name]
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName"), "Transactions require a real replica set"
        await client.drop_database(name)
        await database.prodotti.create_index("codice", unique=True)
        await database.vendite.create_index("id", unique=True)
        await database.vendite_bulk_imports.create_index("idempotency_key", unique=True)
        await database.audit_events.create_index("id", unique=True)
        await database.prodotti.insert_one({
            "id": "cert-product-1", "codice": "CERT-P1",
            "descrizione": "Synthetic certification product", "prezzo": 5,
            "giacenza_negozio": available, "venduti_negozio": 0,
            "giacenza_vending": 0, "venduti_vending": 0,
        })
        monkeypatch.setattr(server, "db", database)

        async def synthetic_backup(*_args, **_kwargs):
            return {"id": "cert-no-real-backup"}
        # Bulk backup is outside the stock transaction; keep this isolated
        # test focused on the real inventory/sales transaction.
        monkeypatch.setattr(server, "create_backup_snapshot", synthetic_backup)
        await fn(database)
    finally:
        await client.drop_database(name)
        client.close()


def test_bulk_concurrent_retry_and_undo_are_exactly_once(monkeypatch):
    async def scenario(db):
        body = server.BulkVenditaIn(
            idempotency_key="cert-bulk-same-key-001",
            righe=[
                {"data": "2026-11-19", "codice": "CERT-P1", "quantita": 2, "importo": 10},
                {"data": "2026-11-19", "codice": "CERT-P1", "quantita": 1, "importo": 5},
            ],
        )
        attempts = await asyncio.gather(
            *(server.bulk_vendite(body) for _ in range(4)),
            return_exceptions=True,
        )
        accepted = [value for value in attempts if isinstance(value, dict)]
        assert accepted, attempts
        # A concurrent request may receive a transient conflict. A retry
        # using the SAME key must always resolve to the committed batch.
        winner = await server.bulk_vendite(body)
        assert all(value == winner for value in accepted)
        assert await db.vendite.count_documents({"sorgente": "BULK"}) == 2
        product = await db.prodotti.find_one({"codice": "CERT-P1"})
        assert product["giacenza_negozio"] == 9
        assert product["venduti_negozio"] == 3

        undo_attempts = await asyncio.gather(
            server.annulla_bulk_vendite(winner["batch_id"]),
            server.annulla_bulk_vendite(winner["batch_id"]),
            return_exceptions=True,
        )
        successful = [value for value in undo_attempts if isinstance(value, dict)]
        assert len(successful) == 1, undo_attempts
        assert successful[0]["rimossi"] == 2
        assert await db.vendite.count_documents({"sorgente": "BULK"}) == 0
        assert await db.audit_events.count_documents({
            "azione": "annulla-vendita-bulk", "batch_id": winner["batch_id"]
        }) == 2
        product = await db.prodotti.find_one({"codice": "CERT-P1"})
        assert product["giacenza_negozio"] == 12
        assert product["venduti_negozio"] == 0
        batch = await db.vendite_bulk_imports.find_one({"id": winner["batch_id"]})
        assert batch["status"] == "annulled"
        with pytest.raises(HTTPException) as error:
            await server.bulk_vendite(body)
        assert error.value.status_code == 409

    asyncio.run(_with_db(monkeypatch, scenario))


def test_distinct_bulk_requests_cannot_sell_same_stock_twice(monkeypatch):
    async def scenario(db):
        def payload(key):
            return server.BulkVenditaIn(
                idempotency_key=key,
                righe=[{"data": "2026-11-19", "codice": "CERT-P1",
                        "quantita": 7, "importo": 35}],
            )
        attempts = await asyncio.gather(
            server.bulk_vendite(payload("cert-bulk-stock-key-A")),
            server.bulk_vendite(payload("cert-bulk-stock-key-B")),
            return_exceptions=True,
        )
        winners = [value for value in attempts if isinstance(value, dict)]
        assert len(winners) == 1, attempts
        product = await db.prodotti.find_one({"codice": "CERT-P1"})
        assert product["giacenza_negozio"] == 3
        assert product["venduti_negozio"] == 7
        assert await db.vendite.count_documents({"sorgente": "BULK"}) == 1
        assert await db.vendite_bulk_imports.count_documents({"status": "active"}) == 1

    asyncio.run(_with_db(monkeypatch, scenario, available=10))


def test_mid_batch_duplicate_key_aborts_every_inventory_write(monkeypatch):
    async def scenario(db):
        # An artificial uniqueness constraint forces failure on row 2,
        # after row 1 has already changed stock inside the transaction.
        await db.vendite.create_index("codice", unique=True)
        body = server.BulkVenditaIn(
            idempotency_key="cert-bulk-forced-abort-001",
            righe=[
                {"data": "2026-11-19", "codice": "CERT-P1", "quantita": 2, "importo": 10},
                {"data": "2026-11-19", "codice": "CERT-P1", "quantita": 1, "importo": 5},
            ],
        )
        with pytest.raises((DuplicateKeyError, HTTPException)) as error:
            await server.bulk_vendite(body)
        # The public router may translate duplicate-key errors into HTTP 409.
        if isinstance(error.value, HTTPException):
            assert error.value.status_code == 409
        product = await db.prodotti.find_one({"codice": "CERT-P1"})
        assert product["giacenza_negozio"] == 12
        assert product["venduti_negozio"] == 0
        assert await db.vendite.count_documents({}) == 0
        assert await db.vendite_bulk_imports.count_documents({}) == 0

    asyncio.run(_with_db(monkeypatch, scenario))


def test_manual_sale_replay_conflicts_without_new_stock_movement(monkeypatch):
    async def scenario(db):
        body = server.VenditaIn(
            data="2026-11-19", codice="CERT-P1", quantita=2, importo=10,
        )
        first = await server.add_vendita(body, "cert-manual-sale-key-001")
        replay = await server.add_vendita(body, "cert-manual-sale-key-001")
        assert first["id"] == replay["id"]
        changed = body.model_copy(update={"quantita": 3, "importo": 15})
        with pytest.raises(HTTPException) as error:
            await server.add_vendita(changed, "cert-manual-sale-key-001")
        assert error.value.status_code == 409
        product = await db.prodotti.find_one({"codice": "CERT-P1"})
        assert product["giacenza_negozio"] == 10
        assert product["venduti_negozio"] == 2
        assert await db.vendite.count_documents({}) == 1
        undone = await server.annulla_vendita_manuale(first["id"])
        assert undone["rimossi"] == 1
        assert await db.vendite.count_documents({}) == 0
        event = await db.audit_events.find_one({
            "azione": "annulla-vendita-manuale",
            "entita_id": first["id"],
        })
        assert event["prima"]["id"] == first["id"]
        assert event["idempotency_key"] == "cert-manual-sale-key-001"
        with pytest.raises(HTTPException) as revoked:
            await server.add_vendita(body, "cert-manual-sale-key-001")
        assert revoked.value.status_code == 409
        restored = await db.prodotti.find_one({"codice": "CERT-P1"})
        assert restored["giacenza_negozio"] == 12
        assert restored["venduti_negozio"] == 0

    asyncio.run(_with_db(monkeypatch, scenario))

import asyncio
import os

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

from backend import server


def test_manual_order_replay_is_once_only_and_payload_mismatch_conflicts(monkeypatch):
    database = AsyncMongoMockClient()["manual_order_idempotency_test"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.storico_ordini.create_index("idempotency_key", unique=True, sparse=True)
        await database.prodotti.insert_one({
            "codice": "P1", "descrizione": "Prodotto", "giacenza_negozio": 10,
            "acquistati": 10,
        })
        payload = server.OrdineIn(
            data="2026-10-27", codice="P1", descrizione="Prodotto",
            quantita=3, prezzo=5, idempotency_key="manual-idempotency-001",
        )
        first = await server.add_ordine(payload)
        replay = await server.add_ordine(payload)
        with pytest.raises(HTTPException) as mismatch:
            await server.add_ordine(payload.model_copy(update={"quantita": 4}))
        product = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        count = await database.storico_ordini.count_documents({})
        return first, replay, mismatch.value, product, count

    first, replay, mismatch, product, count = asyncio.run(scenario())
    assert first["gia_registrato"] is False
    assert replay["gia_registrato"] is True
    assert replay["id"] == first["id"]
    assert mismatch.status_code == 409
    assert product["giacenza_negozio"] == 13
    assert product["acquistati"] == 13
    assert count == 1


def test_manual_order_rolls_back_history_when_product_is_missing(monkeypatch):
    database = AsyncMongoMockClient()["manual_order_rollback_test"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.storico_ordini.create_index("idempotency_key", unique=True, sparse=True)
        with pytest.raises(HTTPException) as error:
            await server.add_ordine(server.OrdineIn(
                data="2026-10-27", codice="MISSING", descrizione="Missing",
                quantita=1, prezzo=1, idempotency_key="manual-idempotency-rollback",
            ))
        return error.value, await database.storico_ordini.count_documents({})

    error, count = asyncio.run(scenario())
    assert error.status_code == 409
    assert count == 0


def test_vending_single_restock_replay_is_once_only_and_mismatch_conflicts(monkeypatch):
    database = AsyncMongoMockClient()["vending_idempotency_test"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.vending_operations.create_index("idempotency_key", unique=True)
        await database.parametri.insert_one({
            "nome": "SCORTA_MINIMA_NEGOZIO_VENDING", "valore": 2,
        })
        await database.vending.insert_one({
            "id": "V1", "colonna": "A01", "codice": "P1",
            "giacenza": 1, "capacita_max": 5, "soglia_minima": 2,
        })
        await database.prodotti.insert_one({
            "codice": "P1", "giacenza_negozio": 10, "giacenza_vending": 1,
        })
        body = {"quantita": 2, "idempotency_key": "vending-idempotency-001"}
        first = await server.ricarica_vending("V1", body)
        replay = await server.ricarica_vending("V1", body)
        with pytest.raises(HTTPException) as mismatch:
            await server.ricarica_vending("V1", {**body, "quantita": 1})
        column = await database.vending.find_one({"id": "V1"}, {"_id": 0})
        product = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        return first, replay, mismatch.value, column, product

    first, replay, mismatch, column, product = asyncio.run(scenario())
    assert first["gia_registrato"] is False
    assert replay["gia_registrato"] is True
    assert mismatch.status_code == 409
    assert column["giacenza"] == 3
    assert product["giacenza_negozio"] == 8
    assert product["giacenza_vending"] == 3

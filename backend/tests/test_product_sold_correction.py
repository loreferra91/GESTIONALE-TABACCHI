import asyncio
import os

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from starlette.requests import Request

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

import backend.server as server


def request_with_operator():
    request = Request({"type": "http", "method": "PATCH", "path": "/", "headers": []})
    request.state.authenticated_user = "test-operatore"
    return request


def isolated_db(monkeypatch):
    database = AsyncMongoMockClient()["sold_correction_test"]
    monkeypatch.setattr(server, "db", database)
    return database


def test_update_changes_shop_sold_stock_and_writes_audit(monkeypatch):
    database = isolated_db(monkeypatch)

    async def exercise():
        original = {
            "id": "product-1",
            "codice": "AMMS395",
            "descrizione": "MARLBORO KS*AST20",
            "prezzo": 6.80,
            "venduti_negozio": 100,
            "venduti_vending": 5,
            "giacenza_negozio": 12,
            "giacenza_vending": 4,
        }
        await database.prodotti.insert_one(original)
        response = await server.update_venduti_negozio(
            "product-1",
            server.VendutiNegozioIn(venduti_negozio=120, valore_atteso=100),
            request_with_operator(),
        )
        product = await database.prodotti.find_one({"id": "product-1"}, {"_id": 0})
        audit = await database.rettifiche_venduti.find_one({}, {"_id": 0})
        return response, product, audit

    response, product, audit = asyncio.run(exercise())

    assert response["venduti_negozio"] == 120
    assert response["venduti_totale"] == 125
    assert product["venduti_negozio"] == 120
    assert product["venduti_vending"] == 5
    assert response["giacenza_negozio"] == -8
    assert product["giacenza_negozio"] == -8
    assert product["giacenza_vending"] == 4
    assert audit["valore_precedente"] == 100
    assert audit["valore_nuovo"] == 120
    assert audit["delta_quantita"] == 20
    assert audit["prezzo_unitario"] == 6.8
    assert audit["importo_contabile"] == 136.0
    assert audit["delta_giacenza_negozio"] == -20
    assert audit["giacenza_precedente"] == 12
    assert audit["giacenza_nuova"] == -8
    assert audit["operatore"] == "test-operatore"


def test_decrease_writes_negative_accounting_correction(monkeypatch):
    database = isolated_db(monkeypatch)

    async def exercise():
        await database.prodotti.insert_one({
            "id": "product-1",
            "codice": "AMMS395",
            "prezzo": 6.80,
            "venduti_negozio": 100,
            "venduti_vending": 5,
            "giacenza_negozio": 12,
            "giacenza_vending": 4,
        })
        response = await server.update_venduti_negozio(
            "product-1",
            server.VendutiNegozioIn(venduti_negozio=97, valore_atteso=100),
            request_with_operator(),
        )
        product = await database.prodotti.find_one({"id": "product-1"}, {"_id": 0})
        audit = await database.rettifiche_venduti.find_one({}, {"_id": 0})
        return response, product, audit

    response, product, audit = asyncio.run(exercise())

    assert response["giacenza_negozio"] == 15
    assert product["giacenza_negozio"] == 15
    assert product["giacenza_vending"] == 4
    assert audit["delta_quantita"] == -3
    assert audit["importo_contabile"] == -20.4
    assert audit["delta_giacenza_negozio"] == 3
    assert audit["giacenza_precedente"] == 12
    assert audit["giacenza_nuova"] == 15


def test_update_rejects_stale_value_without_overwriting(monkeypatch):
    database = isolated_db(monkeypatch)

    async def exercise():
        await database.prodotti.insert_one({
            "id": "product-1",
            "codice": "AMMS395",
            "venduti_negozio": 101,
            "venduti_vending": 5,
        })
        with pytest.raises(HTTPException) as exc_info:
            await server.update_venduti_negozio(
                "product-1",
                server.VendutiNegozioIn(venduti_negozio=120, valore_atteso=100),
                request_with_operator(),
            )
        product = await database.prodotti.find_one({"id": "product-1"}, {"_id": 0})
        audits = await database.rettifiche_venduti.count_documents({})
        return exc_info.value, product, audits

    error, product, audits = asyncio.run(exercise())

    assert error.status_code == 409
    assert product["venduti_negozio"] == 101
    assert audits == 0


def test_update_rejects_negative_values_before_database_write():
    with pytest.raises(ValueError):
        server.VendutiNegozioIn(venduti_negozio=-1, valore_atteso=0)


def test_regular_product_update_preserves_both_sold_counters(monkeypatch):
    database = isolated_db(monkeypatch)

    async def exercise():
        await database.prodotti.insert_one({
            "id": "product-1",
            "codice": "10",
            "descrizione": "ACCENDINI",
            "categoria": "ACCESSORI",
            "prezzo": 1,
            "venduti_negozio": 373,
            "venduti_vending": 29,
            "giacenza_negozio": 78,
            "giacenza_vending": 4,
        })
        await server.update_prodotto("product-1", server.ProdottoIn(
            codice="10",
            descrizione="ACCENDINI NUOVI",
            categoria="ACCESSORI",
            prezzo=1.20,
            giacenza_negozio=78,
        ))
        return await database.prodotti.find_one({"id": "product-1"}, {"_id": 0})

    product = asyncio.run(exercise())

    assert product["descrizione"] == "ACCENDINI NUOVI"
    assert product["venduti_negozio"] == 373
    assert product["venduti_vending"] == 29

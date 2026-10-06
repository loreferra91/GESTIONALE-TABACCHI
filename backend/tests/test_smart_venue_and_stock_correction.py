import asyncio
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

import backend.server as server


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, key, direction):
        self.documents.sort(key=lambda row: row.get(key), reverse=direction < 0)
        return self

    async def to_list(self, length):
        return self.documents[:length] if length is not None else self.documents


class FakeCollection:
    def __init__(self):
        self.documents = []

    @staticmethod
    def _matches(document, query):
        return all(document.get(key) == value for key, value in query.items())

    async def insert_one(self, document):
        self.documents.append(dict(document))

    async def insert_many(self, documents):
        self.documents.extend(dict(document) for document in documents)

    async def delete_many(self, query):
        self.documents = [row for row in self.documents if not self._matches(row, query)] if query else []

    async def delete_one(self, query):
        for index, row in enumerate(self.documents):
            if self._matches(row, query):
                self.documents.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def find(self, query=None, _projection=None):
        query = query or {}
        return FakeCursor([dict(row) for row in self.documents if self._matches(row, query)])

    async def find_one(self, query, _projection=None):
        return next((dict(row) for row in self.documents if self._matches(row, query)), None)

    async def update_one(self, query, update, upsert=False):
        for row in self.documents:
            if self._matches(row, query):
                row.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1)
        if upsert:
            self.documents.append({**query, **update.get("$set", {})})
            return SimpleNamespace(matched_count=0, upserted_id="fake-id")
        return SimpleNamespace(matched_count=0)


def isolated_db(monkeypatch):
    database = SimpleNamespace(
        smart_venue=FakeCollection(),
        smart_venue_hidden=FakeCollection(),
        smart_venue_values=FakeCollection(),
        vending=FakeCollection(),
        prodotti=FakeCollection(),
    )
    monkeypatch.setattr(server, "db", database)
    return database


def test_smart_venue_uses_excel_values_and_current_product_stock(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_list():
        await database.prodotti.insert_one({
            "id": "prod-1", "codice": "P1", "descrizione": "Descrizione prodotto",
            "acquistati": 10, "giacenza_negozio": 25, "giacenza_vending": 4,
        })
        await database.smart_venue.insert_many([
            {
                "id": "smart-venue:P1", "codice": "P1", "descrizione": "Descrizione Excel",
                "acquistati": 1080, "smart_venue": 43,
            },
            {"id": "smart-venue:P2", "codice": "P2", "descrizione": "Prodotto nascosto"},
        ])
        await database.smart_venue_hidden.insert_one({"product_id": "smart-venue:P2"})
        return await server.list_smart_venue()

    rows = asyncio.run(seed_and_list())

    assert len(rows) == 1
    assert rows[0]["codice"] == "P1"
    assert rows[0]["descrizione"] == "Descrizione Excel"
    assert rows[0]["acquistati"] == 1080
    assert rows[0]["rimanenze"] == 29
    assert rows[0]["smart_venue"] == 43
    assert rows[0]["differenza"] == 14


def test_smart_venue_blank_insertion_uses_difference(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_confirm():
        await database.prodotti.insert_one({
            "id": "prod-1", "codice": "P1", "giacenza_negozio": 7, "giacenza_vending": 3,
        })
        await database.smart_venue.insert_one({
            "id": "smart-venue:P1", "codice": "P1", "smart_venue": 14,
        })
        confirmed = await server.confirm_smart_venue_difference("smart-venue:P1", {})
        stored = await database.smart_venue.find_one({"id": "smart-venue:P1"})
        return confirmed, stored

    confirmed, stored = asyncio.run(seed_and_confirm())

    assert confirmed["inserimento_smart_venue"] == 4
    assert confirmed["smart_venue"] == 10
    assert confirmed["differenza"] == 0
    assert stored["smart_venue"] == 10


def test_smart_venue_manual_insertion_decreases_smart_value(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_confirm():
        await database.prodotti.insert_one({
            "id": "prod-1", "codice": "P1", "giacenza_negozio": 7, "giacenza_vending": 3,
        })
        await database.smart_venue.insert_one({
            "id": "smart-venue:P1", "codice": "P1", "smart_venue": 20,
        })
        return await server.confirm_smart_venue_difference(
            "smart-venue:P1", {"inserimento_smart_venue": 3}
        )

    confirmed = asyncio.run(seed_and_confirm())

    assert confirmed["inserimento_smart_venue"] == 3
    assert confirmed["smart_venue"] == 17
    assert confirmed["differenza"] == 7


def test_manual_vending_stock_correction_updates_product_total_without_shop_movement(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_update():
        await database.vending.insert_many([
            {"id": "v1", "colonna": "A01", "codice": "P1", "giacenza": 2, "capacita_max": 5},
            {"id": "v2", "colonna": "A02", "codice": "P1", "giacenza": 1, "capacita_max": 5},
        ])
        await database.prodotti.insert_one({"codice": "P1", "giacenza_negozio": 9, "giacenza_vending": 3})
        response = await server.update_vending_giacenza("v1", {"giacenza": 4})
        product = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        vending = await database.vending.find_one({"id": "v1"}, {"_id": 0})
        return response, product, vending

    response, product, vending = asyncio.run(seed_and_update())

    assert response["giacenza_precedente"] == 2
    assert response["giacenza"] == 4
    assert vending["giacenza_sorgente"] == "CORREZIONE_MANUALE"
    assert product["giacenza_vending"] == 5
    assert product["giacenza_negozio"] == 9


def test_manual_vending_stock_correction_rejects_value_above_capacity(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_update():
        await database.vending.insert_one(
            {"id": "v1", "colonna": "A01", "codice": "P1", "giacenza": 2, "capacita_max": 5}
        )
        await server.update_vending_giacenza("v1", {"giacenza": 6})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(seed_and_update())

    assert exc.value.status_code == 422
    assert "capacità" in exc.value.detail


def test_delete_smart_venue_row(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_delete():
        await database.smart_venue.insert_one({"id": "smart-venue:P1", "codice": "P1"})
        response = await server.delete_smart_venue_row("smart-venue:P1")
        smart_row = await database.smart_venue.find_one({"id": "smart-venue:P1"})
        hidden = await database.smart_venue_hidden.find_one({"product_id": "smart-venue:P1"})
        return response, smart_row, hidden

    response, smart_row, hidden = asyncio.run(seed_and_delete())

    assert response == {"ok": True, "id": "smart-venue:P1"}
    assert smart_row["codice"] == "P1"
    assert hidden["product_id"] == "smart-venue:P1"

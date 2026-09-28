import asyncio
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

import backend.server as server


class FakeCollection:
    def __init__(self, document=None):
        self.document = document
        self.updates = []

    async def find_one(self, query):
        return self.document

    async def update_one(self, query, update):
        self.updates.append((query, update))
        return SimpleNamespace(matched_count=1)


class FakeListCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, *_args):
        return self

    async def to_list(self, _length):
        return self.documents


class FakeListCollection:
    def __init__(self, documents):
        self.documents = documents

    def find(self, *_args, **_kwargs):
        return FakeListCursor(self.documents)


def test_vending_proposals_respect_warehouse_availability(monkeypatch):
    vending = FakeListCollection([
        {"id": "v1", "colonna": "A01", "codice": "P1", "giacenza": 0, "capacita_max": 5, "soglia_minima": 2},
        {"id": "v2", "colonna": "A02", "codice": "P2", "giacenza": 0, "capacita_max": 5, "soglia_minima": 2},
    ])
    prodotti = FakeListCollection([
        {"codice": "P1", "giacenza_negozio": 0},
        {"codice": "P2", "giacenza_negozio": 3},
    ])
    monkeypatch.setattr(server, "db", SimpleNamespace(vending=vending, prodotti=prodotti))

    result = asyncio.run(server.list_vending())

    assert result[0]["proposta"] == 0
    assert result[0]["esito"] == "MAGAZZINO ESAURITO"
    assert result[1]["proposta"] == 3
    assert result[1]["esito"] == "DA CARICARE PARZIALE"


def test_restock_moves_only_the_quantity_that_fits(monkeypatch):
    vending = FakeCollection({
        "id": "v1",
        "colonna": "A01",
        "codice": "P1",
        "giacenza": 4,
        "capacita_max": 5,
    })
    prodotti = FakeCollection({"codice": "P1", "giacenza_negozio": 10})
    monkeypatch.setattr(server, "db", SimpleNamespace(vending=vending, prodotti=prodotti))

    result = asyncio.run(server.ricarica_vending("v1", {"quantita": 10}))

    assert result["nuova_giacenza"] == 5
    assert result["quantita_caricata"] == 1
    assert prodotti.updates == [
        ({"codice": "P1"}, {"$inc": {"giacenza_negozio": -1, "giacenza_vending": 1}})
    ]


def test_restock_is_limited_by_available_warehouse_stock(monkeypatch):
    vending = FakeCollection({
        "id": "v1",
        "colonna": "A01",
        "codice": "P1",
        "giacenza": 0,
        "capacita_max": 5,
    })
    prodotti = FakeCollection({"codice": "P1", "giacenza_negozio": 3})
    monkeypatch.setattr(server, "db", SimpleNamespace(vending=vending, prodotti=prodotti))

    result = asyncio.run(server.ricarica_vending("v1", {"quantita": 5}))

    assert result["quantita_caricata"] == 3
    assert result["nuova_giacenza"] == 3
    assert result["giacenza_magazzino_residua"] == 0
    assert prodotti.updates == [
        ({"codice": "P1"}, {"$inc": {"giacenza_negozio": -3, "giacenza_vending": 3}})
    ]


def test_restock_rejects_when_warehouse_is_empty(monkeypatch):
    vending = FakeCollection({
        "id": "v1",
        "colonna": "A01",
        "codice": "P1",
        "giacenza": 0,
        "capacita_max": 5,
    })
    prodotti = FakeCollection({"codice": "P1", "giacenza_negozio": 0})
    monkeypatch.setattr(server, "db", SimpleNamespace(vending=vending, prodotti=prodotti))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.ricarica_vending("v1", {"quantita": 5}))

    assert exc.value.status_code == 409
    assert vending.updates == []
    assert prodotti.updates == []


@pytest.mark.parametrize("quantita", [0, -1, "non-numerica"])
def test_restock_rejects_non_positive_or_invalid_quantity(quantita):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.ricarica_vending("v1", {"quantita": quantita}))
    assert exc.value.status_code == 422

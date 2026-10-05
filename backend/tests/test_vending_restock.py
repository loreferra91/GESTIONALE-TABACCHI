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


class FakeMutableCollection(FakeListCollection):
    @staticmethod
    def _matches(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif isinstance(expected, dict) and "$gte" in expected:
                if actual < expected["$gte"]:
                    return False
            elif actual != expected:
                return False
        return True

    def find(self, query=None, *_args, **_kwargs):
        query = query or {}
        return FakeListCursor([d for d in self.documents if self._matches(d, query)])

    async def update_one(self, query, update):
        for document in self.documents:
            if not self._matches(document, query):
                continue
            for key, value in update.get("$inc", {}).items():
                document[key] = document.get(key, 0) + value
            return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


def test_vending_proposals_respect_warehouse_availability(monkeypatch):
    vending = FakeListCollection([
        {"id": "v1", "colonna": "A01", "codice": "P1", "giacenza": 0, "capacita_max": 5, "soglia_minima": 2},
        {"id": "v2", "colonna": "A02", "codice": "P2", "giacenza": 0, "capacita_max": 5, "soglia_minima": 2},
        {"id": "v3", "colonna": "A03", "codice": "P3", "giacenza": 2, "capacita_max": 4, "soglia_minima": 2},
    ])
    prodotti = FakeListCollection([
        {"codice": "P1", "giacenza_negozio": 0},
        {"codice": "P2", "giacenza_negozio": 3},
        {"codice": "P3", "giacenza_negozio": 2},
    ])
    monkeypatch.setattr(server, "db", SimpleNamespace(vending=vending, prodotti=prodotti))

    result = asyncio.run(server.list_vending())

    assert result[0]["proposta"] == 0
    assert result[0]["esito"] == "MAGAZZINO ESAURITO"
    assert result[1]["fabbisogno"] == 5
    assert result[1]["proposta"] == 0
    assert result[1]["esito"] == "MAGAZZINO INSUFFICIENTE"
    assert result[2]["fabbisogno"] == 2
    assert result[2]["proposta"] == 2
    assert result[2]["esito"] == "DA CARICARE"


def test_vending_proposal_fills_to_capacity_and_leaves_shop_stock(monkeypatch):
    vending = FakeListCollection([
        {"id": "v1", "colonna": "B01", "codice": "P1", "giacenza": 2, "capacita_max": 4, "soglia_minima": 2},
    ])
    prodotti = FakeListCollection([
        {"codice": "P1", "giacenza_negozio": 5},
    ])
    monkeypatch.setattr(server, "db", SimpleNamespace(vending=vending, prodotti=prodotti))

    result = asyncio.run(server.list_vending())

    assert result[0]["fabbisogno"] == 2
    assert result[0]["proposta"] == 2
    assert result[0]["giacenza_magazzino"] == 5
    assert result[0]["esito"] == "DA CARICARE"


def test_restock_rejects_quantity_above_column_capacity(monkeypatch):
    vending = FakeCollection({
        "id": "v1",
        "colonna": "A01",
        "codice": "P1",
        "giacenza": 4,
        "capacita_max": 5,
    })
    prodotti = FakeCollection({"codice": "P1", "giacenza_negozio": 10})
    monkeypatch.setattr(server, "db", SimpleNamespace(vending=vending, prodotti=prodotti))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.ricarica_vending("v1", {"quantita": 10}))

    assert exc.value.status_code == 422
    assert "al massimo 1" in exc.value.detail
    assert vending.updates == []
    assert prodotti.updates == []


def test_restock_fills_to_capacity_and_leaves_remaining_shop_stock(monkeypatch):
    vending = FakeCollection({
        "id": "v1",
        "colonna": "B01",
        "codice": "P1",
        "giacenza": 2,
        "capacita_max": 4,
    })
    prodotti = FakeCollection({"codice": "P1", "giacenza_negozio": 5})
    monkeypatch.setattr(server, "db", SimpleNamespace(vending=vending, prodotti=prodotti))

    result = asyncio.run(server.ricarica_vending("v1", {"quantita": 2}))

    assert result["quantita_caricata"] == 2
    assert result["nuova_giacenza"] == 4
    assert result["giacenza_magazzino_residua"] == 3
    assert prodotti.updates == [
        ({"codice": "P1"}, {"$inc": {"giacenza_negozio": -2, "giacenza_vending": 2}})
    ]


def test_restock_rejects_when_warehouse_cannot_fill_capacity(monkeypatch):
    vending = FakeCollection({
        "id": "v1",
        "colonna": "A01",
        "codice": "P1",
        "giacenza": 0,
        "capacita_max": 5,
    })
    prodotti = FakeCollection({"codice": "P1", "giacenza_negozio": 3})
    monkeypatch.setattr(server, "db", SimpleNamespace(vending=vending, prodotti=prodotti))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.ricarica_vending("v1", {"quantita": 5}))

    assert exc.value.status_code == 409
    assert "Magazzino insufficiente" in exc.value.detail
    assert vending.updates == []
    assert prodotti.updates == []


def test_restock_accepts_partial_manual_quantity(monkeypatch):
    vending = FakeCollection({
        "id": "v1",
        "colonna": "A01",
        "codice": "P1",
        "giacenza": 2,
        "capacita_max": 5,
    })
    prodotti = FakeCollection({"codice": "P1", "giacenza_negozio": 10})
    monkeypatch.setattr(server, "db", SimpleNamespace(vending=vending, prodotti=prodotti))

    result = asyncio.run(server.ricarica_vending("v1", {"quantita": 1}))

    assert result["quantita_caricata"] == 1
    assert result["nuova_giacenza"] == 3
    assert vending.updates == [({"id": "v1"}, {"$set": {"giacenza": 3}})]
    assert prodotti.updates == [
        ({"codice": "P1"}, {"$inc": {"giacenza_negozio": -1, "giacenza_vending": 1}})
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


def test_complete_restock_applies_manual_and_proposed_quantities(monkeypatch):
    vending = FakeMutableCollection([
        {"id": "v1", "colonna": "A01", "codice": "P1", "giacenza": 2, "capacita_max": 5},
        {"id": "v2", "colonna": "A02", "codice": "P2", "giacenza": 1, "capacita_max": 5},
    ])
    prodotti = FakeMutableCollection([
        {"codice": "P1", "giacenza_negozio": 10, "giacenza_vending": 2},
        {"codice": "P2", "giacenza_negozio": 8, "giacenza_vending": 1},
    ])
    monkeypatch.setattr(server, "db", SimpleNamespace(vending=vending, prodotti=prodotti))

    result = asyncio.run(server.ricarica_vending_completa({
        "righe": [
            {"id": "v1", "quantita": 1},
            {"id": "v2", "quantita": 4},
        ],
    }))

    assert result["colonne_caricate"] == 2
    assert result["pezzi_caricati"] == 5
    assert vending.documents[0]["giacenza"] == 3
    assert vending.documents[1]["giacenza"] == 5
    assert prodotti.documents[0]["giacenza_negozio"] == 9
    assert prodotti.documents[0]["giacenza_vending"] == 3
    assert prodotti.documents[1]["giacenza_negozio"] == 4
    assert prodotti.documents[1]["giacenza_vending"] == 5


def test_complete_restock_validates_all_rows_before_updates(monkeypatch):
    vending = FakeMutableCollection([
        {"id": "v1", "colonna": "A01", "codice": "P1", "giacenza": 2, "capacita_max": 5},
        {"id": "v2", "colonna": "A02", "codice": "P2", "giacenza": 4, "capacita_max": 5},
    ])
    prodotti = FakeMutableCollection([
        {"codice": "P1", "giacenza_negozio": 10, "giacenza_vending": 2},
        {"codice": "P2", "giacenza_negozio": 10, "giacenza_vending": 4},
    ])
    monkeypatch.setattr(server, "db", SimpleNamespace(vending=vending, prodotti=prodotti))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.ricarica_vending_completa({
            "righe": [
                {"id": "v1", "quantita": 2},
                {"id": "v2", "quantita": 2},
            ],
        }))

    assert exc.value.status_code == 422
    assert "Colonna A02" in exc.value.detail
    assert vending.documents[0]["giacenza"] == 2
    assert vending.documents[1]["giacenza"] == 4
    assert prodotti.documents[0]["giacenza_negozio"] == 10
    assert prodotti.documents[1]["giacenza_negozio"] == 10

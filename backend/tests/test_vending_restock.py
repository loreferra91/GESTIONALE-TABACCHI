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


def test_restock_moves_only_the_quantity_that_fits(monkeypatch):
    vending = FakeCollection({
        "id": "v1",
        "colonna": "A01",
        "codice": "P1",
        "giacenza": 4,
        "capacita_max": 5,
    })
    prodotti = FakeCollection()
    monkeypatch.setattr(server, "db", SimpleNamespace(vending=vending, prodotti=prodotti))

    result = asyncio.run(server.ricarica_vending("v1", {"quantita": 10}))

    assert result["nuova_giacenza"] == 5
    assert result["quantita_caricata"] == 1
    assert prodotti.updates == [
        ({"codice": "P1"}, {"$inc": {"giacenza_negozio": -1, "giacenza_vending": 1}})
    ]


@pytest.mark.parametrize("quantita", [0, -1, "non-numerica"])
def test_restock_rejects_non_positive_or_invalid_quantity(quantita):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.ricarica_vending("v1", {"quantita": quantita}))
    assert exc.value.status_code == 422

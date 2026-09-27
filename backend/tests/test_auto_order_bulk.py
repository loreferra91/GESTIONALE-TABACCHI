import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

import backend.server as server


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents
        self.to_list_calls = 0

    async def to_list(self, _length):
        self.to_list_calls += 1
        return self.documents


class FakeProducts:
    def __init__(self, documents):
        self.documents = documents
        self.find_calls = 0

    def find(self, *_args, **_kwargs):
        self.find_calls += 1
        return FakeCursor(self.documents)


class FakeAggregateCollection:
    def __init__(self, documents):
        self.documents = documents
        self.pipelines = []

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return FakeCursor(self.documents)


def test_auto_order_uses_bulk_aggregations_instead_of_queries_per_product(monkeypatch):
    products = FakeProducts([
        {
            "codice": "P1",
            "descrizione": "Prodotto uno",
            "categoria": "ACCESSORI",
            "prezzo": 2,
            "acquistati": 20,
            "giacenza_negozio": 0,
            "giacenza_vending": 1,
            "venduti_negozio": 0,
        },
        {
            "codice": "P2",
            "descrizione": "Prodotto due",
            "categoria": "ACCESSORI",
            "prezzo": 1,
            "acquistati": 10,
            "giacenza_negozio": 10,
            "giacenza_vending": 0,
            "venduti_negozio": 0,
        },
    ])
    sales = FakeAggregateCollection([{"_id": "P1", "tot": 12}])
    orders = FakeAggregateCollection([{"_id": "P1", "n_ord": 2, "tot_quantita": 18}])

    async def fake_get_params():
        return {
            "SOGLIA_ALLERT_PCT": 0.35,
            "FAST_VENDUTO30_MIN": 8,
            "SLOW_VENDUTO30_MAX": 2,
            "FATT_SETTIMANALE": 1.15,
            "GIORNI_COPERTURA_MIN": 7,
            "GIORNI_COPERTURA_TARGET": 14,
            "PERIODO_VENDUTI_GG": 90,
            "LOTTO_ACCESSORI": 1,
        }

    monkeypatch.setattr(server, "get_params", fake_get_params)
    monkeypatch.setattr(
        server,
        "db",
        SimpleNamespace(prodotti=products, vendite=sales, storico_ordini=orders),
    )

    result = asyncio.run(server.auto_order())

    assert products.find_calls == 1
    assert len(sales.pipelines) == 1
    assert len(orders.pipelines) == 1
    assert sales.pipelines[0][0]["$match"]["data"]["$gte"]
    assert sales.pipelines[0][1]["$group"]["_id"] == "$codice"
    assert orders.pipelines[0][0]["$group"]["_id"] == "$codice"

    p1 = next(row for row in result["righe"] if row["codice"] == "P1")
    assert p1["venduto_30gg"] == 12
    assert p1["n_ordini_storici"] == 2
    assert p1["media_ordini_storico"] == 9

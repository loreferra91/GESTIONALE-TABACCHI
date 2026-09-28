import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

import backend.server as server
from fastapi.testclient import TestClient


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
        {
            "codice": "P3",
            "descrizione": "Articolo fermo con storico annuale",
            "categoria": "ACCESSORI",
            "prezzo": 1,
            "acquistati": 20,
            "giacenza_negozio": 0,
            "giacenza_vending": 20,
            "venduti_negozio": 0,
        },
        {
            "codice": "P4",
            "descrizione": "Scorta reale bassa",
            "categoria": "ACCESSORI",
            "prezzo": 1,
            "acquistati": 20,
            "giacenza_negozio": 7,
            "giacenza_vending": 5,
            "venduti_negozio": 100,
        },
    ])
    sales = FakeAggregateCollection([
        {"_id": "P1", "tot": 12},
        {"_id": "P4", "tot": 99},
    ])
    imported_sales = FakeAggregateCollection([{"_id": "P4", "tot": 5}])
    orders = FakeAggregateCollection([{"_id": "P1", "n_ord": 2, "tot_quantita": 18}])

    async def fake_get_params():
        return {
            "SOGLIA_ALLERT_PCT": 0.35,
            "FAST_VENDUTO30_MIN": 8,
            "SLOW_VENDUTO30_MAX": 2,
            "FATT_SETTIMANALE": 1.15,
            "GIORNI_COPERTURA_MIN": 7,
            "GIORNI_COPERTURA_TARGET": 14,
            "AUTO_ORDER_FINESTRA_GG": 10,
            "PERIODO_VENDUTI_GG": 90,
            "LOTTO_ACCESSORI": 1,
        }

    monkeypatch.setattr(server, "get_params", fake_get_params)
    monkeypatch.setattr(
        server,
        "db",
        SimpleNamespace(
            prodotti=products,
            vendite=sales,
            db_storico_vend=imported_sales,
            storico_ordini=orders,
        ),
    )

    result = asyncio.run(server.auto_order())

    assert products.find_calls == 1
    assert len(sales.pipelines) == 1
    assert len(imported_sales.pipelines) == 1
    assert len(orders.pipelines) == 1
    assert sales.pipelines[0][0]["$match"]["data"]["$gte"]
    assert sales.pipelines[0][1]["$group"]["_id"] == "$codice"
    assert orders.pipelines[0][0]["$group"]["_id"] == "$codice"

    p1 = next(row for row in result["righe"] if row["codice"] == "P1")
    assert p1["venduto_periodo"] == 12
    assert p1["finestra_domanda_gg"] == 10
    assert p1["n_ordini_storici"] == 2
    assert p1["media_ordini_storico"] == 9
    assert not any(row["codice"] == "P3" for row in result["righe"])

    p4 = next(row for row in result["righe"] if row["codice"] == "P4")
    assert p4["venduto_periodo"] == 5
    assert p4["fonte_domanda"] == "DB_STORICO_VEND"
    assert p4["magazzino_reale"] == 2
    assert p4["copertura_gg"] == 4


def test_auto_order_pdf_filters_selected_category(monkeypatch):
    async def fake_auto_order():
        base = {
            "descrizione": "Articolo test",
            "qta_da_ordinare": 10,
            "lotto_ordine": 10,
            "prezzo": 5,
            "totale": 50,
            "motivo": "TEST",
        }
        return {
            "righe": [
                {**base, "codice": "SIG1", "categoria": "SIGARETTE"},
                {**base, "codice": "ACC1", "categoria": "ACCESSORI"},
            ],
            "totale": 100,
            "n_righe": 2,
            "parametri": {},
        }

    monkeypatch.setattr(server, "auto_order", fake_auto_order)
    response = TestClient(server.app).get(
        "/api/auto-order/pdf",
        params={"fornitore": "Test", "categoria": "SIGARETTE"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["x-auto-order-category"] == "SIGARETTE"
    assert response.headers["x-auto-order-rows"] == "1"
    assert "ordine_sigarette_" in response.headers["content-disposition"]

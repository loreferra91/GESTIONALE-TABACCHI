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

    async def to_list(self, _length):
        return self.documents


class FakeProducts:
    def __init__(self, documents):
        self.documents = documents
        self.updates = []

    def find(self, *_args, **_kwargs):
        return FakeCursor(self.documents)

    async def update_one(self, *args, **kwargs):
        self.updates.append((args, kwargs))


class FakeAggregateCollection:
    def __init__(self, aggregate_results=None, latest_date="2026-09-28T00:00:00"):
        self.aggregate_results = list(aggregate_results or [])
        self.latest_date = latest_date

    def aggregate(self, _pipeline):
        rows = self.aggregate_results.pop(0) if self.aggregate_results else []
        return FakeCursor(rows)

    async def find_one(self, *_args, **_kwargs):
        return {"data": self.latest_date} if self.latest_date else None


class FakeBatchCollection:
    def __init__(self):
        self.docs = {}
        self.deleted = []

    async def find_one(self, query, *_args, **_kwargs):
        return self.docs.get(query["batch_key"])

    async def insert_one(self, doc):
        self.docs[doc["batch_key"]] = dict(doc)

    async def delete_one(self, query):
        self.deleted.append(query)
        self.docs.pop(query["batch_key"], None)


class FakeRowsCollection:
    def __init__(self, fail_on=None):
        self.docs = []
        self.deleted = []
        self.fail_on = fail_on

    async def insert_one(self, doc):
        if self.fail_on is not None and len(self.docs) + 1 == self.fail_on:
            raise RuntimeError("row insert failed")
        self.docs.append(dict(doc))

    async def delete_many(self, query):
        self.deleted.append(query)
        ids = set(query.get("id", {}).get("$in", []))
        self.docs = [d for d in self.docs if d.get("id") not in ids]


def base_params(**overrides):
    params = {
        "SOGLIA_ALLERT_PCT": 0.35,
        "GIORNI_COPERTURA_MIN": 7,
        "GIORNI_COPERTURA_TARGET": 14,
        "AUTO_ORDER_FINESTRA_BREVE_GG": 10,
        "AUTO_ORDER_FINESTRA_LUNGA_GG": 30,
        "AUTO_ORDER_PESO_BREVE": 0.7,
        "AUTO_ORDER_MIN_VENDUTO_BREVE": 2,
        "AUTO_ORDER_MIN_VENDUTO_LUNGO": 4,
        "AUTO_ORDER_FATTORE_SICUREZZA": 1.0,
        "LOTTO_ACCESSORI": 1,
        "LOTTO_SIGARETTE": 10,
        "LOTTO_ELETTRONICHE": 5,
    }
    params.update(overrides)
    return params


def product(codice, **overrides):
    doc = {
        "codice": codice,
        "descrizione": codice,
        "categoria": "ACCESSORI",
        "prezzo": 1.0,
        "acquistati": 0,
        "venduti_negozio": 0,
        "venduti_vending": 0,
        "giacenza_negozio": 0,
        "giacenza_vending": 0,
    }
    doc.update(overrides)
    return doc


def run_auto_order(monkeypatch, products, params=None, app_sales=None, imported_sales=None, orders=None):
    async def fake_get_params():
        return params or base_params()

    monkeypatch.setattr(server, "get_params", fake_get_params)
    monkeypatch.setattr(
        server,
        "db",
        SimpleNamespace(
            prodotti=FakeProducts(products),
            vendite=FakeAggregateCollection(app_sales or [[], []]),
            db_storico_vend=FakeAggregateCollection(imported_sales or [[], []], latest_date=None),
            storico_ordini=FakeAggregateCollection([orders or []], latest_date=None),
        ),
    )
    return asyncio.run(server.auto_order())


def test_excel_remaining_stock_is_normalized_once():
    assert server.physical_shop_stock_from_excel(20, 5, 3) == 12
    assert server.physical_shop_stock_from_excel(4, 5, 0) == -1


def test_zero_params_are_not_replaced_by_defaults(monkeypatch):
    result = run_auto_order(
        monkeypatch,
        [product("ZERO", giacenza_negozio=0)],
        params=base_params(AUTO_ORDER_PESO_BREVE=0, AUTO_ORDER_MIN_VENDUTO_BREVE=0, AUTO_ORDER_MIN_VENDUTO_LUNGO=0),
        app_sales=[[], [{"_id": "ZERO", "tot": 30}]],
    )

    row = next(r for r in result["righe"] if r["codice"] == "ZERO")
    assert row["domanda_gg"] == 1
    assert row["qta_da_ordinare"] == 14


def test_anomaly_with_sufficient_sales_is_excluded(monkeypatch):
    result = run_auto_order(
        monkeypatch,
        [product("NEG", giacenza_negozio=-1)],
        app_sales=[[{"_id": "NEG", "tot": 10}], [{"_id": "NEG", "tot": 30}]],
    )

    assert not any(r["codice"] == "NEG" for r in result["righe"])
    row = next(r for r in result["esclusi"] if r["codice"] == "NEG")
    assert row["stato"] == "ANOMALIA"
    assert row["qta_da_ordinare"] == 0


def test_restock_then_auto_order_does_not_double_subtract_vending(monkeypatch):
    result = run_auto_order(
        monkeypatch,
        [product("RESTOCK", giacenza_negozio=6, giacenza_vending=5, venduti_vending=10)],
        app_sales=[[{"_id": "RESTOCK", "tot": 10}], [{"_id": "RESTOCK", "tot": 30}]],
    )

    row = next(r for r in result["righe"] if r["codice"] == "RESTOCK")
    assert row["magazzino_reale"] == 6
    assert row["magazzino_reale_lordo"] == 6
    assert row["qta_da_ordinare"] == 8


def test_imported_source_overlaps_app_and_long_only_sales_order(monkeypatch):
    result = run_auto_order(
        monkeypatch,
        [product("SRC", giacenza_negozio=0)],
        params=base_params(AUTO_ORDER_PESO_BREVE=0, AUTO_ORDER_MIN_VENDUTO_BREVE=99, AUTO_ORDER_MIN_VENDUTO_LUNGO=4),
        app_sales=[[{"_id": "SRC", "tot": 99}], [{"_id": "SRC", "tot": 99}]],
        imported_sales=[[], [{"_id": "SRC", "tot": 30}]],
    )

    row = next(r for r in result["righe"] if r["codice"] == "SRC")
    assert row["fonte_domanda"] == "DB_STORICO_VEND"
    assert row["venduto_breve"] == 0
    assert row["venduto_lungo"] == 30
    assert row["qta_da_ordinare"] == 14


def test_configurable_windows_and_compat_fields(monkeypatch):
    result = run_auto_order(
        monkeypatch,
        [product("WIN", giacenza_negozio=0)],
        params=base_params(AUTO_ORDER_FINESTRA_BREVE_GG=5, AUTO_ORDER_FINESTRA_LUNGA_GG=20),
        app_sales=[[{"_id": "WIN", "tot": 5}], [{"_id": "WIN", "tot": 20}]],
    )

    row = next(r for r in result["righe"] if r["codice"] == "WIN")
    assert result["finestra_breve_gg"] == 5
    assert result["finestra_lunga_gg"] == 20
    assert row["venduto_breve"] == row["venduto_10gg"] == 5
    assert row["venduto_lungo"] == row["venduto_30gg"] == 20


async def fake_ao_two_rows():
    return {
        "righe": [
            {"codice": "A", "descrizione": "A", "categoria": "ACCESSORI", "stato": "ORDINA ORA", "anomalia": False, "qta_da_ordinare": 2, "prezzo": 3, "totale": 6, "motivo": "TEST"},
            {"codice": "B", "descrizione": "B", "categoria": "ACCESSORI", "stato": "ORDINA ORA", "anomalia": False, "qta_da_ordinare": 1, "prezzo": 4, "totale": 4, "motivo": "TEST"},
        ],
        "esclusi": [{"codice": "X", "stato": "ANOMALIA", "anomalia": True, "qta_da_ordinare": 0}],
        "totale": 10,
        "parametri": {},
        "data_riferimento_domanda": "2026-09-28",
        "snapshot_key": "snapshot-1",
    }


def test_confirmation_idempotency_and_no_stock_increment(monkeypatch):
    batches = FakeBatchCollection()
    rows = FakeRowsCollection()
    products = FakeProducts([])
    monkeypatch.setattr(server, "auto_order", fake_ao_two_rows)
    monkeypatch.setattr(server, "db", SimpleNamespace(ordini_fornitore=batches, ordini_fornitore_righe=rows, prodotti=products))

    first = asyncio.run(server.conferma_auto_order(server.AutoOrderConfermaIn(idempotency_key="key-1")))
    second = asyncio.run(server.conferma_auto_order(server.AutoOrderConfermaIn(idempotency_key="key-1")))

    assert first["ordinati"] == 2
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert len(rows.docs) == 2
    assert products.updates == []


def test_partial_confirmation_failure_rolls_back(monkeypatch):
    batches = FakeBatchCollection()
    rows = FakeRowsCollection(fail_on=2)
    monkeypatch.setattr(server, "auto_order", fake_ao_two_rows)
    monkeypatch.setattr(server, "db", SimpleNamespace(ordini_fornitore=batches, ordini_fornitore_righe=rows, prodotti=FakeProducts([])))

    with pytest.raises(HTTPException):
        asyncio.run(server.conferma_auto_order(server.AutoOrderConfermaIn(idempotency_key="key-rollback")))

    assert batches.docs == {}
    assert rows.docs == []
    assert batches.deleted == [{"batch_key": "key-rollback"}]
    assert rows.deleted

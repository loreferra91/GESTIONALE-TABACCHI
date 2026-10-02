import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

import backend.server as server


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    async def to_list(self, _length):
        return self.documents


class FakeCollection:
    def __init__(self, documents):
        self.documents = documents
        self.queries = []

    def find(self, *args):
        self.queries.append(args)
        return FakeCursor(self.documents)


def historical_row(amount, payment, accounted="SI", outcome="OK"):
    raw = [""] * 18
    raw[7] = str(amount)
    raw[11] = payment
    raw[15] = accounted
    raw[16] = outcome
    return {"raw": raw}


def test_calculates_all_dashboard_balances_from_historical_vending(monkeypatch):
    historical = FakeCollection([
        historical_row("1200,00", "Contanti"),
        historical_row("250.50", "Carte"),
        historical_row("49.50", "PagoBancomat"),
        historical_row("999", "Contanti", accounted="NO"),
        historical_row("999", "Contanti", outcome="ANNULLATA"),
    ])
    app_sales = FakeCollection([{"importo": 777, "pagamento": "CONTANTI"}])
    monkeypatch.setattr(
        server,
        "db",
        SimpleNamespace(db_storico_vending_ext=historical, vendite=app_sales),
    )

    result = asyncio.run(server._dashboard_balances(1500))

    assert result == {
        "saldoVendingTotale": 1500.0,
        "saldoVendingContanti": 1200.0,
        "saldoVendingElettronico": 300.0,
        "saldoCassa": 1500.0,
        "totalePrelievi": 0.0,
        "differenzaCassaVendingContanti": 2700.0,
    }
    assert app_sales.queries == []


def test_uses_app_vending_sales_as_backward_compatible_fallback(monkeypatch):
    historical = FakeCollection([])
    app_sales = FakeCollection([
        {"importo": 1200, "pagamento": "CONTANTI"},
        {"importo": 200, "pagamento": "POS"},
    ])
    monkeypatch.setattr(
        server,
        "db",
        SimpleNamespace(db_storico_vending_ext=historical, vendite=app_sales),
    )

    result = asyncio.run(server._dashboard_balances(1000))

    assert result["saldoVendingTotale"] == 1400.0
    assert result["saldoVendingContanti"] == 1200.0
    assert result["saldoVendingElettronico"] == 200.0
    assert result["differenzaCassaVendingContanti"] == 2200.0
    query = app_sales.queries[0][0]
    assert query["canale"]["$regex"] == "^VENDING$"


def test_empty_legacy_payment_uses_cash_default_without_losing_unknown_methods():
    result = server._calculate_dashboard_balances(
        [(10, "Contanti"), (5, ""), (3, "Voucher elettronico")], 10
    )

    assert result["saldoVendingTotale"] == 18.0
    assert result["saldoVendingContanti"] == 15.0
    assert result["saldoVendingElettronico"] == 3.0
    assert result["saldoVendingTotale"] == (
        result["saldoVendingContanti"] + result["saldoVendingElettronico"]
    )


def test_cash_difference_offsets_a_negative_cash_register_balance():
    result = server._calculate_dashboard_balances(
        [(3445.30, "Contanti")], -1610.62
    )

    assert result["differenzaCassaVendingContanti"] == 1834.68


def test_cash_difference_subtracts_vending_withdrawals():
    result = server._calculate_dashboard_balances(
        [(3445.30, "Contanti")], -1610.62, 500
    )

    assert result["totalePrelievi"] == 500.0
    assert result["differenzaCassaVendingContanti"] == 1334.68

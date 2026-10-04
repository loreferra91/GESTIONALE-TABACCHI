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


class SalesCollection:
    def __init__(self, documents):
        self.documents = documents

    def find(self, *_args):
        return FakeCursor(self.documents)


def historical_row(amount, payment, accounted="SI", outcome="OK", day="2026-08-31"):
    raw = [""] * 18
    raw[2] = day
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
    app_sales = FakeCollection([
        {"data": "2026-08-31", "importo": 777, "pagamento": "CONTANTI"},
        {"data": "2026-09-01", "importo": 25, "pagamento": "CONTANTI"},
        {"data": "02/09/2026", "importo": 10, "pagamento": "POS"},
    ])
    monkeypatch.setattr(
        server,
        "db",
        SimpleNamespace(db_storico_vending_ext=historical, vendite=app_sales),
    )

    result = asyncio.run(server._dashboard_balances(1500, 1225))

    assert result == {
        "saldoVendingTotale": 1535.0,
        "venditeVendingContanti": 1225.0,
        "giacenzaVendingContanti": 1225.0,
        "prelievoVending": 0.0,
        "scontriniVending": 0.0,
        "prelievoDaVending": 0.0,
        "prelieviContantiDaVending": 0.0,
        "prelieviContantiCassaVending": 0.0,
        "cassaVending": 1225.0,
        "giacenzaAttualeCassaVending": 1225.0,
        "prelieviVending": 0.0,
        "saldoCassaNegozioEVending": 1500.0,
        "saldoVendingContanti": 1225.0,
        "saldoVendingElettronico": 310.0,
        "saldoCassa": 1500.0,
        "totalePrelievi": 0.0,
        "differenzaCassaVendingContanti": 1500.0,
    }
    assert len(app_sales.queries) == 1


def test_uses_app_vending_sales_as_backward_compatible_fallback(monkeypatch):
    historical = FakeCollection([])
    app_sales = FakeCollection([
        {"data": "2026-09-01", "importo": 1200, "pagamento": "CONTANTI"},
        {"data": "2026-09-01", "importo": 200, "pagamento": "POS"},
    ])
    monkeypatch.setattr(
        server,
        "db",
        SimpleNamespace(db_storico_vending_ext=historical, vendite=app_sales),
    )

    result = asyncio.run(server._dashboard_balances(1000, 1200))

    assert result["saldoVendingTotale"] == 1400.0
    assert result["saldoVendingContanti"] == 1200.0
    assert result["saldoVendingElettronico"] == 200.0
    assert result["differenzaCassaVendingContanti"] == 1000.0
    query = app_sales.queries[0][0]
    assert query["canale"]["$regex"] == "^VENDING$"


def test_latest_historical_vending_date_uses_date_then_datetime_fallback():
    row_with_date = historical_row(10, "Contanti", day="02/10/2026")
    row_with_datetime = historical_row(10, "Contanti", day="")
    row_with_datetime["raw"][1] = "2026-10-03 08:30:00"

    result = server._latest_historical_vending_date([row_with_date, row_with_datetime])

    assert result.isoformat() == "2026-10-03"


def test_legacy_vending_history_without_dates_does_not_double_count_app_sales(monkeypatch):
    historical = FakeCollection([historical_row(100, "Contanti", day="")])
    app_sales = FakeCollection([
        {"data": "2026-10-03", "importo": 100, "pagamento": "CONTANTI"},
    ])
    monkeypatch.setattr(
        server,
        "db",
        SimpleNamespace(db_storico_vending_ext=historical, vendite=app_sales),
    )

    result = asyncio.run(server._dashboard_balances(0))

    assert result["saldoVendingTotale"] == 100


def test_same_day_csv_cash_created_after_excel_import_is_counted(monkeypatch):
    historical = FakeCollection([historical_row(100, "Contanti", day="2026-10-03")])
    app_sales = FakeCollection([
        {
            "data": "2026-10-03T12:00:00",
            "created_at": "2026-10-03T10:05:00+00:00",
            "importo": 25,
            "pagamento": "CONTANTI",
        },
        {
            "data": "2026-10-03T11:00:00",
            "created_at": "2026-10-03T09:55:00+00:00",
            "importo": 999,
            "pagamento": "CONTANTI",
        },
    ])
    monkeypatch.setattr(
        server,
        "db",
        SimpleNamespace(db_storico_vending_ext=historical, vendite=app_sales),
    )

    result = asyncio.run(server._dashboard_balances(
        0,
        latest_import_created_at="2026-10-03T10:00:00+00:00",
    ))

    assert result["saldoVendingContanti"] == 125


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


def test_vending_sales_do_not_change_combined_cash_balance():
    result = server._calculate_dashboard_balances(
        [(3445.30, "Contanti")], -1610.62, 3445.30
    )

    assert result["venditeVendingContanti"] == 3445.30
    assert result["prelievoVending"] == 0
    assert result["differenzaCassaVendingContanti"] == -1610.62


def test_cash_difference_adds_vending_withdrawals_to_signed_store_balance():
    result = server._calculate_dashboard_balances(
        [(3445.30, "Contanti")], -1610.62, 2945.30
    )

    assert result["totalePrelievi"] == 500.0
    assert result["differenzaCassaVendingContanti"] == -1110.62


def test_vending_cash_inventory_withdrawals_and_receipts_are_exposed_separately():
    result = server._calculate_dashboard_balances(
        [(1000, "Contanti"), (250, "POS")], 300, 600, 50
    )

    assert result["venditeVendingContanti"] == 1000.0
    assert result["giacenzaVendingContanti"] == 600.0
    assert result["scontriniVending"] == 50.0
    assert result["prelievoVending"] == 450.0
    assert result["prelievoDaVending"] == 450.0
    assert result["prelieviContantiDaVending"] == 450.0
    assert result["prelieviContantiCassaVending"] == 450.0
    assert result["giacenzaAttualeCassaVending"] == 600.0
    assert result["cassaVending"] == 600.0
    assert result["saldoCassaNegozioEVending"] == 750.0


def test_supplemental_store_cash_sales_only_counts_new_unimported_cash(monkeypatch):
    sales = FakeCollection([
        {"data": "31/08/2026", "canale": "NEGOZIO", "pagamento": "CONTANTI", "importo": 50},
        {"data": "2026-09-01", "canale": "NEGOZIO", "pagamento": "CONTANTI", "importo": 100},
        {"data": "2026-09-02", "canale": "NEGOZIO", "pagamento": "POS", "importo": 20},
        {"data": "03/09/2026", "canale": "VENDING", "pagamento": "CONTANTI", "importo": 30},
        {"data": "03/10/2026", "canale": "NEGOZIO", "pagamento": "CONTANTI", "importo": 329.60},
    ])
    monkeypatch.setattr(server, "db", SimpleNamespace(vendite=sales))

    result = asyncio.run(server._supplemental_store_cash_sales("2026-08-31T00:00:00"))

    assert result == 429.60


def test_supplemental_store_cash_sales_counts_all_when_no_import_exists(monkeypatch):
    sales = FakeCollection([
        {"data": "2026-10-03", "canale": "NEGOZIO", "pagamento": "", "importo": 10},
        {"data": "2026-10-03", "canale": "VENDING", "pagamento": "CONTANTI", "importo": 5},
    ])
    monkeypatch.setattr(server, "db", SimpleNamespace(vendite=sales))

    assert asyncio.run(server._supplemental_store_cash_sales(None)) == 10


def test_parse_sale_date_normalizes_italian_and_iso_dates():
    assert server._parse_sale_date("03/10/2026").isoformat() == "2026-10-03"
    assert server._parse_sale_date("2026-10-03T08:30:00").isoformat() == "2026-10-03"


def test_latest_sale_date_compares_mixed_formats_chronologically():
    result = server._latest_sale_date([
        {"data": "31/08/2026"},
        {"data": "2026-10-02"},
        {"data": "03/10/2026"},
    ])

    assert result.isoformat() == "2026-10-03"


def test_dashboard_sales_trend_uses_latest_available_day_and_merges_sources(monkeypatch):
    monkeypatch.setattr(
        server,
        "db",
        SimpleNamespace(
            vendite=SalesCollection([
                {"data": "2026-09-30T10:00:00", "importo": 5},
            ]),
            db_storico_vend=SalesCollection([
                {"data": "2026-09-30T00:00:00", "importo": 10},
                {"data": "01/10/2026", "importo": 20},
            ]),
        ),
    )

    result = asyncio.run(server._dashboard_sales_trend())

    assert result["ultimo_giorno"] == "2026-10-01"
    assert result["totale_ultimo_giorno"] == 20
    assert result["totale_periodo"] == 35
    assert len(result["serie"]) == 30
    assert result["serie"][-2] == {"data": "2026-09-30", "importo": 15.0}
    assert result["serie"][-1] == {"data": "2026-10-01", "importo": 20.0}


def test_dashboard_sales_trend_handles_empty_sources(monkeypatch):
    monkeypatch.setattr(
        server,
        "db",
        SimpleNamespace(vendite=SalesCollection([]), db_storico_vend=SalesCollection([])),
    )

    result = asyncio.run(server._dashboard_sales_trend())

    assert result == {
        "ultimo_giorno": None,
        "totale_ultimo_giorno": 0,
        "variazione_pct": None,
        "totale_periodo": 0,
        "serie": [],
    }

import asyncio
import io
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient
from pymongo.errors import DuplicateKeyError
from starlette.datastructures import UploadFile

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

from backend import server


async def _async_value(value):
    return value


def fresh_db(monkeypatch):
    database = AsyncMongoMockClient()["vending_cash_test"]
    monkeypatch.setattr(server, "db", database)
    return database


def test_missing_cash_state_is_initialized_from_accounting(monkeypatch):
    database = fresh_db(monkeypatch)

    async def scenario():
        await database.parametri.insert_one({
            "nome": "GIACENZA_INIZIALE_CONTANTI_VENDING", "valore": 654.55,
        })
        await database.vending_accounting_state.insert_one({
            "id": server.VENDING_ACCOUNTING_STATE_ID,
            "baseline_importi_pagamenti": {"CONTANTI": 4314.50},
            "baseline_righe": 1,
            "created_at": "2026-10-24T10:00:00+00:00",
        })
        await database.prelievi_vending.insert_one({
            "id": "existing", "data": "2026-10-10", "importo": 3527.05,
        })
        balance = await server._vending_cash_balance()
        state = await database.cassa_vending_stato.find_one({"id": "saldo"}, {"_id": 0})
        return balance, state

    balance, state = asyncio.run(scenario())

    assert balance == 1442.0
    assert state["giacenza"] == 1442.0
    assert state["origine"] == "CONTABILITA_DERIVATA"


def test_reconciliation_repairs_only_cash_cache_and_allows_real_withdrawal(monkeypatch):
    database = fresh_db(monkeypatch)

    async def scenario():
        await database.parametri.insert_one({
            "nome": "GIACENZA_INIZIALE_CONTANTI_VENDING", "valore": 654.55,
        })
        await database.vending_accounting_state.insert_one({
            "id": server.VENDING_ACCOUNTING_STATE_ID,
            "baseline_importi_pagamenti": {"CONTANTI": 4314.50},
            "baseline_righe": 1,
            "created_at": "2026-10-24T10:00:00+00:00",
        })
        await database.prelievi_vending.insert_one({
            "id": "existing", "data": "2026-10-10", "importo": 3527.05,
        })
        await database.scontrini_vending.insert_one({
            "id": "receipt", "data": "2026-10-10", "importo": 334.40,
        })
        await database.cassa_vending_stato.insert_one({
            "id": "saldo", "giacenza": -964.80,
        })
        accounting_before = {
            "parametri": await database.parametri.find({}, {"_id": 0}).to_list(None),
            "ledger": await database.vending_accounting_state.find({}, {"_id": 0}).to_list(None),
            "prelievi": await database.prelievi_vending.find({}, {"_id": 0}).to_list(None),
            "scontrini": await database.scontrini_vending.find({}, {"_id": 0}).to_list(None),
        }
        first = await server.reconcile_vending_cash_state()
        second = await server.reconcile_vending_cash_state()
        state_after_reconciliation = await server._vending_cash_balance()
        accounting_after = {
            "parametri": await database.parametri.find({}, {"_id": 0}).to_list(None),
            "ledger": await database.vending_accounting_state.find({}, {"_id": 0}).to_list(None),
            "prelievi": await database.prelievi_vending.find({}, {"_id": 0}).to_list(None),
            "scontrini": await database.scontrini_vending.find({}, {"_id": 0}).to_list(None),
        }
        movement = await server.add_prelievo_vending(server.PrelievoVendingIn(
            data="2026-10-24",
            importo=1181.30,
            descrizione="prelievo vending 24/10",
            operatore="MC",
        ))
        return {
            "first": first,
            "second": second,
            "state_after_reconciliation": state_after_reconciliation,
            "accounting_unchanged": accounting_before == accounting_after,
            "movement": movement,
            "final_balance": await server._vending_cash_balance(),
            "backups": await database.backup_snapshots.count_documents({}),
            "audits": await database.audit_events.count_documents({
                "entita": "CASSA_VENDING_STATO",
                "azione": "RICONCILIATO",
            }),
        }

    result = asyncio.run(scenario())

    assert result["first"]["saldo_tecnico_precedente"] == -964.80
    assert result["first"]["saldo_contabile_atteso"] == 1442.0
    assert result["first"]["modificato"] is True
    assert result["second"]["gia_applicata"] is True
    assert result["state_after_reconciliation"] == 1442.0
    assert result["accounting_unchanged"] is True
    assert result["movement"]["importo"] == 1181.30
    assert result["final_balance"] == 260.70
    assert result["backups"] == 1
    assert result["audits"] == 1


def test_cash_csv_increments_vending_cash_but_cards_do_not(monkeypatch):
    database = fresh_db(monkeypatch)
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "04/10/2026 10:00;PRODOTTO CASH;5,8;1-A01;1001;Sigarette;Contanti\n"
        "04/10/2026 10:01;PRODOTTO CARTA;6,0;1-A02;1002;Sigarette;Carte\n"
    )
    async def scenario():
        await database.cassa_vending_stato.insert_one({"id": "saldo", "giacenza": 237.55})
        for index, code in enumerate(("AMMS1001", "AMMS1002"), start=1):
            await database.prodotti.insert_one({
                "id": f"product-{index}", "codice": code,
                "descrizione": f"PRODOTTO {index}",
                "giacenza_vending": 5, "venduti_vending": 0,
            })
            await database.vending.insert_one({
                "id": f"column-{index}", "colonna": f"A0{index}", "codice": code,
                "giacenza": 5, "giacenza_aggiornata_il": "2026-10-04T07:00:00+00:00",
            })
        result = await server.import_csv_vending(
            UploadFile(filename="vendite.csv", file=io.BytesIO(raw.encode()))
        )
        return result, await server._vending_accounting_ledger_summary()

    result, ledger = asyncio.run(scenario())

    assert result["contanti_aggiunti_giacenza"] == 5.8
    assert asyncio.run(server._vending_cash_balance()) == 243.35
    assert result["contabilita_allineata"] is True
    assert result["contabilita_importi_pagamenti"] == {"CONTANTI": 5.8, "CARTE": 6.0}
    assert ledger["importi_pagamenti"] == {"CONTANTI": 5.8, "CARTE": 6.0}
    assert ledger["totale"] == 11.8


def test_csv_import_can_be_undone_with_stock_column_and_cash_restored(monkeypatch):
    database = fresh_db(monkeypatch)
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "04/10/2026 10:00;PRODOTTO CASH;5,8;1-B02;1001;Sigarette;Contanti\n"
    )

    async def scenario():
        await database.cassa_vending_stato.insert_one({"id": "saldo", "giacenza": 100})
        await database.vending_accounting_snapshots.insert_one({
            "id": "previous-accounting",
            "status": "active",
            "created_at": "2026-10-03T08:00:00+00:00",
            "importi_pagamenti": {"CONTANTI": 50},
        })
        await database.prodotti.insert_one({
            "id": "product-1",
            "codice": "AMMS1001",
            "descrizione": "PRODOTTO CASH",
            "giacenza_vending": 10,
            "venduti_vending": 0,
        })
        await database.vending.insert_one({
            "id": "column-1",
            "colonna": "B02",
            "codice": "AMMS1001",
            "giacenza": 5,
            "giacenza_aggiornata_il": "2026-10-04T07:00:00+00:00",
        })
        upload = UploadFile(filename="vendite.csv", file=io.BytesIO(raw.encode()))
        imported = await server.import_csv_vending(upload)
        product_after_import = await database.prodotti.find_one({"id": "product-1"}, {"_id": 0})
        column_after_import = await database.vending.find_one({"id": "column-1"}, {"_id": 0})
        cash_after_import = await server._vending_cash_balance()
        latest = await server.ultimo_csv_vending()
        undone = await server.annulla_csv_vending(imported["batch_id"])
        product_after_undo = await database.prodotti.find_one({"id": "product-1"}, {"_id": 0})
        column_after_undo = await database.vending.find_one({"id": "column-1"}, {"_id": 0})
        cash_after_undo = await server._vending_cash_balance()
        remaining = await database.vendite.find({"batch_id": imported["batch_id"]}).to_list(10)
        active_accounting = await database.vending_accounting_snapshots.find_one(
            {"status": "active"}, {"_id": 0}
        )
        ledger_events = await database.vending_accounting_events.find({}).to_list(10)
        return (
            imported, product_after_import, column_after_import, cash_after_import, latest,
            undone, product_after_undo, column_after_undo, cash_after_undo, remaining,
            active_accounting, ledger_events,
        )

    (
        imported, product_after_import, column_after_import, cash_after_import, latest,
        undone, product_after_undo, column_after_undo, cash_after_undo, remaining,
        active_accounting, ledger_events,
    ) = asyncio.run(scenario())

    assert imported["inseriti"] == 1
    assert imported["batch_id"]
    assert product_after_import["giacenza_vending"] == 4
    assert product_after_import["venduti_vending"] == 1
    assert column_after_import["giacenza"] == 4
    assert cash_after_import == 105.8
    assert latest["id"] == imported["batch_id"]
    assert undone["rimossi"] == 1
    assert undone["contanti_rimossi_giacenza"] == 5.8
    assert product_after_undo["giacenza_vending"] == 5
    assert product_after_undo["venduti_vending"] == 0
    assert column_after_undo["giacenza"] == 5
    assert cash_after_undo == 100
    assert remaining == []
    assert undone["contabilita_ripristinata"] is True
    assert active_accounting["id"] == "previous-accounting"
    assert ledger_events == []


def test_same_csv_is_idempotent_and_does_not_change_stock_or_cash_twice(monkeypatch):
    database = fresh_db(monkeypatch)
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "04/10/2026 10:00;PRODOTTO CASH;5,8;1-B02;1001;Sigarette;Contanti\n"
    )

    async def scenario():
        await database.prodotti.insert_one({
            "id": "product-1", "codice": "AMMS1001", "descrizione": "PRODOTTO CASH",
            "giacenza_vending": 5, "venduti_vending": 0,
        })
        await database.vending.insert_one({
            "id": "column-1", "colonna": "B02", "codice": "AMMS1001", "giacenza": 5,
            "giacenza_aggiornata_il": "2026-10-04T07:00:00+00:00",
        })
        first = await server.import_csv_vending(UploadFile(filename="one.csv", file=io.BytesIO(raw.encode())))
        second = await server.import_csv_vending(UploadFile(filename="same.csv", file=io.BytesIO(raw.encode())))
        product = await database.prodotti.find_one({"id": "product-1"}, {"_id": 0})
        column = await database.vending.find_one({"id": "column-1"}, {"_id": 0})
        sales = await database.vendite.find({"sorgente": "CSV_VENDING"}).to_list(10)
        return first, second, product, column, sales, await server._vending_cash_balance()

    first, second, product, column, sales, cash = asyncio.run(scenario())

    assert first["inseriti"] == 1
    assert second["inseriti"] == 0
    assert second["righe_csv_gia_importate"] == 1
    assert column["giacenza"] == 4
    assert product["venduti_vending"] == 1
    assert len(sales) == 1
    assert cash == 5.8


def test_accounting_ledger_keeps_rows_that_leave_the_vending_window(monkeypatch):
    database = fresh_db(monkeypatch)
    first = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "04/10/2026 10:00;A;1,0;1-A01;1001;Sigarette;Contanti\n"
        "04/10/2026 10:01;B;2,0;1-A02;1002;Sigarette;Carte\n"
        "04/10/2026 10:02;C;3,0;1-A03;1003;Sigarette;PagoBancomat\n"
    )
    second = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "04/10/2026 10:01;B;2,0;1-A02;1002;Sigarette;Carte\n"
        "04/10/2026 10:02;C;3,0;1-A03;1003;Sigarette;PagoBancomat\n"
        "04/10/2026 10:03;D;4,0;1-A04;1004;Sigarette;Contanti\n"
    )

    async def scenario():
        first_result, _ = await server._store_vending_accounting_snapshot(
            first.encode(), "first.csv"
        )
        second_result, _ = await server._store_vending_accounting_snapshot(
            second.encode(), "second.csv"
        )
        return first_result, second_result, await database.vending_accounting_events.count_documents({})

    first_result, second_result, event_count = asyncio.run(scenario())

    assert first_result["totale"] == 6.0
    assert first_result["eventi_nuovi"] == 3
    assert second_result["totale"] == 10.0
    assert second_result["eventi_nuovi"] == 1
    assert second_result["importi_pagamenti"] == {
        "CONTANTI": 5.0,
        "CARTE": 2.0,
        "PAGOBANCOMAT": 3.0,
    }
    assert event_count == 4


def test_accounting_ledger_is_idempotent_when_export_switches_to_seconds(monkeypatch):
    database = fresh_db(monkeypatch)
    header = "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
    minute_export = (
        header
        + "07/10/2026 23:58;A;5,8;1-B02;1001;Sigarette;Carte\n"
        + "08/10/2026 09:12;B;6,0;1-C03;1002;Sigarette;Contanti\n"
    )
    second_export = (
        header
        + "08-10-2026 09:12:37;B;6,0;1-C03;1002;Sigarette;Contanti\n"
        + "07-10-2026 23:58:16;A;5,8;1-B02;1001;Sigarette;Carte\n"
    )

    async def scenario():
        first, _ = await server._store_vending_accounting_snapshot(
            minute_export.encode(), "minute.csv"
        )
        second, _ = await server._store_vending_accounting_snapshot(
            second_export.encode(), "seconds.csv"
        )
        events = await database.vending_accounting_events.find({}).to_list(None)
        return first, second, events

    first, second, events = asyncio.run(scenario())

    assert first["eventi_nuovi"] == 2
    assert second["eventi_nuovi"] == 0
    assert second["righe"] == 2
    assert second["totale"] == 11.8
    assert len(events) == 2
    assert len({event["csv_accounting_key"] for event in events}) == 2


def test_accounting_ledger_preserves_additional_identical_occurrence_across_formats(monkeypatch):
    database = fresh_db(monkeypatch)
    header = "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
    first_export = header + "07/10/2026 23:58;A;5,8;1-B02;1001;Sigarette;Carte\n"
    second_export = (
        header
        + "07-10-2026 23:58:50;A;5,8;1-B02;1001;Sigarette;Carte\n"
        + "07-10-2026 23:58:16;A;5,8;1-B02;1001;Sigarette;Carte\n"
    )
    third_export = (
        header
        + "07/10/2026 23:58;A;5,8;1-B02;1001;Sigarette;Carte\n"
        + "07/10/2026 23:58;A;5,8;1-B02;1001;Sigarette;Carte\n"
    )

    async def scenario():
        first, _ = await server._store_vending_accounting_snapshot(first_export.encode(), "one.csv")
        second, _ = await server._store_vending_accounting_snapshot(second_export.encode(), "two.csv")
        third, _ = await server._store_vending_accounting_snapshot(third_export.encode(), "three.csv")
        events = await database.vending_accounting_events.find({}).to_list(None)
        return first, second, third, events

    first, second, third, events = asyncio.run(scenario())

    assert first["eventi_nuovi"] == 1
    assert second["eventi_nuovi"] == 1
    assert third["eventi_nuovi"] == 0
    assert third["righe"] == 2
    assert third["totale"] == 11.6
    assert {event["csv_accounting_occurrence"] for event in events} == {1, 2}


def test_accounting_ledger_keeps_history_across_a_thousand_row_rolling_window(monkeypatch):
    database = fresh_db(monkeypatch)
    header = "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
    start = datetime(2026, 1, 1, 0, 0)

    def row(index):
        sale_at = start + timedelta(minutes=index)
        return (
            f"{sale_at:%d/%m/%Y %H:%M};P{index};1,0;1-A01;{100000 + index};"
            "Sigarette;Contanti\n"
        )

    first_export = header + "".join(row(index) for index in range(999, -1, -1))
    second_export = header + "".join(row(index) for index in range(1000, 0, -1))

    async def scenario():
        first, _ = await server._store_vending_accounting_snapshot(first_export.encode(), "first.csv")
        second, _ = await server._store_vending_accounting_snapshot(second_export.encode(), "second.csv")
        return first, second, await database.vending_accounting_events.count_documents({})

    first, second, event_count = asyncio.run(scenario())

    assert first["eventi_nuovi"] == 1000
    assert second["eventi_nuovi"] == 1
    assert second["righe"] == 1001
    assert second["totale"] == 1001.0
    assert event_count == 1001


def test_ledger_migration_recovers_imported_sales_outside_latest_snapshot(monkeypatch):
    database = fresh_db(monkeypatch)
    rows = [
        {
            "data": "2026-10-04T10:00:00", "colonna": "A01", "codice": "1001",
            "prezzo": 10.0, "pagamento": "CONTANTI", "nome": "CURRENT",
        },
        {
            "data": "2026-09-01T10:00:00", "colonna": "A02", "codice": "1002",
            "prezzo": 5.0, "pagamento": "CARTE", "nome": "OLDER",
        },
    ]
    server._assign_csv_vending_event_keys(rows)

    async def scenario():
        await database.vending_accounting_snapshots.insert_one({
            "id": "latest-snapshot",
            "status": "active",
            "created_at": "2026-10-04T11:00:00+00:00",
            "file_name": "latest.csv",
            "righe": 1,
            "data_da": rows[0]["data"],
            "data_a": rows[0]["data"],
            "importi_pagamenti": {"CONTANTI": 10.0},
            "csv_event_keys": [rows[0]["csv_event_key"]],
        })
        for row in rows:
            await database.vendite.insert_one({
                "id": row["nome"],
                "sorgente": "CSV_VENDING",
                "canale": "VENDING",
                "csv_event_key": row["csv_event_key"],
                "data": row["data"],
                "importo": row["prezzo"],
                "pagamento": row["pagamento"],
                "created_at": "2026-10-04T10:30:00+00:00",
            })
        await server._ensure_vending_accounting_state()
        return await server._vending_accounting_ledger_summary()

    summary = asyncio.run(scenario())

    assert summary["righe"] == 2
    assert summary["totale"] == 15.0
    assert summary["importi_pagamenti"] == {"CONTANTI": 10.0, "CARTE": 5.0}


def test_cumulative_csv_imports_only_the_additional_identical_occurrence(monkeypatch):
    database = fresh_db(monkeypatch)
    header = "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
    row = "04/10/2026 10:00;PRODOTTO;5,8;1-B02;1001;Sigarette;Carte\n"

    async def scenario():
        await database.prodotti.insert_one({
            "id": "product-1", "codice": "AMMS1001", "descrizione": "PRODOTTO",
            "giacenza_vending": 5, "venduti_vending": 0,
        })
        await database.vending.insert_one({
            "id": "column-1", "colonna": "B02", "codice": "AMMS1001", "giacenza": 5,
            "giacenza_aggiornata_il": "2026-10-04T07:00:00+00:00",
        })
        first = await server.import_csv_vending(UploadFile(filename="one.csv", file=io.BytesIO((header + row).encode())))
        second = await server.import_csv_vending(UploadFile(filename="two.csv", file=io.BytesIO((header + row + row).encode())))
        column = await database.vending.find_one({"id": "column-1"}, {"_id": 0})
        sales = await database.vendite.find({"sorgente": "CSV_VENDING"}).to_list(10)
        return first, second, column, sales

    first, second, column, sales = asyncio.run(scenario())

    assert first["inseriti"] == 1
    assert second["inseriti"] == 1
    assert second["righe_csv_gia_importate"] == 1
    assert column["giacenza"] == 3
    assert len(sales) == 2
    assert {sale["csv_event_occurrence"] for sale in sales} == {1, 2}


def test_csv_dedup_survives_export_switch_from_minutes_to_seconds(monkeypatch):
    database = fresh_db(monkeypatch)
    header = "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
    old_rows = (
        "07/10/2026 23:58;PRODOTTO A;5,8;1-B02;1001;Sigarette;Carte\n"
        "08/10/2026 09:12;PRODOTTO B;6,0;1-C03;1002;Sigarette;Contanti\n"
        "09/10/2026 18:30;PRODOTTO C;5,5;1-D04;1003;Sigarette;Pagobancomat\n"
    )
    new_rows = (
        "10-10-2026 11:00:05;PRODOTTO D;6,5;1-E05;1004;Sigarette;Carte\n"
        "09-10-2026 18:30:42;PRODOTTO C;5,5;1-D04;1003;Sigarette;Pagobancomat\n"
        "08-10-2026 09:12:37;PRODOTTO B;6,0;1-C03;1002;Sigarette;Contanti\n"
        "07-10-2026 23:58:16;PRODOTTO A;5,8;1-B02;1001;Sigarette;Carte\n"
    )

    async def scenario():
        for index, (code, column) in enumerate((("1001", "B02"), ("1002", "C03"), ("1003", "D04"), ("1004", "E05")), 1):
            await database.prodotti.insert_one({
                "id": f"product-{index}", "codice": f"AMMS{code}",
                "descrizione": f"PRODOTTO {chr(64 + index)}", "giacenza_vending": 10,
                "venduti_vending": 0,
            })
            await database.vending.insert_one({
                "id": f"column-{index}", "colonna": column, "codice": f"AMMS{code}",
                "giacenza": 10, "giacenza_aggiornata_il": "2026-10-07T20:00:00+00:00",
            })
        first = await server.import_csv_vending(UploadFile(
            filename="old-minute-export.csv", file=io.BytesIO((header + old_rows).encode())
        ))
        preview = await server.preview_csv_vending(UploadFile(
            filename="new-second-export.csv", file=io.BytesIO((header + new_rows).encode())
        ))
        return first, preview

    first, preview = asyncio.run(scenario())

    assert first["inseriti"] == 3
    assert preview["righe_csv_gia_importate"] == 3
    assert preview["righe"] == 1
    assert preview["vendite"][0]["codice"] == "1004"


def test_minute_compatibility_keeps_newest_additional_sale(monkeypatch):
    database = fresh_db(monkeypatch)
    header = "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
    old_row = "07/10/2026 23:58;PRODOTTO;5,8;1-B02;1001;Sigarette;Carte\n"
    current_rows = (
        "07-10-2026 23:58:50;PRODOTTO;5,8;1-B02;1001;Sigarette;Carte\n"
        "07-10-2026 23:58:16;PRODOTTO;5,8;1-B02;1001;Sigarette;Carte\n"
    )

    async def scenario():
        await database.prodotti.insert_one({
            "id": "product-1", "codice": "AMMS1001", "descrizione": "PRODOTTO",
            "giacenza_vending": 10, "venduti_vending": 0,
        })
        await database.vending.insert_one({
            "id": "column-1", "colonna": "B02", "codice": "AMMS1001", "giacenza": 10,
            "giacenza_aggiornata_il": "2026-10-07T20:00:00+00:00",
        })
        await server.import_csv_vending(UploadFile(
            filename="old.csv", file=io.BytesIO((header + old_row).encode())
        ))
        return await server.preview_csv_vending(UploadFile(
            filename="current.csv", file=io.BytesIO((header + current_rows).encode())
        ))

    preview = asyncio.run(scenario())

    assert preview["righe_csv_gia_importate"] == 1
    assert preview["righe"] == 1
    assert preview["vendite"][0]["data"] == "2026-10-07T23:58:50"


def test_sales_before_stock_snapshot_are_recorded_without_decrement(monkeypatch):
    database = fresh_db(monkeypatch)
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "04/10/2026 09:00;PRODOTTO;5,8;1-B02;1001;Sigarette;Carte\n"
        "04/10/2026 11:00;PRODOTTO;5,8;1-B02;1001;Sigarette;Carte\n"
    )

    async def scenario():
        await database.prodotti.insert_one({
            "id": "product-1", "codice": "AMMS1001", "descrizione": "PRODOTTO",
            "giacenza_vending": 5, "venduti_vending": 0,
        })
        await database.vending.insert_one({
            "id": "column-1", "colonna": "B02", "codice": "AMMS1001", "giacenza": 5,
            # 08:00 UTC = 10:00 Europe/Rome in ottobre.
            "giacenza_aggiornata_il": "2026-10-04T08:00:00+00:00",
        })
        result = await server.import_csv_vending(UploadFile(filename="mixed.csv", file=io.BytesIO(raw.encode())))
        column = await database.vending.find_one({"id": "column-1"}, {"_id": 0})
        sales = await database.vendite.find({"sorgente": "CSV_VENDING"}).sort("data", 1).to_list(10)
        return result, column, sales

    result, column, sales = asyncio.run(scenario())

    assert result["inseriti"] == 2
    assert result["righe_gia_comprese_nella_giacenza"] == 1
    assert result["righe_da_scalare"] == 1
    assert column["giacenza"] == 4
    assert [sale["stock_effect_applied"] for sale in sales] == [False, True]


def test_restored_backup_time_is_used_when_legacy_columns_have_no_timestamp(monkeypatch):
    database = fresh_db(monkeypatch)
    rows = [
        {"data": "2026-10-07T22:00:00", "colonna": "A01"},
        {"data": "2026-10-07T23:00:00", "colonna": "A01"},
    ]

    async def scenario():
        await database.vending.insert_one({"id": "column-1", "colonna": "A01", "giacenza": 4})
        await database.backup_snapshots.insert_one({
            "id": "restored",
            # 20:30 UTC = 22:30 Europe/Rome.
            "created_at": "2026-10-07T20:30:00+00:00",
            "last_restored_at": "2026-10-08T07:00:00+00:00",
        })
        return await server._annotate_csv_vending_stock_effects(rows)

    summary = asyncio.run(scenario())

    assert summary["importabile"] is True
    assert summary["righe_gia_comprese_nella_giacenza"] == 1
    assert summary["righe_da_scalare"] == 1
    assert [row["scala_giacenza"] for row in rows] == [False, True]


def test_csv_import_is_blocked_instead_of_clipping_stock_to_zero(monkeypatch):
    database = fresh_db(monkeypatch)
    header = "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
    row = "04/10/2026 10:00;PRODOTTO;5,8;1-B02;1001;Sigarette;Carte\n"

    async def scenario():
        await database.vending.insert_one({
            "id": "column-1", "colonna": "B02", "codice": "AMMS1001", "giacenza": 1,
            "giacenza_aggiornata_il": "2026-10-04T07:00:00+00:00",
        })
        upload = UploadFile(filename="too-many.csv", file=io.BytesIO((header + row + row).encode()))
        try:
            await server.import_csv_vending(upload)
        except Exception as exc:
            error = exc
        else:
            error = None
        column = await database.vending.find_one({"id": "column-1"}, {"_id": 0})
        sales = await database.vendite.find({}).to_list(10)
        return error, column, sales

    error, column, sales = asyncio.run(scenario())

    assert error is not None
    assert getattr(error, "status_code", None) == 409
    assert column["giacenza"] == 1
    assert sales == []


def test_registering_and_deleting_withdrawal_updates_vending_cash(monkeypatch):
    database = fresh_db(monkeypatch)
    asyncio.run(database.cassa_vending_stato.insert_one({"id": "saldo", "giacenza": 237.55}))

    movement = asyncio.run(server.add_prelievo_vending(server.PrelievoVendingIn(
        data="2026-10-04", importo=100, descrizione="Test", operatore="Codex"
    )))
    assert asyncio.run(server._vending_cash_balance()) == 137.55

    asyncio.run(server.del_prelievo_vending(movement["id"]))
    assert asyncio.run(server._vending_cash_balance()) == 237.55


def test_withdrawal_rejects_insufficient_vending_cash_without_writes(monkeypatch):
    database = fresh_db(monkeypatch)

    async def scenario():
        await database.cassa_vending_stato.insert_one({
            "id": server.VENDING_CASH_STATE_ID, "giacenza": 20,
        })
        with pytest.raises(server.HTTPException) as caught:
            await server.add_prelievo_vending(server.PrelievoVendingIn(
                data="2026-10-04", importo=20.01, descrizione="Troppo alto",
                idempotency_key="withdrawal-insufficient-001",
            ))
        return (
            caught.value,
            await server._vending_cash_balance(),
            await database.prelievi_vending.count_documents({}),
        )

    error, balance, movements = asyncio.run(scenario())

    assert error.status_code == 409
    assert "insufficiente" in str(error.detail).lower()
    assert balance == 20
    assert movements == 0


def test_withdrawal_idempotency_does_not_debit_vending_cash_twice(monkeypatch):
    database = fresh_db(monkeypatch)

    async def scenario():
        await database.cassa_vending_stato.insert_one({
            "id": server.VENDING_CASH_STATE_ID, "giacenza": 100,
        })
        body = server.PrelievoVendingIn(
            data="2026-10-04", importo=25, descrizione="Test",
            idempotency_key="withdrawal-idempotency-001",
        )
        first = await server.add_prelievo_vending(body)
        second = await server.add_prelievo_vending(body)
        return first, second, await server._vending_cash_balance(), await database.prelievi_vending.count_documents({})

    first, second, balance, movements = asyncio.run(scenario())

    assert first["id"] == second["id"]
    assert balance == 75
    assert movements == 1

def test_transaction_duplicate_key_reads_idempotent_document_after_abort(monkeypatch):
    prelievi = SimpleNamespace()
    state = SimpleNamespace()
    prelievi.find_one = AsyncMock()
    existing = {"id": "already-there", "idempotency_key": "collision-key-001", "importo": 25}

    async def duplicate_with_same_fingerprint(document, **_kwargs):
        existing["request_fingerprint"] = document["request_fingerprint"]
        raise DuplicateKeyError("duplicate")

    prelievi.insert_one = AsyncMock(side_effect=duplicate_with_same_fingerprint)
    state.find_one = AsyncMock(side_effect=[None, {"id": "saldo", "giacenza": 100}, {"id": "saldo", "giacenza": 100}])
    state.update_one = AsyncMock()
    prelievi.find_one.side_effect = [None, existing]
    audit = AsyncMock()
    transaction_sessions = []

    async def run_transactionally(operation):
        session = object()
        transaction_sessions.append(session)
        return await operation(session)

    database = SimpleNamespace(prelievi_vending=prelievi, cassa_vending_stato=state)
    monkeypatch.setattr(server, "db", database)
    from backend.cash_movements_router import create_cash_movements_router
    routes = create_cash_movements_router(
        get_database=lambda: database,
        record_audit_event=audit,
        cap_limit=lambda: 0,
        get_authoritative_vending_cash_snapshot=lambda: _async_value({"giacenza": 100}),
        run_transactionally=run_transactionally,
    )

    result = asyncio.run(routes["add_prelievo_vending"](server.PrelievoVendingIn(
        data="2026-10-04", importo=25, descrizione="Test",
        idempotency_key="collision-key-001",
    )))

    assert result == {**existing, "gia_registrato": True}
    assert transaction_sessions
    assert audit.await_count == 0
    assert all(call.kwargs.get("session") is None for call in prelievi.find_one.await_args_list[-1:])

def test_transaction_non_duplicate_error_propagates_without_external_read(monkeypatch):
    prelievi = SimpleNamespace()
    state = SimpleNamespace()
    prelievi.find_one = AsyncMock(return_value=None)
    prelievi.insert_one = AsyncMock(side_effect=RuntimeError("insert failed"))
    state.find_one = AsyncMock(side_effect=[None, {"id": "saldo", "giacenza": 100}, {"id": "saldo", "giacenza": 100}])
    state.update_one = AsyncMock()
    audit = AsyncMock()

    async def run_transactionally(operation):
        return await operation(object())

    database = SimpleNamespace(prelievi_vending=prelievi, cassa_vending_stato=state)
    from backend.cash_movements_router import create_cash_movements_router
    routes = create_cash_movements_router(
        get_database=lambda: database,
        record_audit_event=audit,
        cap_limit=lambda: 0,
        get_authoritative_vending_cash_snapshot=lambda: _async_value({"giacenza": 100}),
        run_transactionally=run_transactionally,
    )

    with pytest.raises(RuntimeError, match="insert failed"):
        asyncio.run(routes["add_prelievo_vending"](server.PrelievoVendingIn(
            data="2026-10-04", importo=25, descrizione="Test",
            idempotency_key="runtime-error-key-001",
        )))

    assert prelievi.find_one.await_count == 1
    assert prelievi.find_one.await_args.kwargs.get("session") is not None
    assert audit.await_count == 0


def test_withdrawal_adjustment_error_compensates_insert(monkeypatch):
    database = fresh_db(monkeypatch)

    async def scenario():
        await database.cassa_vending_stato.insert_one({
            "id": server.VENDING_CASH_STATE_ID, "giacenza": 100,
        })
        collection_type = type(database.cassa_vending_stato)

        async def fail_adjustment(*args, **kwargs):
            raise RuntimeError("adjustment failed")

        monkeypatch.setattr(collection_type, "update_one", fail_adjustment)
        with pytest.raises(RuntimeError, match="adjustment failed"):
            await server.add_prelievo_vending(server.PrelievoVendingIn(
                data="2026-10-04", importo=25, descrizione="Test",
                idempotency_key="withdrawal-adjustment-error-001",
            ))
        return await database.prelievi_vending.count_documents({})

    movements = asyncio.run(scenario())
    assert movements == 0


def test_withdrawal_delete_error_restores_original_document(monkeypatch):
    database = fresh_db(monkeypatch)
    original = {"id": "withdrawal-to-restore", "data": "2026-10-04", "importo": 25, "descrizione": "Test"}

    async def scenario():
        await database.cassa_vending_stato.insert_one({
            "id": server.VENDING_CASH_STATE_ID, "giacenza": 75,
        })
        await database.prelievi_vending.insert_one(dict(original))
        collection_type = type(database.cassa_vending_stato)

        async def fail_adjustment(*args, **kwargs):
            raise RuntimeError("adjustment failed")

        monkeypatch.setattr(collection_type, "update_one", fail_adjustment)
        with pytest.raises(RuntimeError, match="adjustment failed"):
            await server.del_prelievo_vending(original["id"])
        return await database.prelievi_vending.find_one({"id": original["id"]}, {"_id": 0})

    restored = asyncio.run(scenario())
    assert restored == original


def test_updating_opening_cash_adjusts_current_balance_by_the_delta(monkeypatch):
    database = fresh_db(monkeypatch)

    async def scenario():
        await database.parametri.insert_one({
            "nome": "GIACENZA_INIZIALE_CONTANTI_VENDING",
            "valore": 50,
        })
        await database.cassa_vending_stato.insert_one({
            "id": server.VENDING_CASH_STATE_ID,
            "giacenza": 200,
        })
        updated = await server.update_parametro(
            "GIACENZA_INIZIALE_CONTANTI_VENDING",
            server.ParametroIn(valore=125),
        )
        return updated, await server._vending_cash_balance()

    updated, balance = asyncio.run(scenario())

    assert updated["valore"] == 125
    assert balance == 275.0


def test_receipts_reduce_net_withdrawal_and_combined_balance():
    result = server._calculate_dashboard_balances(
        [(3445.30, "Contanti"), (2445, "Carte")],
        saldo_cassa=-664.72,
        totale_prelievi_vending=3207.75,
        totale_scontrini=100,
    )

    assert result["prelievoVendingLordo"] == 3207.75
    assert result["prelievoVendingNetto"] == 3107.75
    assert result["prelievoVending"] == 3107.75
    assert result["giacenzaVendingContanti"] == 237.55
    assert result["scontriniVending"] == 100
    assert result["saldoCassaNegozioEVending"] == 2443.03

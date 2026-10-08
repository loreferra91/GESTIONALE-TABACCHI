import asyncio
import io
import os

from mongomock_motor import AsyncMongoMockClient
from starlette.datastructures import UploadFile

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

from backend import server


def fresh_db(monkeypatch):
    database = AsyncMongoMockClient()["vending_cash_test"]
    monkeypatch.setattr(server, "db", database)
    return database


def test_legacy_withdrawal_total_initializes_current_vending_cash(monkeypatch):
    database = fresh_db(monkeypatch)
    asyncio.run(database.prelievi_vending.insert_one({"id": "legacy", "importo": 237.55}))

    assert asyncio.run(server._vending_cash_balance()) == 237.55


def test_cash_csv_increments_vending_cash_but_cards_do_not(monkeypatch):
    database = fresh_db(monkeypatch)
    asyncio.run(database.prelievi_vending.insert_one({"id": "legacy", "importo": 237.55}))
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "04/10/2026 10:00;PRODOTTO CASH;5,8;1-A01;1001;Sigarette;Contanti\n"
        "04/10/2026 10:01;PRODOTTO CARTA;6,0;1-A02;1002;Sigarette;Carte\n"
    )
    upload = UploadFile(filename="vendite.csv", file=io.BytesIO(raw.encode()))

    result = asyncio.run(server.import_csv_vending(upload))
    snapshot = asyncio.run(database.vending_accounting_snapshots.find_one(
        {"status": "active"}, {"_id": 0}
    ))

    assert result["contanti_aggiunti_giacenza"] == 5.8
    assert asyncio.run(server._vending_cash_balance()) == 243.35
    assert result["contabilita_allineata"] is True
    assert result["contabilita_importi_pagamenti"] == {"CONTANTI": 5.8, "CARTE": 6.0}
    assert snapshot["importi_pagamenti"] == {"CONTANTI": 5.8, "CARTE": 6.0}
    assert snapshot["totale"] == 11.8


def test_csv_import_can_be_undone_with_stock_column_and_cash_restored(monkeypatch):
    database = fresh_db(monkeypatch)
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "04/10/2026 10:00;PRODOTTO CASH;5,8;1-B02;1001;Sigarette;Contanti\n"
    )

    async def scenario():
        await database.prelievi_vending.insert_one({"id": "legacy", "importo": 100})
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
        reverted_accounting = await database.vending_accounting_snapshots.find_one(
            {"source_batch_id": imported["batch_id"]}, {"_id": 0}
        )
        return (
            imported, product_after_import, column_after_import, cash_after_import, latest,
            undone, product_after_undo, column_after_undo, cash_after_undo, remaining,
            active_accounting, reverted_accounting,
        )

    (
        imported, product_after_import, column_after_import, cash_after_import, latest,
        undone, product_after_undo, column_after_undo, cash_after_undo, remaining,
        active_accounting, reverted_accounting,
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
    assert reverted_accounting["status"] == "reverted"


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
    asyncio.run(database.prelievi_vending.insert_one({"id": "legacy", "importo": 237.55}))
    asyncio.run(server._vending_cash_balance())

    movement = asyncio.run(server.add_prelievo_vending(server.PrelievoVendingIn(
        data="2026-10-04", importo=100, descrizione="Test", operatore="Codex"
    )))
    assert asyncio.run(server._vending_cash_balance()) == 137.55

    asyncio.run(server.del_prelievo_vending(movement["id"]))
    assert asyncio.run(server._vending_cash_balance()) == 237.55


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


def test_receipts_are_separate_from_cash_withdrawal_and_combined_balance():
    result = server._calculate_dashboard_balances(
        [(3445.30, "Contanti"), (2445, "Carte")],
        saldo_cassa=-664.72,
        totale_prelievi_vending=3207.75,
        totale_scontrini=100,
    )

    assert result["prelievoVending"] == 3207.75
    assert result["giacenzaVendingContanti"] == 237.55
    assert result["scontriniVending"] == 100
    assert result["saldoCassaNegozioEVending"] == 2543.03

"""Critical invariants for reconciled demand and linked supplier receipts."""
import asyncio
import io
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient
import openpyxl

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

from backend import server
from backend.auto_order_planning import commercial_day, is_orderable_product, pending_by_code, projected_shortage, reconcile_sales, rounded_order_quantity
from backend.auto_order_migrations import AUTO_ORDER_2_SCHEMA_ID, migrate_auto_order_2_schema
from backend.auto_order_router import RiconciliaCaricoIn
from backend.orders_router import parse_auto_order_excel


def daily(code, day, quantity, channel=None, source=None):
    return {"_id": {"codice": code, "day": day, "canale": channel, "sorgente": source}, "tot": quantity}


def test_native_sales_keep_shop_and_vending_separate():
    result = reconcile_sales(
        [daily("P", "2026-11-10", 30, "NEGOZIO", "BULK"),
         daily("P", "2026-11-10", 4, "VENDING", "CSV_VENDING"),
         daily("P", "2026-11-12", 5, "NEGOZIO", "MANUALE")],
        date(2026, 11, 12), 10, 30,
    )["P"]
    assert result["venduto_lungo"] == result["venduto_breve"] == 39
    assert result["venduto_negozio"] == 35
    assert result["venduto_vending"] == 4
    assert result["venduto_negozio_breve"] == 35
    assert result["venduto_vending_breve"] == 4
    assert result["qualita_dati"] == "DATI_AFFIDABILI"
    assert result["fonti_utilizzate"] == ["CSV_VENDING", "VENDITE"]


def test_native_sales_keep_short_and_long_channel_windows_distinct():
    result = reconcile_sales(
        [daily("P", "2026-10-20", 30, "VENDING", "CSV_VENDING"),
         daily("P", "2026-11-09", 6, "NEGOZIO", "MANUALE")],
        date(2026, 11, 9), 10, 30,
    )["P"]
    assert result["venduto_breve"] == 6
    assert result["venduto_lungo"] == 36
    assert result["venduto_negozio_breve"] == 6
    assert result["venduto_vending_breve"] == 0
    assert result["venduto_negozio"] == 6
    assert result["venduto_vending"] == 30


def test_accounting_change_entries_are_not_orderable(isolated_database):
    db = isolated_database
    today = datetime.now(ZoneInfo("Europe/Rome")).date().isoformat()

    async def scenario():
        await db.prodotti.insert_many([
            {"id": "change-1", "codice": "0000001", "descrizione": "resto non erogato", "categoria": "SIGARETTE", "prezzo": 1, "giacenza_negozio": 0},
            {"id": "change-2", "codice": "11223344", "descrizione": "Resti non erogati", "categoria": "SIGARETTE", "prezzo": 80.2, "giacenza_negozio": 0},
            {"id": "real", "codice": "AMMS338", "descrizione": "WINSTON BLUE", "categoria": "SIGARETTE", "prezzo": 5.8, "giacenza_negozio": 0},
        ])
        await db.vendite.insert_many([
            {"id": code, "codice": code, "data": today + "T12:00:00", "quantita": 1, "canale": "NEGOZIO", "sorgente": "MANUALE"}
            for code in ("0000001", "11223344", "AMMS338")
        ])
        return await server.auto_order()

    proposal = asyncio.run(scenario())
    codes = {row["codice"] for row in [*proposal["righe"], *proposal["esclusi"]]}
    assert codes == {"AMMS338"}
    with pytest.raises(HTTPException) as error:
        server._auto_order_selected_rows(proposal, [server.AutoOrderRigaIn(codice="11223344", quantita=10)])
    assert error.value.status_code == 422
    assert not is_orderable_product({"descrizione": "resto non erogato"})
    assert not is_orderable_product({"descrizione": "vendibile", "ordinabile_auto_order": False})


def test_existing_receipt_closes_legacy_supplier_order_without_reloading_stock(isolated_database):
    db = isolated_database

    async def scenario():
        await db.prodotti.insert_one({"id": "product", "codice": "A", "giacenza_negozio": 42, "acquistati": 30})
        await db.ordini_fornitore.insert_one({"id": "order", "batch_key": "order-key", "data": "2026-11-01", "stato": "CREATO"})
        await db.ordini_fornitore_righe.insert_many([
            {"id": "line-a", "batch_key": "order-key", "codice": "A", "quantita": 10},
            {"id": "line-b", "batch_key": "order-key", "codice": "B", "quantita": 20},
        ])
        await db.ricezioni_merce.insert_one({"id": "receipt", "status": "completed", "data": "2026-11-06", "file_sorgente": "ordine 01-11-2026.xlsx"})
        await db.storico_ordini.insert_many([
            {"id": "receipt-a", "batch_id": "receipt", "codice": "A", "quantita": 10},
            {"id": "receipt-b", "batch_id": "receipt", "codice": "B", "quantita": 10},
            {"id": "receipt-c", "batch_id": "receipt", "codice": "C", "quantita": 10},
        ])
        candidates = (await server.auto_order_module["carichi_esistenti"]("order-key"))["carichi"]
        assert len(candidates) == 1
        assert candidates[0]["mancanti"] == {"B": 10}
        assert candidates[0]["extra"] == {"C": 10}
        with pytest.raises(HTTPException) as error:
            await server.auto_order_module["chiudi_con_carico"](
                "order-key", "receipt", RiconciliaCaricoIn(version=0))
        assert error.value.status_code == 422
        result = await server.auto_order_module["chiudi_con_carico"](
            "order-key", "receipt", RiconciliaCaricoIn(version=0, conferma_differenze=True))
        with pytest.raises(HTTPException) as duplicate:
            await server.auto_order_module["chiudi_con_carico"](
                "order-key", "receipt", RiconciliaCaricoIn(version=1, conferma_differenze=True))
        assert duplicate.value.status_code == 409
        with pytest.raises(HTTPException) as annulment:
            await server.annulla_bulk_carico("receipt")
        assert annulment.value.status_code == 409
        return result, await db.prodotti.find_one({"codice": "A"}), await db.ordini_fornitore.find_one({"id": "order"}), await db.ordini_fornitore_righe.find_one({"id": "line-b"}), await db.ricezioni_merce.find_one({"id": "receipt"})

    result, product, order, short_line, receipt = asyncio.run(scenario())
    assert result["ok"] is True and result["mancanti"] == {"B": 10}
    assert product["giacenza_negozio"] == 42 and product["acquistati"] == 30
    assert order["stato"] == "RICEVUTO" and order["last_receipt_batch_id"] == "receipt"
    assert short_line["quantita_ricevuta"] == 10 and short_line["quantita_non_consegnata"] == 10
    assert short_line["quantita_residua"] == 0
    assert receipt["legacy_reconciled_order_batch_key"] == "order-key"


def test_pending_uses_residual_and_delivery_horizon():
    batch = [{"batch_key": "one", "stato": "CREATO"}]
    rows = [{"batch_key": "one", "codice": "P", "quantita": 100, "quantita_ricevuta": 60,
             "data_consegna_prevista": "2026-11-15"}]
    pending = pending_by_code(batch, rows, date(2026, 11, 9), 14)["P"]
    assert pending["quantita_in_arrivo"] == 40
    assert pending["ricezioni"] == [{"data": "2026-11-15", "quantita": 40}]
    assert rounded_order_quantity(60 - (10 + pending["quantita_in_arrivo"]), 10) == 10
    assert projected_shortage(10, 3, pending["ricezioni"], date(2026, 11, 9), 10) == "2026-11-12"


def test_batch_delivery_update_supersedes_original_line_date():
    pending = pending_by_code(
        [{"batch_key": "one", "stato": "INVIATO", "data_consegna_prevista": "2026-11-12"}],
        [{"batch_key": "one", "codice": "P", "quantita": 10, "data_consegna_prevista": "2026-11-30"}],
        date(2026, 11, 9), 7,
    )["P"]
    assert pending["quantita_in_arrivo"] == 10
    assert pending["ricezioni"][0]["data"] == "2026-11-12"


def test_undated_pending_is_not_available():
    pending = pending_by_code(
        [{"batch_key": "one", "stato": "CONFERMATO"}],
        [{"batch_key": "one", "codice": "P", "quantita": 50}],
        date(2026, 11, 9), 14,
    )["P"]
    assert pending["quantita_in_arrivo"] == 0
    assert pending["quantita_senza_data"] == 50


def test_commercial_days_follow_rome_dst():
    assert commercial_day("2026-03-29T22:30:00Z") == date(2026, 3, 30)
    assert commercial_day("2026-10-25T23:30:00Z") == date(2026, 10, 26)
    assert commercial_day("2026-10-25T10:00:00") == date(2026, 10, 25)
    assert commercial_day("25/10/2026") == date(2026, 10, 25)


def test_schema_migration_is_idempotent_and_preserves_legacy_orders():
    db = AsyncMongoMockClient()["auto_order_migration_test"]
    async def scenario():
        await db.ordini_fornitore.insert_one({"id": "legacy", "batch_key": "old", "stato": "CREATO"})
        await migrate_auto_order_2_schema(db)
        await migrate_auto_order_2_schema(db)
        return await db.app_migrations.count_documents({"id": AUTO_ORDER_2_SCHEMA_ID}), await db.ordini_fornitore.find_one({"id": "legacy"})
    count, legacy = asyncio.run(scenario())
    assert count == 1 and legacy["stato"] == "CREATO"


def test_reconciliation_handles_more_than_5000_product_groups():
    rows = [daily(f"P{index:05d}", "2026-11-09", 1, "NEGOZIO", "MANUALE") for index in range(6001)]
    result = reconcile_sales(rows, date(2026, 11, 9), 10, 30)
    assert len(result) == 6001
    assert result["P06000"]["venduto_lungo"] == 1


@pytest.fixture
def isolated_database(monkeypatch):
    database = AsyncMongoMockClient()["auto_order_2_test"]
    monkeypatch.setattr(server, "db", database)

    async def fake_backup(*_args, **_kwargs):
        return {"id": "backup-test"}

    monkeypatch.setattr(server, "create_backup_snapshot", fake_backup)
    return database


def test_linked_partial_receipts_update_residual_once(isolated_database):
    db = isolated_database

    async def scenario():
        await db.prodotti.insert_one({"id": "product", "codice": "P", "descrizione": "P", "giacenza_negozio": 10, "acquistati": 0})
        await db.ordini_fornitore.insert_one({"id": "supplier", "batch_key": "supplier-key", "stato": "CONFERMATO", "status": "COMPLETED"})
        await db.ordini_fornitore_righe.insert_one({"id": "line", "batch_key": "supplier-key", "codice": "P", "quantita": 100,
                                                    "quantita_ordinata": 100, "quantita_ricevuta": 0, "quantita_residua": 100, "stato": "CONFERMATO"})
        first_body = server.BulkOrdineIn(data="2026-11-09", idempotency_key="receipt-60", supplier_order_batch_key="supplier-key",
                                         righe=[{"codice": "P", "quantita": 60, "prezzo": 1}])
        first = await server.bulk_carico(first_body)
        duplicate = await server.bulk_carico(first_body)
        after_first = await db.ordini_fornitore_righe.find_one({"id": "line"})
        second = await server.bulk_carico(server.BulkOrdineIn(
            data="2026-11-10", idempotency_key="receipt-40", supplier_order_batch_key="supplier-key",
            righe=[{"codice": "P", "quantita": 40, "prezzo": 1}],
        ))
        return first, duplicate, after_first, second, await db.ordini_fornitore_righe.find_one({"id": "line"}), await db.prodotti.find_one({"codice": "P"})

    first, duplicate, after_first, second, line, product = asyncio.run(scenario())
    assert first["caricate"] == 1 and duplicate["gia_registrato"] is True
    assert after_first["quantita_ricevuta"] == 60 and after_first["quantita_residua"] == 40
    assert after_first["stato"] == "PARZIALMENTE_RICEVUTO"
    assert second["caricate"] == 1 and line["quantita_ricevuta"] == 100
    assert line["quantita_residua"] == 0 and line["stato"] == "RICEVUTO"
    assert product["giacenza_negozio"] == 110


def test_linked_receipt_rejects_excess(isolated_database):
    db = isolated_database

    async def scenario():
        await db.ordini_fornitore.insert_one({"id": "supplier", "batch_key": "supplier-key", "stato": "CONFERMATO", "status": "COMPLETED"})
        await db.ordini_fornitore_righe.insert_one({"id": "line", "batch_key": "supplier-key", "codice": "P", "quantita": 10,
                                                    "quantita_ordinata": 10, "quantita_ricevuta": 0, "stato": "CONFERMATO"})
        with pytest.raises(HTTPException) as error:
            await server.bulk_carico(server.BulkOrdineIn(idempotency_key="excess", supplier_order_batch_key="supplier-key",
                                                         righe=[{"codice": "P", "quantita": 11, "prezzo": 1}]))
        return error.value.status_code, await db.prodotti.count_documents({})

    status, products = asyncio.run(scenario())
    assert status == 422 and products == 0


def test_auto_order_counts_dated_pending_and_has_stable_snapshot(isolated_database):
    db = isolated_database
    today = datetime.now(ZoneInfo("Europe/Rome")).date()

    async def scenario():
        await db.prodotti.insert_one({"id": "product", "codice": "P", "descrizione": "P", "categoria": "ACCESSORI",
                                      "giacenza_negozio": 10, "prezzo": 5})
        await db.vendite.insert_many([
            {"id": str(day), "codice": "P", "data": (today - timedelta(days=day)).isoformat() + "T12:00:00",
             "quantita": 3, "canale": "NEGOZIO", "sorgente": "MANUALE"}
            for day in range(30)
        ])
        await db.ordini_fornitore.insert_one({"id": "supplier", "batch_key": "supplier-key", "stato": "CONFERMATO", "status": "COMPLETED"})
        await db.ordini_fornitore_righe.insert_one({"id": "line", "batch_key": "supplier-key", "codice": "P", "quantita": 50,
                                                    "quantita_ricevuta": 0, "stato": "CONFERMATO",
                                                    "data_consegna_prevista": (today + timedelta(days=5)).isoformat()})
        first = await server.auto_order()
        second = await server.auto_order()
        return first, second

    first, second = asyncio.run(scenario())
    row = next(row for row in first["esclusi"] if row["codice"] == "P")
    assert row["quantita_in_arrivo"] == 50
    assert row["qta_da_ordinare"] == 0
    assert row["posizione_inventariale"] == 60
    assert first["snapshot_key"] == second["snapshot_key"]


def test_excel_history_presence_and_changes_do_not_affect_native_sales(isolated_database):
    db = isolated_database
    today = datetime.now(ZoneInfo("Europe/Rome")).date()

    async def scenario():
        await db.prodotti.insert_one({
            "id": "product", "codice": "P", "descrizione": "P", "categoria": "ACCESSORI",
            "giacenza_negozio": 3, "giacenza_vending": 999,
        })
        await db.vending.insert_one({"id": "column", "codice": "P", "giacenza": 4})
        await db.vendite.insert_many([
            {"id": "shop", "codice": "P", "data": today.isoformat() + "T10:00:00", "quantita": 18,
             "canale": "NEGOZIO", "sorgente": "BULK"},
            {"id": "vending", "codice": "P", "data": today.isoformat() + "T11:00:00", "quantita": 6,
             "canale": "VENDING", "sorgente": "CSV_VENDING", "csv_event_key": "event-1"},
        ])
        absent = await server.auto_order()
        await db.db_storico_vend.insert_one({"data": today.isoformat(), "codice": "P", "quantita": 400})
        present = await server.auto_order()
        await db.db_storico_vend.update_one({"codice": "P"}, {"$set": {"quantita": 900}})
        changed = await server.auto_order()
        return absent, present, changed

    absent, present, changed = asyncio.run(scenario())
    assert absent["snapshot_key"] == present["snapshot_key"] == changed["snapshot_key"]
    for proposal in (absent, present, changed):
        row = next(row for row in [*proposal["righe"], *proposal["esclusi"]] if row["codice"] == "P")
        assert row["venduto_lungo"] == 24
        assert row["venduto_negozio"] == 18
        assert row["venduto_vending"] == 6
        assert row["giacenza_negozio"] == 3
        assert row["giacenza_vending"] == 4
        assert row["domanda_negozio_gg"] > row["domanda_vending_gg"] > 0
        assert row["copertura_gg"] == row["copertura_negozio_gg"]
        assert row["copertura_vending_gg"] > row["copertura_negozio_gg"]
        assert row["fonte_domanda"] == "VENDITE"
        assert "ultimo_import_excel" not in proposal["source_watermarks"]


def test_native_italian_dates_are_counted_in_configured_long_window(isolated_database):
    db = isolated_database
    today = datetime.now(ZoneInfo("Europe/Rome")).date()
    older = today - timedelta(days=45)

    async def scenario():
        await db.parametri.insert_one({"nome": "AUTO_ORDER_FINESTRA_LUNGA_GG", "valore": 90})
        await db.prodotti.insert_one({"id": "product", "codice": "P", "descrizione": "P", "categoria": "ACCESSORI", "giacenza_negozio": 1})
        await db.vendite.insert_one({"id": "older-native", "codice": "P", "data": older.strftime("%d/%m/%Y"),
                                    "quantita": 9, "canale": "NEGOZIO", "sorgente": "MANUALE"})
        proposal = await server.auto_order()
        return next(row for row in [*proposal["righe"], *proposal["esclusi"]] if row["codice"] == "P")

    row = asyncio.run(scenario())
    assert row["venduto_breve"] == 0
    assert row["venduto_lungo"] == row["venduto_negozio"] == 9


def test_vending_demand_uses_vending_stock_and_suggests_restock(isolated_database):
    db = isolated_database
    today = datetime.now(ZoneInfo("Europe/Rome")).date()

    async def scenario():
        await db.prodotti.insert_one({"id": "product", "codice": "P", "descrizione": "P", "categoria": "ACCESSORI", "giacenza_negozio": 40})
        await db.vending.insert_one({"id": "column", "codice": "P", "giacenza": 1})
        await db.vendite.insert_one({"id": "vending-sale", "codice": "P", "data": today.isoformat() + "T12:00:00",
                                     "quantita": 10, "canale": "VENDING", "sorgente": "CSV_VENDING", "csv_event_key": "event-1"})
        proposal = await server.auto_order()
        return next(row for row in [*proposal["righe"], *proposal["esclusi"]] if row["codice"] == "P")

    row = asyncio.run(scenario())
    assert row["domanda_negozio_gg"] == 0
    assert row["domanda_vending_gg"] > 0
    assert row["copertura_negozio_gg"] is None
    assert row["copertura_gg"] == row["copertura_vending_gg"]
    assert row["stato"] == "CONTROLLO MANUALE"
    assert "RICARICA" in row["motivo"]


def test_native_sale_order_and_linked_receipt_complete_without_excel(isolated_database):
    db = isolated_database
    today = datetime.now(ZoneInfo("Europe/Rome")).date()

    async def scenario():
        await db.prodotti.insert_one({
            "id": "product", "codice": "P", "descrizione": "P", "categoria": "ACCESSORI",
            "giacenza_negozio": 40, "venduti_negozio": 0, "acquistati": 0, "prezzo": 1,
        })
        await server.add_vendita(server.VenditaIn(
            data=today.isoformat() + "T12:00:00", codice="P", quantita=35, canale="NEGOZIO",
        ), idempotency_key="native-sale-cycle-001")
        after_sale = await db.prodotti.find_one({"codice": "P"})
        proposal = await server.auto_order()
        row = next(row for row in proposal["righe"] if row["codice"] == "P")
        order = await server.conferma_auto_order(server.AutoOrderConfermaIn(
            snapshot_key=proposal["snapshot_key"], idempotency_key="native-order-cycle-001",
            data_consegna_prevista=today + timedelta(days=5),
            righe=[server.AutoOrderRigaIn(codice="P", quantita=row["qta_da_ordinare"])],
        ))
        receipt = await server.bulk_carico(server.BulkOrdineIn(
            data=today.isoformat(), idempotency_key="native-receipt-cycle-001",
            supplier_order_batch_key=order["batch_key"],
            righe=[{"codice": "P", "quantita": row["qta_da_ordinare"], "prezzo": 1}],
        ))
        after_receipt = await db.prodotti.find_one({"codice": "P"})
        order_line = await db.ordini_fornitore_righe.find_one({"batch_key": order["batch_key"]})
        refreshed = await server.auto_order()
        return after_sale, row, order, receipt, after_receipt, order_line, refreshed

    after_sale, row, order, receipt, after_receipt, order_line, refreshed = asyncio.run(scenario())
    assert after_sale["giacenza_negozio"] == 5
    assert after_sale["venduti_negozio"] == 35
    assert row["venduto_negozio"] == row["venduto_lungo"] == 35
    assert row["qta_da_ordinare"] > 0
    assert order["status"] == "COMPLETED"
    assert receipt["caricate"] == 1
    assert order_line["quantita_ricevuta"] == row["qta_da_ordinare"]
    assert after_receipt["giacenza_negozio"] == 5 + row["qta_da_ordinare"]
    assert refreshed["snapshot_key"] != order["snapshot_key"]


def test_linked_receipt_undo_restores_order_residual(isolated_database):
    db = isolated_database

    async def scenario():
        await db.prodotti.insert_one({"id": "product", "codice": "P", "descrizione": "P", "giacenza_negozio": 10, "acquistati": 0})
        await db.ordini_fornitore.insert_one({"id": "supplier", "batch_key": "supplier-key", "stato": "CONFERMATO", "status": "COMPLETED"})
        await db.ordini_fornitore_righe.insert_one({"id": "line", "batch_key": "supplier-key", "codice": "P", "quantita": 100,
                                                    "quantita_ordinata": 100, "quantita_ricevuta": 0, "quantita_residua": 100, "stato": "CONFERMATO"})
        receipts = []
        for quantity, key in ((60, "first"), (40, "second")):
            receipts.append(await server.bulk_carico(server.BulkOrdineIn(
                data="2026-11-09", idempotency_key=key, supplier_order_batch_key="supplier-key",
                righe=[{"codice": "P", "quantita": quantity, "prezzo": 1}],
            )))
        await server.annulla_bulk_carico(receipts[1]["batch_id"])
        after_second_undo = await db.ordini_fornitore_righe.find_one({"id": "line"})
        await server.annulla_bulk_carico(receipts[0]["batch_id"])
        return after_second_undo, await db.ordini_fornitore_righe.find_one({"id": "line"}), await db.prodotti.find_one({"codice": "P"})

    partial, original, product = asyncio.run(scenario())
    assert partial["quantita_ricevuta"] == 60 and partial["quantita_residua"] == 40
    assert original["quantita_ricevuta"] == 0 and original["quantita_residua"] == 100
    assert product["giacenza_negozio"] == 10


def test_confirmation_rejects_stale_snapshot_and_invalid_lot(isolated_database, monkeypatch):
    async def fake_ao():
        return {"snapshot_key": "current", "data_quality": "DATI_AFFIDABILI", "righe": [
            {"codice": "P", "descrizione": "P", "categoria": "SIGARETTE", "lotto_ordine": 10,
             "qta_da_ordinare": 10, "prezzo": 5, "totale": 50, "anomalia": False}
        ], "esclusi": []}

    monkeypatch.setattr(server, "auto_order", fake_ao)

    async def scenario():
        with pytest.raises(HTTPException) as stale:
            await server.conferma_auto_order(server.AutoOrderConfermaIn(
                snapshot_key="old", righe=[server.AutoOrderRigaIn(codice="P", quantita=10)],
            ))
        with pytest.raises(HTTPException) as lot:
            await server.conferma_auto_order(server.AutoOrderConfermaIn(
                snapshot_key="current", righe=[server.AutoOrderRigaIn(codice="P", quantita=13)],
            ))
        return stale.value.status_code, lot.value.status_code

    assert asyncio.run(scenario()) == (409, 422)


def test_concurrent_confirmation_creates_one_complete_batch(isolated_database, monkeypatch):
    db = isolated_database
    async def fake_ao():
        return {"snapshot_key": "stable", "data_quality": "DATI_AFFIDABILI", "data_riferimento_domanda": "2026-11-09",
                "righe": [{"codice": "P", "descrizione": "P", "categoria": "ACCESSORI", "lotto_ordine": 1,
                           "qta_da_ordinare": 2, "prezzo": 5, "totale": 10, "anomalia": False}], "esclusi": []}

    monkeypatch.setattr(server, "auto_order", fake_ao)

    async def scenario():
        await db.ordini_fornitore.create_index("batch_key", unique=True)
        body = server.AutoOrderConfermaIn(snapshot_key="stable", idempotency_key="same-request",
                                         righe=[server.AutoOrderRigaIn(codice="P", quantita=2)])
        outcomes = await asyncio.gather(server.conferma_auto_order(body), server.conferma_auto_order(body), return_exceptions=True)
        return outcomes, await db.ordini_fornitore.count_documents({}), await db.ordini_fornitore_righe.count_documents({})

    outcomes, batches, lines = asyncio.run(scenario())
    assert batches == 1 and lines == 1
    assert sum(isinstance(item, dict) and item.get("duplicate") is False for item in outcomes) == 1
    assert all(isinstance(item, dict) or isinstance(item, HTTPException) and item.status_code == 409 for item in outcomes)


def test_supplier_delivery_update_uses_version_and_audit(isolated_database):
    db = isolated_database
    async def seed():
        await db.ordini_fornitore.insert_one({"id": "supplier", "batch_key": "supplier-key", "stato": "CONFERMATO", "status": "COMPLETED", "version": 0})
        await db.ordini_fornitore_righe.insert_one({"id": "line", "batch_key": "supplier-key", "codice": "P", "quantita": 20, "stato": "CONFERMATO"})
    asyncio.run(seed())
    delivery = (datetime.now(ZoneInfo("Europe/Rome")).date() + timedelta(days=3)).isoformat()
    client = TestClient(server.app)
    first = client.patch("/api/auto-order/forniture/supplier-key", json={"version": 0, "stato": "INVIATO", "data_consegna_prevista": delivery})
    stale = client.patch("/api/auto-order/forniture/supplier-key", json={"version": 0, "stato": "ANNULLATO"})
    assert first.status_code == 200 and first.json()["version"] == 1
    assert first.json()["data_consegna_prevista"] == delivery
    assert stale.status_code == 409


def test_confirmed_excel_uses_saved_quantities_and_imports_reference(isolated_database, monkeypatch):
    db = isolated_database
    async def fake_ao():
        return {"snapshot_key": "changed-now", "righe": [], "esclusi": [], "parametri": {},
                "data_riferimento_domanda": "2026-11-09"}
    monkeypatch.setattr(server, "auto_order", fake_ao)

    async def seed():
        await db.ordini_fornitore.insert_one({"id": "supplier", "batch_key": "confirmed-batch", "snapshot_key": "old",
                                             "stato": "CONFERMATO", "status": "COMPLETED", "ordinati": 1})
        await db.ordini_fornitore_righe.insert_one({"id": "line", "batch_key": "confirmed-batch", "riga": 1,
                                                    "codice": "P", "descrizione": "P", "categoria": "ACCESSORI",
                                                    "quantita": 20, "prezzo": 5, "totale": 100})
    asyncio.run(seed())
    client = TestClient(server.app)
    stale = client.get("/api/auto-order/excel", params={"snapshot_key": "old", "selezione": '[{"codice":"P","quantita":20}]'})
    exported = client.get("/api/auto-order/excel", params={"batch_key": "confirmed-batch"})
    assert stale.status_code == 409
    assert exported.status_code == 200
    book = openpyxl.load_workbook(io.BytesIO(exported.content), read_only=True, data_only=True)
    assert book["Ordine fornitore"]["C5"].value == 20
    preview = parse_auto_order_excel(exported.content)
    assert preview["supplier_order_batch_key"] == "confirmed-batch"
    assert preview["righe"][0]["quantita"] == 20


def test_legacy_import_state_does_not_change_native_demand(isolated_database):
    db = isolated_database
    async def scenario():
        today = datetime.now(ZoneInfo("Europe/Rome")).date()
        await db.prodotti.insert_one({"id": "product", "codice": "P", "descrizione": "P", "categoria": "ACCESSORI", "giacenza_negozio": 0})
        await db.vendite.insert_one({"id": "sale", "codice": "P", "data": today.isoformat() + "T12:00:00", "quantita": 20, "canale": "NEGOZIO", "sorgente": "MANUALE"})
        baseline = await server.auto_order()
        await db.auto_order_import_state.insert_one({"_id": "excel-full", "status": "PROCESSING"})
        proposal = await server.auto_order()
        return baseline, proposal
    baseline, proposal = asyncio.run(scenario())
    assert proposal["data_quality"] == "DATI_AFFIDABILI"
    assert proposal["snapshot_key"] == baseline["snapshot_key"]
    assert proposal["righe"][0]["venduto_lungo"] == 20


def test_excel_import_freshness_setting_is_ignored_by_native_planning(isolated_database):
    db = isolated_database
    async def scenario():
        today = datetime.now(ZoneInfo("Europe/Rome")).date()
        await db.prodotti.insert_one({"id": "product", "codice": "P", "descrizione": "P", "categoria": "ACCESSORI", "giacenza_negozio": 0})
        await db.vendite.insert_one({"id": "sale", "codice": "P", "data": today.isoformat() + "T12:00:00", "quantita": 20, "canale": "NEGOZIO", "sorgente": "MANUALE"})
        baseline = await server.auto_order()
        await db.parametri.insert_one({"nome": "AUTO_ORDER_MAX_ETA_IMPORT_GG", "valore": 2})
        proposal = await server.auto_order()
        return baseline, proposal
    baseline, proposal = asyncio.run(scenario())
    assert proposal["data_quality"] == "DATI_AFFIDABILI"
    assert proposal["snapshot_key"] == baseline["snapshot_key"]


def test_two_distinct_confirmations_from_one_snapshot_conflict(isolated_database):
    db = isolated_database
    today = datetime.now(ZoneInfo("Europe/Rome")).date()
    async def scenario():
        await db.ordini_fornitore.create_index("batch_key", unique=True)
        await db.prodotti.insert_one({"id": "product", "codice": "P", "descrizione": "P", "categoria": "ACCESSORI",
                                      "giacenza_negozio": 0, "prezzo": 5})
        await db.vendite.insert_one({"codice": "P", "data": today.isoformat() + "T12:00:00", "quantita": 30,
                                     "canale": "NEGOZIO", "sorgente": "MANUALE"})
        proposal = await server.auto_order()
        assert proposal["righe"]
        body_one = server.AutoOrderConfermaIn(snapshot_key=proposal["snapshot_key"], idempotency_key="one",
                                             righe=[server.AutoOrderRigaIn(codice="P", quantita=proposal["righe"][0]["qta_da_ordinare"])])
        body_two = body_one.model_copy(update={"idempotency_key": "two"})
        results = await asyncio.gather(server.conferma_auto_order(body_one), server.conferma_auto_order(body_two), return_exceptions=True)
        return results, await db.ordini_fornitore.count_documents({}), await db.prodotti.find_one({"codice": "P"})
    results, batch_count, product = asyncio.run(scenario())
    assert batch_count == 1
    assert sum(isinstance(result, HTTPException) and result.status_code == 409 for result in results) == 1
    assert product["giacenza_negozio"] == 0 and product["auto_order_version"] == 1


def test_overreceived_supplier_line_blocks_auto_order(isolated_database):
    db = isolated_database
    async def scenario():
        await db.prodotti.insert_one({"id": "product", "codice": "P", "descrizione": "P", "categoria": "ACCESSORI", "giacenza_negozio": 2})
        await db.ordini_fornitore.insert_one({"id": "supplier", "batch_key": "bad", "stato": "CONFERMATO", "status": "COMPLETED"})
        await db.ordini_fornitore_righe.insert_one({"id": "line", "batch_key": "bad", "codice": "P", "quantita": 10,
                                                    "quantita_ricevuta": 11, "stato": "CONFERMATO"})
        return await server.auto_order()
    proposal = asyncio.run(scenario())
    assert proposal["data_quality"] == "DATI_INCOERENTI"
    assert proposal["esclusi"][0]["stato"] == "ANOMALIA"
    assert proposal["esclusi"][0]["anomalie_forniture"] == ["QUANTITA_ORDINE_INCOERENTE"]

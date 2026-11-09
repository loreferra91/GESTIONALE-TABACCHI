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
from backend.auto_order_planning import commercial_day, pending_by_code, projected_shortage, reconcile_sales, rounded_order_quantity
from backend.auto_order_migrations import AUTO_ORDER_2_SCHEMA_ID, migrate_auto_order_2_schema
from backend.orders_router import parse_auto_order_excel


def daily(code, day, quantity, channel=None, source=None):
    return {"_id": {"codice": code, "day": day, "canale": channel, "sorgente": source}, "tot": quantity}


def test_sales_overlap_is_not_doubled_and_later_app_days_are_kept():
    result = reconcile_sales(
        [daily("P", "2026-11-10", 30, "NEGOZIO"), daily("P", "2026-11-12", 5, "NEGOZIO")],
        [daily("P", "2026-11-10", 30)],
        date(2026, 11, 12), 10, 30,
    )["P"]
    assert result["venduto_lungo"] == 35
    assert result["venduto_breve"] == 35
    assert result["qualita_dati"] == "DATI_PARZIALI"
    assert result["anomalie_riconciliazione"] == ["SOVRAPPOSIZIONE_FONTI_SENZA_ID_EVENTO"]


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
    result = reconcile_sales(rows, [], date(2026, 11, 9), 10, 30)
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


def test_interrupted_import_blocks_confirmation(isolated_database):
    db = isolated_database
    async def scenario():
        await db.auto_order_import_state.insert_one({"_id": "excel-full", "status": "PROCESSING"})
        proposal = await server.auto_order()
        with pytest.raises(HTTPException) as blocked:
            await server.conferma_auto_order(server.AutoOrderConfermaIn(snapshot_key=proposal["snapshot_key"]))
        return proposal["data_quality"], blocked.value.status_code
    assert asyncio.run(scenario()) == ("DATI_INCOERENTI", 409)


def test_configured_import_freshness_blocks_missing_source(isolated_database):
    db = isolated_database
    async def scenario():
        await db.parametri.insert_one({"nome": "AUTO_ORDER_MAX_ETA_IMPORT_GG", "valore": 2})
        proposal = await server.auto_order()
        with pytest.raises(HTTPException) as blocked:
            await server.conferma_auto_order(server.AutoOrderConfermaIn(snapshot_key=proposal["snapshot_key"]))
        return proposal["data_quality"], blocked.value.status_code
    assert asyncio.run(scenario()) == ("DATI_OBSOLETI", 409)


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

import asyncio
import os
import re
from types import SimpleNamespace

import pytest
import openpyxl
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from mongomock_motor import AsyncMongoMockClient

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

import backend.server as server


def test_manual_order_cancellation_compensates_stock_purchase_and_smart_venue(monkeypatch):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.prodotti.insert_one({
            "id": "p1", "codice": "P1", "descrizione": "Prodotto",
            "giacenza_negozio": 10, "acquistati": 30,
        })
        await database.smart_venue.insert_one({"id": "sv1", "codice": "P1", "smart_venue": 4})
        created = await server.add_ordine(server.OrdineIn(
            data="2026-10-19", codice="P1", descrizione="Prodotto",
                quantita=3, prezzo=5, idempotency_key="manual-order-cancel-001",
        ))
        cancelled = await server.del_ordine(created["id"])
        repeated = await server.del_ordine(created["id"])
        return (
            cancelled,
            repeated,
            await database.prodotti.find_one({"codice": "P1"}, {"_id": 0}),
            await database.smart_venue.find_one({"codice": "P1"}, {"_id": 0}),
            await database.storico_ordini.find_one({"id": created["id"]}, {"_id": 0}),
            await database.storico_ordini.count_documents({"annullato": {"$ne": True}}),
        )

    cancelled, repeated, product, smart, order, active_orders = asyncio.run(scenario())

    assert cancelled == {"ok": True, "gia_annullato": False, "id": order["id"]}
    assert repeated == {"ok": True, "gia_annullato": True, "id": order["id"]}
    assert product["giacenza_negozio"] == 10
    assert product["acquistati"] == 30
    assert smart["smart_venue"] == 4
    assert order["annullato"] is True
    assert active_orders == 0


def test_bulk_text_parser_groups_multiple_barcodes_by_adm_code():
    text = (
        "4033100045844 - BENSON BLUE - 02982 - Sigarette\n"
        "5.30\n2\n10.60\n"
        "80919599 - BENSON BLUE AST20 - 02982 - Sigarette\n"
        "5.30\n3\n15.90\n"
    )

    parsed = server._parse_smart_venue_bulk_text(text)

    assert parsed["errori"] == []
    assert parsed["record"] == 2
    assert parsed["righe"] == [{
        "codice": "AMMS2982",
        "descrizione": "BENSON BLUE",
        "categoria": "Sigarette",
        "smart_venue": 5,
        "barcodes": ["4033100045844", "80919599"],
        "righe_sorgente": [1, 2],
        "avvisi": [],
    }]


def test_smart_venue_resolves_old_codes_and_collapses_duplicate_snapshots(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_compare():
        await database.prodotti.insert_many([
            {
                "id": "sunset", "codice": "AMMS20933",
                "descrizione": "ISENZIA SUNSET CORAL CRUSH", "giacenza_negozio": 8,
            },
            {
                "id": "evo", "codice": "AMMS21601",
                "descrizione": "EVO AMBER STICKS", "giacenza_negozio": 22,
            },
            {
                "id": "diana", "codice": "AMMS233",
                "descrizione": "DIANA ROSSA KS*AST20", "giacenza_negozio": 77,
            },
        ])
        await database.vending.insert_one({
            "id": "H2", "codice": "AMMS233", "giacenza": 4,
        })
        return await server._smart_venue_bulk_comparison([
            {"codice": "AMMS20933", "descrizione": "ISENZIA SUNSET CORAL CRUSH", "smart_venue": 8},
            {"codice": "AMMS21000", "descrizione": "ISENZIA SUNSET CORAL CRUSH (Conf. astuccio da 20 pezzi)", "smart_venue": 8},
            {"codice": "AMMS20697", "descrizione": "EVO AMBER STICKS*20PZ (Conf. astuccio da 20 pezzi)", "smart_venue": 22},
            {"codice": "AMMS21601", "descrizione": "EVO AMBER STICKS", "smart_venue": 22},
            {"codice": "AMMS223", "descrizione": "DIANA ROSSA KS*AST20 (nuove) (Conf. astuccio da 20 pezzi)", "smart_venue": 81},
            {"codice": "AMMS233", "descrizione": "DIANA ROSSA KS (Conf. astuccio da 20 pezzi)", "smart_venue": 0},
        ])

    rows = asyncio.run(seed_and_compare())

    assert [(row["codice"], row["smart_venue"], row["stato"]) for row in rows] == [
        ("AMMS20933", 8, "ALLINEATO"),
        ("AMMS21601", 22, "ALLINEATO"),
        ("AMMS233", 81, "ALLINEATO"),
    ]
    assert rows[0]["codici_sorgente"] == ["AMMS20933", "AMMS21000"]
    assert rows[1]["codici_sorgente"] == ["AMMS20697", "AMMS21601"]
    assert rows[2]["codici_sorgente"] == ["AMMS223", "AMMS233"]


def test_smart_venue_save_uses_one_bulk_write(monkeypatch):
    database = isolated_db(monkeypatch)
    bulk_sizes = []

    async def fake_backup(*_args, **_kwargs):
        return {"id": "backup-1"}

    async def fake_bulk(_collection, operations, batch_size=1000):
        bulk_sizes.append((len(operations), batch_size))
        return {"inseriti": len(operations), "aggiornati": 0, "errori": 0}

    async def fake_history(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(server, "create_backup_snapshot", fake_backup)
    monkeypatch.setattr(server, "_bulk_upsert", fake_bulk)
    monkeypatch.setattr(server, "record_import_history", fake_history)

    async def seed_and_save():
        await database.prodotti.insert_one({
            "id": "evo", "codice": "AMMS21601",
            "descrizione": "EVO AMBER STICKS", "giacenza_negozio": 22,
        })
        return await server.import_smart_venue_bulk(server.SmartVenueBulkIn(testo=(
            "80824947 - EVO AMBER STICKS*20PZ (Conf. astuccio da 20 pezzi) - 20697 - Sigarette\n"
            "4.50\n22\n99.00\n"
            "99999999 - EVO AMBER STICKS - 21601 - Sigarette\n"
            "4.50\n22\n99.00\n"
        )))

    response = asyncio.run(seed_and_save())

    assert response["ok"] is True
    assert bulk_sizes == [(1, 1000)]


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, key, direction):
        self.documents.sort(key=lambda row: row.get(key), reverse=direction < 0)
        return self

    async def to_list(self, length):
        return self.documents[:length] if length is not None else self.documents


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = [dict(document) for document in (documents or [])]

    @staticmethod
    def _matches(document, query):
        for key, value in query.items():
            current = document.get(key)
            if isinstance(value, dict):
                if "$in" in value and current not in value["$in"]:
                    return False
                if "$ne" in value:
                    if isinstance(current, list) and value["$ne"] in current:
                        return False
                    if not isinstance(current, list) and current == value["$ne"]:
                        return False
                if "$regex" in value and not re.search(value["$regex"], str(current or "")):
                    return False
            elif isinstance(current, list):
                if value not in current:
                    return False
            elif current != value:
                return False
        return True

    async def insert_one(self, document):
        self.documents.append(dict(document))

    async def insert_many(self, documents):
        self.documents.extend(dict(document) for document in documents)

    async def delete_many(self, query):
        self.documents = [row for row in self.documents if not self._matches(row, query)] if query else []

    async def delete_one(self, query):
        for index, row in enumerate(self.documents):
            if self._matches(row, query):
                self.documents.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def find(self, query=None, _projection=None):
        query = query or {}
        return FakeCursor([dict(row) for row in self.documents if self._matches(row, query)])

    async def find_one(self, query, _projection=None):
        return next((dict(row) for row in self.documents if self._matches(row, query)), None)

    async def update_one(self, query, update, upsert=False):
        for row in self.documents:
            if self._matches(row, query):
                row.update(update.get("$set", {}))
                for key, value in update.get("$inc", {}).items():
                    row[key] = row.get(key, 0) + value
                for key, value in update.get("$addToSet", {}).items():
                    if value not in row.setdefault(key, []):
                        row[key].append(value)
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            self.documents.append({**query, **update.get("$set", {})})
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id="fake-id")
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, query, update, upsert=False):
        matched = modified = 0
        for row in self.documents:
            if self._matches(row, query):
                matched += 1
                row.update(update.get("$set", {}))
                modified += 1
        return SimpleNamespace(matched_count=matched, modified_count=modified)

    async def count_documents(self, query):
        return sum(1 for row in self.documents if self._matches(row, query))

    async def bulk_write(self, operations, ordered=True):
        matched = 0
        for operation in operations:
            result = await self.update_one(operation._filter, operation._doc)
            matched += result.matched_count
        return SimpleNamespace(matched_count=matched)


def isolated_db(monkeypatch):
    database = SimpleNamespace(
        smart_venue=FakeCollection(),
        smart_venue_hidden=FakeCollection(),
        smart_venue_values=FakeCollection(),
        smart_venue_movimenti=FakeCollection(),
        vending=FakeCollection(),
        prodotti=FakeCollection(),
        storico_ordini=FakeCollection(),
        app_migrations=FakeCollection(),
        ricezioni_merce=FakeCollection(),
        rettifiche_inventario=FakeCollection(),
        audit_events=FakeCollection(),
    )
    monkeypatch.setattr(server, "db", database)
    async def fake_backup(*_args, **_kwargs):
        return {"id": "backup-test"}
    monkeypatch.setattr(server, "create_backup_snapshot", fake_backup)
    return database


def test_bulk_receipt_creates_pending_smart_venue_movement_without_changing_balance(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_receive():
        await database.prodotti.insert_many([
            {"codice": "P1", "descrizione": "Prodotto Smart", "acquistati": 10, "giacenza_negozio": 5},
            {"codice": "P2", "descrizione": "Prodotto normale", "acquistati": 4, "giacenza_negozio": 2},
        ])
        await database.smart_venue.insert_one({
            "id": "smart-venue:P1", "codice": "P1", "smart_venue": 8,
        })
        response = await server.bulk_carico(server.BulkOrdineIn(
            file_sorgente="TEST",
            data="2026-10-14T00:00:00+00:00",
            righe=[
                {"codice": "P1", "descrizione": "Prodotto Smart", "quantita": 3, "prezzo": 5},
                {"codice": "P2", "descrizione": "Prodotto normale", "quantita": 2, "prezzo": 4},
            ],
        ))
        return (
            response,
            await database.prodotti.find_one({"codice": "P1"}),
            await database.prodotti.find_one({"codice": "P2"}),
            await database.smart_venue.find_one({"codice": "P1"}),
        )

    response, smart_product, normal_product, smart_row = asyncio.run(seed_and_receive())

    assert response["caricate"] == 2
    assert smart_product["giacenza_negozio"] == 8
    assert smart_product["acquistati"] == 13
    assert smart_row["smart_venue"] == 8
    assert len(database.smart_venue_movimenti.documents) == 1
    assert database.smart_venue_movimenti.documents[0]["stato"] == "PENDENTE"
    assert database.smart_venue_movimenti.documents[0]["quantita"] == 3
    assert normal_product["giacenza_negozio"] == 4
    assert count_rows_for_code(database.smart_venue.documents, "P2") == 0


def count_rows_for_code(rows, code):
    return sum(1 for row in rows if row.get("codice") == code)


def test_single_receipt_creates_pending_smart_venue_movement(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_receive():
        await database.prodotti.insert_one({
            "codice": "P1", "descrizione": "Prodotto Smart", "acquistati": 10, "giacenza_negozio": 5,
        })
        await database.smart_venue.insert_one({
            "id": "smart-venue:P1", "codice": "P1", "smart_venue": 8,
        })
        await server.add_ordine(server.OrdineIn(
            data="2026-10-14T00:00:00+00:00",
            file_sorgente="TEST",
            codice="P1",
            descrizione="Prodotto Smart",
            quantita=2,
            prezzo=5,
                idempotency_key="manual-order-smart-001",
        ))
        return await database.smart_venue.find_one({"codice": "P1"})

    smart_row = asyncio.run(seed_and_receive())

    assert smart_row["smart_venue"] == 8
    assert database.smart_venue_movimenti.documents[0]["stato"] == "PENDENTE"


def test_confirming_pending_load_increments_smart_venue_once(monkeypatch):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.prodotti.insert_one({"codice": "P1", "descrizione": "Prodotto", "acquistati": 1, "giacenza_negozio": 1})
        await database.smart_venue.insert_one({"id": "smart-venue:P1", "codice": "P1", "smart_venue": 8})
        receipt = await server.bulk_carico(server.BulkOrdineIn(file_sorgente="TEST", righe=[{"codice": "P1", "quantita": 3, "prezzo": 5}]))
        order_id = receipt["movimenti"][0]["id"]
        first = await server.conferma_carico_smart_venue(order_id)
        second = await server.conferma_carico_smart_venue(order_id)
        return first, second, await database.smart_venue.find_one({"codice": "P1"}), database.smart_venue_movimenti.documents[0]

    first, second, smart, movement = asyncio.run(scenario())
    assert first["gia_confermato"] is False
    assert second["gia_confermato"] is True
    assert smart["smart_venue"] == 11
    assert movement["stato"] == "CONFERMATO"


def test_historical_metadata_migration_is_dry_run_then_idempotent(monkeypatch):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.app_migrations.insert_one({"id": server.SMART_VENUE_AUTO_ORDER_REPAIR_ID, "pezzi_ripristinati": 810})
        await database.storico_ordini.insert_many([{"id": f"o-{code}", "data": "2026-10-12T00:00:00+00:00", "file_sorgente": "da auto-order (modificato)", "codice": code, "quantita": quantity} for code, quantity in server.SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED.items()])
        await database.smart_venue.insert_many([{"codice": code, "riparazioni_applicate": [server.SMART_VENUE_AUTO_ORDER_REPAIR_ID]} for code in server.SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED])
        preview = await server.historical_smart_venue_metadata_migration()
        applied = await server.historical_smart_venue_metadata_migration(dry_run=False)
        repeated = await server.historical_smart_venue_metadata_migration(dry_run=False)
        return preview, applied, repeated

    preview, applied, repeated = asyncio.run(scenario())
    assert preview["ok"] is True and preview["dry_run"] is True and preview["modifiche_previste"] == 27
    assert applied["righe_aggiornate"] == 27 and applied["pezzi_verificati"] == 810
    assert repeated["gia_applicata"] is True


def test_verified_auto_order_repair_totals_27_rows_and_810_pieces():
    rows = [
        {"codice": code, "quantita": quantity}
        for code, quantity in server.SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED.items()
    ]

    assert server._receipt_quantities_by_code(rows) == server.SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED
    assert len(rows) == 27
    assert sum(row["quantita"] for row in rows) == 810


def test_verified_auto_order_repair_is_backed_up_and_idempotent(monkeypatch):
    database = isolated_db(monkeypatch)
    backup_calls = []

    async def fake_backup(*args):
        backup_calls.append(args)
        return {"id": "backup-before-repair"}

    monkeypatch.setattr(server, "create_backup_snapshot", fake_backup)

    async def seed_and_repair_twice():
        await database.storico_ordini.insert_many([
            {
                "data": "2026-10-12T00:00:00+00:00",
                "file_sorgente": "da auto-order (modificato)",
                "codice": code,
                "quantita": quantity,
            }
            for code, quantity in server.SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED.items()
        ])
        await database.smart_venue.insert_many([
            {"codice": code, "smart_venue": 5}
            for code in server.SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED
        ])
        first = await server.repair_smart_venue_auto_order_2026_10_12()
        after_first = {
            row["codice"]: row["smart_venue"] for row in database.smart_venue.documents
        }
        second = await server.repair_smart_venue_auto_order_2026_10_12()
        after_second = {
            row["codice"]: row["smart_venue"] for row in database.smart_venue.documents
        }
        return first, second, after_first, after_second

    first, second, after_first, after_second = asyncio.run(seed_and_repair_twice())

    assert first["gia_applicata"] is False
    assert first["pezzi_ripristinati"] == 810
    assert second["gia_applicata"] is True
    assert backup_calls == [(
        "Prima riparazione Smart Venue carico Auto-Order 12/10/2026",
        "riparazione-smart-venue-carico",
        ["smart_venue", "storico_ordini"],
    )]
    assert after_second == after_first
    assert after_first == {
        code: 5 + quantity
        for code, quantity in server.SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED.items()
    }


def test_smart_venue_uses_product_acquired_and_stock_with_excel_smart_value(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_list():
        await database.prodotti.insert_one({
            "id": "prod-1", "codice": "P1", "descrizione": "Descrizione prodotto",
            "acquistati": 10, "giacenza_negozio": 25, "giacenza_vending": 4,
        })
        await database.smart_venue.insert_many([
            {
                "id": "smart-venue:P1", "codice": "P1", "descrizione": "Descrizione Excel",
                "acquistati": 1080, "smart_venue": 43, "codice_smart": "0012345",
            },
            {"id": "smart-venue:P2", "codice": "P2", "descrizione": "Prodotto nascosto"},
        ])
        await database.smart_venue_hidden.insert_one({"product_id": "smart-venue:P2"})
        return await server.list_smart_venue()

    rows = asyncio.run(seed_and_list())

    assert len(rows) == 1
    assert rows[0]["codice"] == "P1"
    assert rows[0]["descrizione"] == "Descrizione Excel"
    assert rows[0]["acquistati"] == 10
    assert rows[0]["rimanenze"] == 25
    assert rows[0]["smart_venue"] == 43
    assert rows[0]["barcode"] == "0012345"
    assert rows[0]["differenza"] == 18


def test_smart_venue_import_uses_column_f_and_preserves_manual_rows(monkeypatch):
    database = isolated_db(monkeypatch)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "(SMART VENUE)"
    sheet.append([])
    sheet.append(["Codice", "Descrizione", "ACQ.", "RIMANENZE", "codice2", "RIMANENZE4"])
    sheet.append(["AMMS9", "MARLBORO GOLD", 1080, 29, "87248265", 43])

    async def seed_and_import():
        await database.smart_venue.insert_one({
            "id": "smart-venue:MAN1", "codice": "MAN1", "descrizione": "Manuale",
            "smart_venue": 7, "origine": "MANUALE",
        })
        result = await server._import_smart_venue(sheet)
        rows = await database.smart_venue.find({}).to_list(5000)
        return result, {row["codice"]: row for row in rows}

    result, rows = asyncio.run(seed_and_import())

    assert result["inseriti"] == 2
    assert rows["AMMS9"]["smart_venue"] == 43
    assert rows["AMMS9"]["barcode"] == "87248265"
    assert rows["AMMS9"]["origine"] == "EXCEL"
    assert rows["MAN1"]["smart_venue"] == 7


def test_can_add_new_smart_venue_product_with_preset_category(monkeypatch):
    database = isolated_db(monkeypatch)

    async def create_and_read():
        response = await server.create_smart_venue_product(server.SmartVenueProductIn(
            codice="new1",
            descrizione="Nuovo prodotto",
            barcode="00998877",
            categoria="SIGARI",
            prezzo=5.5,
            acquistati=10,
            giacenza_negozio=3,
            giacenza_vending=2,
            smart_venue=8,
        ))
        product = await database.prodotti.find_one({"codice": "NEW1"})
        smart_row = await database.smart_venue.find_one({"codice": "NEW1"})
        return response, product, smart_row

    response, product, smart_row = asyncio.run(create_and_read())

    assert product["categoria"] == "SIGARI"
    assert product["acquistati"] == 10
    assert smart_row["origine"] == "MANUALE"
    assert smart_row["barcode"] == "00998877"
    assert response["barcode"] == "00998877"
    assert response["rimanenze"] == 3
    assert response["smart_venue"] == 8
    assert response["differenza"] == 5


def test_new_smart_venue_product_response_is_json_serializable_with_mongo_ids(monkeypatch):
    database = AsyncMongoMockClient()["smart_venue_json_test"]
    monkeypatch.setattr(server, "db", database)

    response = asyncio.run(server.create_smart_venue_product(server.SmartVenueProductIn(
        codice="NEW2",
        descrizione="Nuovo prodotto serializzabile",
        smart_venue=4,
    )))

    assert jsonable_encoder(response)["smart_venue"] == 4
    assert "_id" not in response


def test_legacy_smart_venue_confirmation_is_retired(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_confirm():
        await database.prodotti.insert_one({
            "id": "prod-1", "codice": "P1", "giacenza_negozio": 7, "giacenza_vending": 3,
        })
        await database.vending.insert_one({
            "id": "v1", "codice": "P1", "giacenza": 3,
        })
        await database.smart_venue.insert_one({
            "id": "smart-venue:P1", "codice": "P1", "smart_venue": 14,
        })
        with pytest.raises(HTTPException) as error:
            await server.confirm_smart_venue_difference("smart-venue:P1", {})
        return error.value, await database.smart_venue.find_one({"id": "smart-venue:P1"})

    error, stored = asyncio.run(seed_and_confirm())

    assert error.status_code == 410
    assert "conferma-uscita" in error.detail
    assert stored["smart_venue"] == 14


def test_smart_venue_barcode_can_be_edited_and_cleared(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_edit_and_clear():
        await database.smart_venue.insert_one({
            "id": "smart-venue:P1", "codice": "P1", "codice_smart": "OLD-BARCODE",
        })
        edited = await server.update_smart_venue_barcode(
            "smart-venue:P1", server.SmartVenueBarcodeIn(barcode="  NEW-BARCODE  ")
        )
        cleared = await server.update_smart_venue_barcode(
            "smart-venue:P1", server.SmartVenueBarcodeIn(barcode="")
        )
        rows = await server.list_smart_venue()
        return edited, cleared, rows

    edited, cleared, rows = asyncio.run(seed_edit_and_clear())

    assert edited["barcode"] == "NEW-BARCODE"
    assert cleared["barcode"] == ""
    assert rows[0]["barcode"] == ""


def test_smart_venue_exit_decreases_smart_value(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_confirm():
        await database.prodotti.insert_one({
            "id": "prod-1", "codice": "P1", "giacenza_negozio": 7, "giacenza_vending": 3,
        })
        await database.vending.insert_one({
            "id": "v-exit", "codice": "P1", "giacenza": 3,
        })
        await database.smart_venue.insert_one({
            "id": "smart-venue:P1", "codice": "P1", "smart_venue": 20,
        })
        return await server.confirm_smart_venue_exit(
            "smart-venue:P1",
            server.SmartVenueExitIn(
                codice="P1", quantita=3, motivazione="uscita test",
                idempotency_key="exit-test", saldo_atteso=20,
                differenza_attesa=10,
            ),
        )

    confirmed = asyncio.run(seed_and_confirm())

    assert confirmed["quantita"] == 3
    assert confirmed["smart_venue"] == 17
    assert confirmed["differenza"] == 7


def test_manual_vending_stock_correction_updates_product_total_without_shop_movement(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_update():
        await database.vending.insert_many([
            {"id": "v1", "colonna": "A01", "codice": "P1", "giacenza": 2, "capacita_max": 5},
            {"id": "v2", "colonna": "A02", "codice": "P1", "giacenza": 1, "capacita_max": 5},
        ])
        await database.prodotti.insert_one({"codice": "P1", "giacenza_negozio": 9, "giacenza_vending": 3})
        response = await server.update_vending_giacenza("v1", {"giacenza": 4})
        product = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        vending = await database.vending.find_one({"id": "v1"}, {"_id": 0})
        return response, product, vending

    response, product, vending = asyncio.run(seed_and_update())

    assert response["giacenza_precedente"] == 2
    assert response["giacenza"] == 4
    assert vending["giacenza_sorgente"] == "CORREZIONE_MANUALE"
    assert product["giacenza_vending"] == 5
    assert product["giacenza_negozio"] == 9


def test_manual_vending_stock_correction_rejects_value_above_capacity(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_update():
        await database.vending.insert_one(
            {"id": "v1", "colonna": "A01", "codice": "P1", "giacenza": 2, "capacita_max": 5}
        )
        await server.update_vending_giacenza("v1", {"giacenza": 6})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(seed_and_update())

    assert exc.value.status_code == 422
    assert "capacità" in exc.value.detail


def test_delete_smart_venue_row(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_delete():
        await database.smart_venue.insert_one({"id": "smart-venue:P1", "codice": "P1"})
        response = await server.delete_smart_venue_row("smart-venue:P1")
        smart_row = await database.smart_venue.find_one({"id": "smart-venue:P1"})
        hidden = await database.smart_venue_hidden.find_one({"product_id": "smart-venue:P1"})
        return response, smart_row, hidden

    response, smart_row, hidden = asyncio.run(seed_and_delete())

    assert response == {"ok": True, "id": "smart-venue:P1"}
    assert smart_row["codice"] == "P1"
    assert hidden["product_id"] == "smart-venue:P1"


def test_receipt_is_idempotent_and_external_confirmation_does_not_increment_stock(monkeypatch):
    database = isolated_db(monkeypatch)

    async def receive_twice_and_confirm_twice():
        await database.prodotti.insert_one({
            "codice": "P1", "descrizione": "WINSTON BLUE",
            "acquistati": 150, "giacenza_negozio": 150,
        })
        await database.smart_venue.insert_one({
            "id": "smart-venue:P1", "codice": "P1", "smart_venue": 150,
        })
        payload = server.BulkOrdineIn(
            file_sorgente="BOLLA-1",
            data="2026-10-15T08:00:00+00:00",
            idempotency_key="receipt-once",
            righe=[{"codice": "P1", "descrizione": "WINSTON BLUE", "quantita": 150, "prezzo": 5}],
        )
        first = await server.bulk_carico(payload)
        second = await server.bulk_carico(payload)
        movement_id = first["movimenti"][0]["id"]
        confirmed = await server.conferma_carico_smart_venue(movement_id)
        confirmed_again = await server.conferma_carico_smart_venue(movement_id)
        return (
            first,
            second,
            confirmed,
            confirmed_again,
            await database.prodotti.find_one({"codice": "P1"}),
            await database.smart_venue.find_one({"codice": "P1"}),
        )

    first, second, confirmed, confirmed_again, product, smart_row = asyncio.run(receive_twice_and_confirm_twice())

    assert first["gia_registrato"] is False
    assert second["gia_registrato"] is True
    assert product["giacenza_negozio"] == 300
    assert smart_row["smart_venue"] == 300
    assert confirmed["caricato_smart_venue"] is True
    assert confirmed_again["gia_confermato"] is True
    assert count_rows_for_code(database.storico_ordini.documents, "P1") == 1


def test_real_stock_adjustment_records_history_without_creating_sales(monkeypatch):
    database = isolated_db(monkeypatch)
    database.vendite = FakeCollection()

    async def seed_adjust_and_read():
        await database.prodotti.insert_one({
            "id": "prod-1", "codice": "P1", "descrizione": "WINSTON BLUE",
            "giacenza_negozio": 150, "giacenza_vending": 3,
        })
        await database.vending.insert_many([
            {"id": "v1", "colonna": "A01", "codice": "P1", "giacenza": 2, "capacita_max": 5},
            {"id": "v2", "colonna": "A02", "codice": "P1", "giacenza": 1, "capacita_max": 5},
        ])
        response = await server.update_giacenza_reale(
            "P1",
            server.GiacenzaRealeIn(
                giacenza_negozio=148,
                giacenza_vending=5,
                attesa_giacenza_negozio=150,
                attesa_giacenza_vending=3,
                motivazione="Conteggio fisico",
            ),
        )
        rows = await server.list_giacenze_reali()
        return response, rows

    response, rows = asyncio.run(seed_adjust_and_read())

    assert response["giacenza_totale"] == 153
    assert rows[0]["giacenza_negozio"] == 148
    assert rows[0]["giacenza_vending"] == 5
    assert rows[0]["giacenza_totale"] == 153
    assert [(row["ubicazione"], row["differenza"]) for row in database.rettifiche_inventario.documents] == [
        ("NEGOZIO", -2),
        ("VENDING", 2),
    ]
    assert database.vendite.documents == []


def test_real_stock_adjustment_rejects_stale_concurrent_values(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_update():
        await database.prodotti.insert_one({
            "id": "prod-1", "codice": "P1", "descrizione": "WINSTON BLUE",
            "giacenza_negozio": 149, "giacenza_vending": 0,
        })
        await server.update_giacenza_reale(
            "P1",
            server.GiacenzaRealeIn(
                giacenza_negozio=148,
                giacenza_vending=0,
                attesa_giacenza_negozio=150,
                attesa_giacenza_vending=0,
            ),
        )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(seed_and_update())

    assert exc.value.status_code == 409


def test_real_stock_adjustment_response_remains_json_serializable_with_mongo_ids(monkeypatch):
    database = AsyncMongoMockClient()["real_stock_json_test"]
    monkeypatch.setattr(server, "db", database)

    async def seed_and_update():
        await database.prodotti.insert_one({
            "id": "prod-1", "codice": "P1", "descrizione": "WINSTON BLUE",
            "giacenza_negozio": 150, "giacenza_vending": 0,
        })
        return await server.update_giacenza_reale(
            "P1",
            server.GiacenzaRealeIn(
                giacenza_negozio=148,
                giacenza_vending=0,
                attesa_giacenza_negozio=150,
                attesa_giacenza_vending=0,
                motivazione="Conteggio fisico",
            ),
        )

    response = asyncio.run(seed_and_update())

    assert jsonable_encoder(response)["rettifiche"][0]["differenza"] == -2
    assert "_id" not in response["rettifiche"][0]


def test_existing_over_capacity_vending_stock_does_not_block_shop_only_correction():
    columns = [{"id": "v1", "colonna": "A01", "giacenza": 6, "capacita_max": 5}]

    assert server._plan_vending_inventory(columns, 6) == []
    with pytest.raises(HTTPException) as exc:
        server._plan_vending_inventory(columns, 7)

    assert exc.value.status_code == 422

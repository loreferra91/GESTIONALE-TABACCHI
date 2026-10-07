import asyncio
import os
from types import SimpleNamespace

import pytest
import openpyxl
from fastapi import HTTPException

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

import backend.server as server


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
        ("AMMS20933", 8, "OK"),
        ("AMMS21601", 22, "OK"),
        ("AMMS233", 81, "OK"),
    ]
    assert rows[0]["codici_sorgente"] == ["AMMS20933", "AMMS21000"]
    assert rows[1]["codici_sorgente"] == ["AMMS20697", "AMMS21601"]
    assert rows[2]["codici_sorgente"] == ["AMMS223", "AMMS233"]


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, key, direction):
        self.documents.sort(key=lambda row: row.get(key), reverse=direction < 0)
        return self

    async def to_list(self, length):
        return self.documents[:length] if length is not None else self.documents


class FakeCollection:
    def __init__(self):
        self.documents = []

    @staticmethod
    def _matches(document, query):
        return all(document.get(key) == value for key, value in query.items())

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
                return SimpleNamespace(matched_count=1)
        if upsert:
            self.documents.append({**query, **update.get("$set", {})})
            return SimpleNamespace(matched_count=0, upserted_id="fake-id")
        return SimpleNamespace(matched_count=0)


def isolated_db(monkeypatch):
    database = SimpleNamespace(
        smart_venue=FakeCollection(),
        smart_venue_hidden=FakeCollection(),
        smart_venue_values=FakeCollection(),
        vending=FakeCollection(),
        prodotti=FakeCollection(),
    )
    monkeypatch.setattr(server, "db", database)
    return database


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


def test_smart_venue_blank_insertion_uses_difference(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_confirm():
        await database.prodotti.insert_one({
            "id": "prod-1", "codice": "P1", "giacenza_negozio": 7, "giacenza_vending": 3,
        })
        await database.smart_venue.insert_one({
            "id": "smart-venue:P1", "codice": "P1", "smart_venue": 14,
        })
        confirmed = await server.confirm_smart_venue_difference("smart-venue:P1", {})
        stored = await database.smart_venue.find_one({"id": "smart-venue:P1"})
        return confirmed, stored

    confirmed, stored = asyncio.run(seed_and_confirm())

    assert confirmed["inserimento_smart_venue"] == 7
    assert confirmed["smart_venue"] == 7
    assert confirmed["differenza"] == 0
    assert stored["smart_venue"] == 7


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


def test_smart_venue_manual_insertion_decreases_smart_value(monkeypatch):
    database = isolated_db(monkeypatch)

    async def seed_and_confirm():
        await database.prodotti.insert_one({
            "id": "prod-1", "codice": "P1", "giacenza_negozio": 7, "giacenza_vending": 3,
        })
        await database.smart_venue.insert_one({
            "id": "smart-venue:P1", "codice": "P1", "smart_venue": 20,
        })
        return await server.confirm_smart_venue_difference(
            "smart-venue:P1", {"inserimento_smart_venue": 3}
        )

    confirmed = asyncio.run(seed_and_confirm())

    assert confirmed["inserimento_smart_venue"] == 3
    assert confirmed["smart_venue"] == 17
    assert confirmed["differenza"] == 10


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

import asyncio
import io
import os
from types import SimpleNamespace

import openpyxl
import pytest
from fastapi import HTTPException, UploadFile

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

import backend.server as server


class CapturingProducts:
    def __init__(self):
        self.operations = []

    async def bulk_write(self, operations, ordered):
        assert ordered is False
        self.operations.extend(operations)
        return SimpleNamespace(upserted_count=0, matched_count=len(operations))


class CapturingImportHistory:
    def __init__(self, result=None):
        self.saved = None
        self.query = None
        self.result = result

    async def insert_one(self, document):
        self.saved = document

    async def find_one(self, query, projection, sort):
        self.query = query
        assert projection == {"_id": 0}
        assert sort == [("created_at", -1)]
        return self.result


def inventory_workbook():
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Foglio1"
    sheet.append(["CODICE", "DESCRIZIONE", "ACQUISTATI", "GIACENZA NEGOZIO", "GIACENZA VENDING"])
    sheet.append(["AMMS9", "MARLBORO GOLD KS*AST20", 1080, 20, 4])
    sheet.append([10008, "OCB FILTRI", 178, 32, 0])
    return workbook


def test_finds_inventory_sheet_by_headers_not_sheet_name():
    found = server._find_product_stock_sheet(inventory_workbook())

    assert found is not None
    sheet, header_row, columns = found
    assert sheet.title == "Foglio1"
    assert header_row == 1
    assert columns == {
        "codice": 0,
        "descrizione": 1,
        "acquistati": 2,
        "giacenza_negozio": 3,
        "giacenza_vending": 4,
    }


def test_inventory_import_updates_only_both_stocks(monkeypatch):
    products = CapturingProducts()
    monkeypatch.setattr(server, "db", SimpleNamespace(prodotti=products))
    sheet, header_row, columns = server._find_product_stock_sheet(inventory_workbook())

    result = asyncio.run(server._import_product_stock_sheet(sheet, header_row, columns))

    assert result == {"inseriti": 0, "aggiornati": 2, "errori": 0, "righe_lette": 2}
    by_code = {operation._filter["codice"]: operation._doc["$set"] for operation in products.operations}
    assert by_code["AMMS9"] == {
        "codice": "AMMS9",
        "descrizione": "MARLBORO GOLD KS*AST20",
        "giacenza_negozio": 20,
        "giacenza_vending": 4,
    }
    assert "acquistati" not in by_code["AMMS9"]
    assert by_code["10008"]["giacenza_negozio"] == 32
    assert by_code["10008"]["giacenza_vending"] == 0


def test_full_import_rejects_workbook_without_supported_headers():
    workbook = openpyxl.Workbook()
    workbook.active.append(["COLONNA NON SUPPORTATA"])
    payload = io.BytesIO()
    workbook.save(payload)
    upload = UploadFile(filename="non-supportato.xlsx", file=io.BytesIO(payload.getvalue()))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.import_excel_full(upload, "test-invalid-format"))

    assert exc.value.status_code == 422
    assert "Nessun foglio importabile" in exc.value.detail
    assert server.IMPORT_PROGRESS["test-invalid-format"]["status"] == "failed"


def test_stock_import_history_is_explicitly_non_accounting(monkeypatch):
    history = CapturingImportHistory()
    monkeypatch.setattr(server, "db", SimpleNamespace(import_history=history))

    result = asyncio.run(server.record_import_history(
        "GIACENZA.xlsx",
        {"fogli_trovati": ["Foglio1"], "totali": {}},
    ))

    assert result["tipo_import"] == "giacenze"
    assert result["contabilita_inclusa"] is False
    assert history.saved["contabilita_inclusa"] is False


def test_latest_accounting_import_excludes_stock_only_history(monkeypatch):
    expected = {"file": "GODSERVICES38.xlsm"}
    history = CapturingImportHistory(expected)
    monkeypatch.setattr(server, "db", SimpleNamespace(import_history=history))

    result = asyncio.run(server._latest_accounting_import())

    assert result == expected
    assert history.query == {
        "$or": [
            {"contabilita_inclusa": True},
            {"fogli_trovati": "RIEP_VENDITA"},
        ]
    }

import asyncio
import os
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from openpyxl import Workbook

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

import backend.server as server


class FakeHistoryCollection:
    def __init__(self):
        self.delete_calls = 0
        self.inserted = []

    async def delete_many(self, _query):
        self.delete_calls += 1

    async def insert_many(self, documents):
        self.inserted.extend(documents)


def test_import_storico_vend_detects_shifted_excel_columns(monkeypatch):
    ws = Workbook().active
    ws.append([None, None, None, "DATABASE STORICO", None, None])
    ws.append([None, "data", "Codice", "Descrizione", "Qtà", "Importo"])
    ws.append([None, datetime(2026, 9, 19), "AMMS201", "Prodotto", 2, 11])
    # Riproduce un codice numerico a cui Excel ha applicato un formato data.
    ws.append([None, datetime(2026, 9, 19), datetime(1900, 1, 10), "Accendino", 3, 3])
    collection = FakeHistoryCollection()
    monkeypatch.setattr(server, "db", SimpleNamespace(db_storico_vend=collection))

    result = asyncio.run(server._import_db_storico_vend(ws))

    assert result == {"inseriti": 2, "aggiornati": 0, "errori": 0}
    assert collection.delete_calls == 1
    assert [row["codice"] for row in collection.inserted] == ["AMMS201", "10"]
    assert collection.inserted[0]["data"] == "2026-09-19T00:00:00"
    assert collection.inserted[0]["quantita"] == 2


def test_import_storico_vend_preserves_existing_data_for_unknown_layout(monkeypatch):
    ws = Workbook().active
    ws.append(["colonna sconosciuta", "valore"])
    collection = FakeHistoryCollection()
    monkeypatch.setattr(server, "db", SimpleNamespace(db_storico_vend=collection))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(server._import_db_storico_vend(ws))

    assert exc.value.status_code == 422
    assert collection.delete_calls == 0
    assert collection.inserted == []

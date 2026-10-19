import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

from backend import server


class _Cursor:
    async def to_list(self, _limit):
        return [{"id": "doc-1", "value": 1}]


class _SourceCollection:
    def find(self, *_args, **_kwargs):
        return _Cursor()


class _ItemsCollection:
    async def delete_many(self, _query):
        return None

    async def insert_many(self, _documents):
        return None


class _SnapshotsCollection:
    def __init__(self):
        self.saved = None

    async def insert_one(self, document):
        self.saved = document
        # PyMongo/Motor aggiunge ObjectId direttamente al dizionario ricevuto.
        document["_id"] = object()
        return SimpleNamespace(inserted_id=document["_id"])


class _BackupDb:
    def __init__(self):
        self.backup_snapshot_items = _ItemsCollection()
        self.backup_snapshots = _SnapshotsCollection()
        self.sources = {name: _SourceCollection() for name in server.BACKUP_COLLECTIONS}

    def __getitem__(self, name):
        return self.sources[name]


def test_backup_snapshot_response_does_not_leak_mongo_object_id(monkeypatch):
    fake_db = _BackupDb()
    monkeypatch.setattr(server, "db", fake_db)

    result = asyncio.run(server.create_backup_snapshot("Backup test"))

    assert "_id" not in result
    assert "_id" in fake_db.backup_snapshots.saved
    assert result["total_docs"] == len(server.BACKUP_COLLECTIONS)
    assert result["partial"] is False
    assert "vendite" in server.BACKUP_COLLECTIONS
    assert "cassa" in server.BACKUP_COLLECTIONS


def test_backup_snapshot_can_be_limited_to_changed_collections(monkeypatch):
    fake_db = _BackupDb()
    monkeypatch.setattr(server, "db", fake_db)

    result = asyncio.run(server.create_backup_snapshot(
        "Backup SmartVenue",
        "pre-import-smart-venue-bulk",
        ["smart_venue", "smart_venue_hidden", "smart_venue_values"],
    ))

    assert result["counts"] == {
        "smart_venue": 1,
        "smart_venue_hidden": 1,
        "smart_venue_values": 1,
    }
    assert result["total_docs"] == 3
    assert result["partial"] is True


def test_backup_retention_keeps_three_recent_snapshots_and_frees_old_items(monkeypatch):
    database = AsyncMongoMockClient()["backup_retention_test"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        for index in range(5):
            snapshot_id = f"backup-{index}"
            await database.backup_snapshots.insert_one({
                "id": snapshot_id,
                "status": "READY",
                "created_at": f"2026-10-{index + 1:02d}T10:00:00+00:00",
                "label": snapshot_id,
                "reason": "test",
                "total_docs": 2,
            })
            await database.backup_snapshot_items.insert_many([
                {"snapshot_id": snapshot_id, "collection": "prodotti", "doc": {"id": 1}},
                {"snapshot_id": snapshot_id, "collection": "prodotti", "doc": {"id": 2}},
            ])
        preview = await server.backup_retention_preview(3)
        result = await server.prune_backup_snapshots(3)
        remaining = await database.backup_snapshot_items.count_documents({})
        pruned = await database.backup_snapshots.count_documents({"status": "PRUNED"})
        return preview, result, remaining, pruned

    preview, result, remaining, pruned = asyncio.run(scenario())

    assert preview["snapshot_da_svuotare"] == 2
    assert preview["documenti_da_eliminare_stimati"] == 4
    assert result["documenti_eliminati"] == 4
    assert remaining == 6
    assert pruned == 2


def test_backup_file_validation_requires_every_operational_collection():
    collections = {name: [] for name in server.BACKUP_COLLECTIONS}
    collections["vendite"] = [{"id": "sale-1", "importo": 5.8}]
    payload = {
        "format": server.BACKUP_FILE_FORMAT,
        "version": server.BACKUP_FILE_VERSION,
        "collections": collections,
    }

    assert server._validate_backup_file(payload)["vendite"][0]["id"] == "sale-1"

    del collections["vendite"]
    try:
        server._validate_backup_file(payload)
    except server.HTTPException as exc:
        assert exc.status_code == 422
        assert "vendite" in exc.detail
    else:
        raise AssertionError("Un backup privo delle vendite deve essere rifiutato")


def test_backup_snapshot_does_not_truncate_after_25000_documents(monkeypatch):
    database = AsyncMongoMockClient()["backup_over_25000"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_many([
            {"id": f"p-{index}", "codice": f"P{index}"}
            for index in range(25_001)
        ])
        snapshot = await server.create_backup_snapshot(
            "Backup grande", collection_names=["prodotti"]
        )
        stored = await database.backup_snapshot_items.count_documents({
            "snapshot_id": snapshot["id"], "collection": "prodotti",
        })
        return snapshot, stored

    snapshot, stored = asyncio.run(scenario())
    assert snapshot["counts"]["prodotti"] == 25_001
    assert stored == 25_001
    assert snapshot["checksums"]["prodotti"]


def test_backup_validation_rejects_checksum_mismatch():
    collections = {name: [] for name in server.BACKUP_COLLECTIONS}
    collections["prodotti"] = [{"id": "p1", "codice": "P1"}]
    payload = {
        "format": server.BACKUP_FILE_FORMAT,
        "version": server.BACKUP_FILE_VERSION,
        "counts": {name: len(docs) for name, docs in collections.items()},
        "checksums": {"prodotti": hashlib.sha256(b"alterato").hexdigest()},
        "collections": collections,
    }

    with pytest.raises(HTTPException, match="Checksum") as caught:
        server._validate_backup_file(payload)

    assert caught.value.status_code == 422


def test_backup_checksum_round_trip_uses_stable_document_order(monkeypatch):
    database = AsyncMongoMockClient()["backup_checksum_roundtrip"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_many([
            {"id": "p2", "codice": "P2"},
            {"id": "p1", "codice": "P1"},
        ])
        snapshot = await server.create_backup_snapshot(
            "Checksum", collection_names=["prodotti"]
        )
        stored = await server._backup_documents(snapshot["id"], ["prodotti"])
        collections = {name: [] for name in server.BACKUP_COLLECTIONS}
        collections["prodotti"] = stored["prodotti"]
        payload = {
            "format": server.BACKUP_FILE_FORMAT,
            "version": server.BACKUP_FILE_VERSION,
            "counts": {name: len(docs) for name, docs in collections.items()},
            "checksums": snapshot["checksums"],
            "collections": collections,
        }
        return server._validate_backup_file(payload)

    validated = asyncio.run(scenario())
    assert [doc["id"] for doc in validated["prodotti"]] == ["p2", "p1"]


def test_restore_lock_rejects_concurrent_restore(monkeypatch):
    database = AsyncMongoMockClient()["restore_lock"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.operation_locks.insert_one({"_id": "backup-restore", "token": "other"})
        with pytest.raises(HTTPException) as caught:
            async with server._restore_lock():
                pass
        return caught.value

    error = asyncio.run(scenario())
    assert error.status_code == 409


def test_restore_collections_compensates_standalone_failure(monkeypatch):
    database = AsyncMongoMockClient()["restore_compensation"]

    class FailingParametersCollection:
        def __init__(self, collection):
            self.collection = collection

        def find(self, *args, **kwargs):
            return self.collection.find(*args, **kwargs)

        async def delete_many(self, *args, **kwargs):
            return await self.collection.delete_many(*args, **kwargs)

        async def insert_many(self, documents, **kwargs):
            if any(document.get("nome") == "NEW_PARAM" for document in documents):
                raise RuntimeError("simulated insert failure")
            return await self.collection.insert_many(documents, **kwargs)

    class RestoreDatabase:
        def __getitem__(self, name):
            if name == "parametri":
                return FailingParametersCollection(database.parametri)
            return database[name]

    monkeypatch.setattr(server, "db", RestoreDatabase())

    async def scenario():
        await database.prodotti.insert_one({"id": "old-product", "codice": "OLD"})
        await database.parametri.insert_one({"nome": "OLD_PARAM", "valore": 1})

        with pytest.raises(RuntimeError, match="simulated insert failure"):
            await server._restore_collections({
                "prodotti": [{"id": "new-product", "codice": "NEW"}],
                "parametri": [{"nome": "NEW_PARAM", "valore": 2}],
            })
        return (
            await database.prodotti.find({}, {"_id": 0}).to_list(None),
            await database.parametri.find({}, {"_id": 0}).to_list(None),
        )

    products, parameters = asyncio.run(scenario())

    assert products == [{"id": "old-product", "codice": "OLD"}]
    assert parameters == [{"nome": "OLD_PARAM", "valore": 1}]


def test_csv_preview_preserves_payment_types_and_normalizes_dates():
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "03/10/2026 05:52;WINSTON RED 100s;5,8;1-B02;1783;Sigarette;Contanti\n"
        "03-10-2026 02:49:46;TEREA AZURE;5,5;1-F04;20659;Tabacchi;PagoBancomat\n"
        "2026-10-03T01:20:00;ACQUA;1,0;1-A01;;Bevande;Carte\n"
    )

    parsed = server._parse_csv_vending(raw)

    assert [row["pagamento"] for row in parsed["righe"]] == ["CONTANTI", "PAGOBANCOMAT", "CARTE"]
    assert [row["colonna"] for row in parsed["righe"]] == ["B02", "F04", "A01"]
    assert all(row["data"].startswith("2026-10-03T") for row in parsed["righe"])
    assert parsed["errori"] == []
    assert parsed["saltati"] == 0


def test_csv_preview_accepts_vending_dates_with_two_digit_years():
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "11/10/26 02:33;CAMEL BLUE SOFT;5,80;1-C11;632;Sigarette;Contanti\n"
        "10-10-26 22:17:05;MARLBORO RED;6,80;1-E09;395;Sigarette;Carte\n"
        "18/7/26 15:48;TEREA AZURE;5,50;1-F04;20659;Tabacchi;PagoBancomat\n"
    )

    summary = server._summarize_vending_csv(server._parse_csv_vending(raw))

    assert summary["righe"] == 3
    assert summary["errori"] == []
    assert summary["importi_pagamenti"] == {
        "CONTANTI": 5.8,
        "CARTE": 6.8,
        "PAGOBANCOMAT": 5.5,
    }
    assert summary["data_da"] == "2026-07-18T15:48:00"
    assert summary["data_a"] == "2026-10-11T02:33:00"


def test_accounting_csv_summary_matches_payment_totals_without_stock_changes():
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "02/08/2026 05:34;A;10,00;1-A01;1;Tabacchi;Contanti\n"
        "07/10/2026 06:56;B;5,50;1-A02;2;Tabacchi;Carte\n"
        "07/10/2026 06:57;C;4,50;1-A03;3;Tabacchi;PagoBancomat\n"
    )

    summary = server._summarize_vending_csv(server._parse_csv_vending(raw))

    assert summary["righe"] == 3
    assert summary["pagamenti"] == {"CONTANTI": 1, "CARTE": 1, "PAGOBANCOMAT": 1}
    assert summary["importi_pagamenti"] == {
        "CONTANTI": 10.0,
        "CARTE": 5.5,
        "PAGOBANCOMAT": 4.5,
    }
    assert summary["totale_elettronici"] == 10.0
    assert summary["totale"] == 20.0
    assert summary["data_da"] == "2026-08-02T05:34:00"
    assert summary["data_a"] == "2026-10-07T06:57:00"


def test_csv_preview_filters_rows_after_latest_excel_vending_timestamp():
    rows = [
        {"data": "2026-10-02T07:58:00", "nome": "Gia presente"},
        {"data": "2026-10-02T10:27:00", "nome": "Nuova stesso giorno"},
        {"data": "2026-10-03T05:52:00", "nome": "Nuova giorno dopo"},
    ]
    historical = [{"raw": ["id", "2026-10-02 07:58:00", "2026-10-02"]}]

    filtered, cutoff = server._filter_new_csv_vending_rows(rows, historical)

    assert cutoff == server.datetime(2026, 10, 2, 7, 58)
    assert [row["nome"] for row in filtered] == ["Nuova stesso giorno", "Nuova giorno dopo"]


def test_csv_preview_keeps_every_valid_row_without_excel_history():
    rows = [{"data": "2026-10-03T05:52:00", "nome": "Vendita"}]

    filtered, cutoff = server._filter_new_csv_vending_rows(rows, [])

    assert filtered == rows
    assert cutoff is None


def test_vending_column_normalization_preserves_internal_codes():
    assert server._normalize_vending_column("1-B02") == "B02"
    assert server._normalize_vending_column(" 1-f04 ") == "F04"
    assert server._normalize_vending_column("A07") == "A07"

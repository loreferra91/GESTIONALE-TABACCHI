"""External JSON backup checks on isolated MongoDB mocks; no production access."""
import asyncio
import hashlib
import io
import json

import pytest
from fastapi import HTTPException, UploadFile
from mongomock_motor import AsyncMongoMockClient

from backend import server
from backend.backup_router import BACKUP_COLLECTIONS, create_backup_router


def _document_checksum(documents):
    digest = hashlib.sha256()
    for document in documents:
        digest.update(json.dumps(
            document, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str,
        ).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _backup_payload():
    collections = {name: [] for name in BACKUP_COLLECTIONS}
    collections["prodotti"] = [{"id": "item-restored", "codice": "123", "giacenza": 9}]
    collections["vendite"] = [{"id": "sale-restored", "quantita": 2}]
    return {
        "format": server.BACKUP_FILE_FORMAT,
        "version": server.BACKUP_FILE_VERSION,
        "created_at": "2026-11-19T10:00:00+00:00",
        "label": "External restore fixture",
        "counts": {key: len(value) for key, value in collections.items()},
        "checksums": {key: _document_checksum(value) for key, value in collections.items()},
        "collections": collections,
    }


def _upload(payload):
    return UploadFile(
        filename="gestionale-backup-fixture.json",
        file=io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


def _router(database, *, max_bytes=20 * 1024 * 1024):
    async def run_without_transactions(operation):
        return await operation(None)

    async def read_capped(upload):
        return await server._read_capped(upload, max_bytes=max_bytes)

    return create_backup_router(
        get_database=lambda: database,
        session_kwargs=lambda session: {},
        run_transactionally=run_without_transactions,
        read_capped=read_capped,
    )


def test_external_restore_preview_is_read_only_and_recognizes_current_ledgers():
    database = AsyncMongoMockClient()["gestionale_cert_external_preview"]
    endpoints = _router(database)
    payload = _backup_payload()

    async def scenario():
        await database.prodotti.insert_one({"id": "existing", "codice": "888"})
        preview = await endpoints["backup_restore_file_preview"](_upload(payload))
        docs = await database.prodotti.find({}, {"_id": 0}).to_list(None)
        snapshot_count = await database.backup_snapshots.count_documents({})
        return preview, docs, snapshot_count

    preview, docs, snapshot_count = asyncio.run(scenario())
    assert preview["can_restore"] is True
    assert preview["total_docs"] == 2
    assert len(preview["counts"]) == len(BACKUP_COLLECTIONS)
    assert preview["missing_checksum_collections"] == []
    assert preview["missing_optional_collections"] == []
    assert docs == [{"id": "existing", "codice": "888"}]
    assert snapshot_count == 0


def test_external_restore_disposable_roundtrip_and_pre_restore_backup():
    database = AsyncMongoMockClient()["gestionale_cert_external_restore"]
    endpoints = _router(database)
    payload = _backup_payload()

    async def scenario():
        await database.prodotti.insert_one({"id": "old", "codice": "777"})
        result = await endpoints["backup_restore_file"](_upload(payload))
        products = await database.prodotti.find({}, {"_id": 0}).to_list(None)
        sales = await database.vendite.find({}, {"_id": 0}).to_list(None)
        snapshot = await database.backup_snapshots.find_one(
            {"id": result["pre_restore_backup"]}, {"_id": 0}
        )
        previous_products = await database.backup_snapshot_items.find(
            {"snapshot_id": result["pre_restore_backup"], "collection": "prodotti"},
            {"_id": 0, "doc": 1},
        ).to_list(None)
        return result, products, sales, snapshot, previous_products

    result, products, sales, snapshot, previous_products = asyncio.run(scenario())
    assert result["ok"] is True
    assert result["restored_docs"] == 2
    assert products[0]["id"] == "item-restored"
    assert len(products) == 1
    assert sales == [{"id": "sale-restored", "quantita": 2}]
    assert snapshot["status"] == "READY"
    assert snapshot["reason"] == "pre-restore-file"
    assert previous_products[0]["doc"]["id"] == "old"


def test_restore_blocks_legacy_backup_omitting_populated_ledger():
    database = AsyncMongoMockClient()["gestionale_cert_legacy_restore"]
    endpoints = _router(database)
    payload = _backup_payload()
    payload["collections"].pop("vending_operations")
    payload["counts"].pop("vending_operations")
    payload["checksums"].pop("vending_operations")

    async def scenario():
        await database.vending_operations.insert_one({"id": "operation-already-present"})
        await database.prodotti.insert_one({"id": "current"})
        preview = await endpoints["backup_restore_file_preview"](_upload(payload))
        with pytest.raises(HTTPException) as error:
            await endpoints["backup_restore_file"](_upload(payload))
        products = await database.prodotti.find({}, {"_id": 0}).to_list(None)
        snapshots = await database.backup_snapshots.count_documents({})
        return preview, error.value, products, snapshots

    preview, error, products, snapshots = asyncio.run(scenario())
    assert preview["can_restore"] is False
    assert preview["nonempty_missing_collections"] == ["vending_operations"]
    assert error.status_code == 409
    assert products == [{"id": "current"}]
    assert snapshots == 0


def test_legacy_backup_without_optional_empty_collections_is_accepted():
    database = AsyncMongoMockClient()["gestionale_cert_legacy_empty"]
    endpoints = _router(database)
    payload = _backup_payload()
    payload["collections"].pop("vending_operations")
    payload["counts"].pop("vending_operations")
    payload["checksums"].pop("vending_operations")

    preview = asyncio.run(endpoints["backup_restore_file_preview"](_upload(payload)))
    assert preview["can_restore"] is True
    assert "vending_operations" in preview["missing_optional_collections"]
    assert preview["warnings"]


@pytest.mark.parametrize("invalid_counts", [
    {"prodotti": "1"}, {"prodotti": -1}, {"prodotti": True}, ["invalid"],
])
def test_external_restore_bad_counts_return_422(invalid_counts):
    payload = _backup_payload()
    payload["counts"] = invalid_counts
    with pytest.raises(HTTPException) as error:
        server._validate_backup_file(payload)
    assert error.value.status_code == 422


def test_external_restore_rejects_corrupted_json_without_db_writes():
    database = AsyncMongoMockClient()["gestionale_cert_invalid_json"]
    endpoints = _router(database)

    async def scenario():
        upload = UploadFile(filename="invalid.json", file=io.BytesIO(b"{invalid"))
        with pytest.raises(HTTPException) as error:
            await endpoints["backup_restore_file_preview"](upload)
        return error.value, await database.backup_snapshots.count_documents({})

    error, backups = asyncio.run(scenario())
    assert error.status_code == 422
    assert backups == 0


def test_external_restore_oversized_json_is_rejected_before_snapshot():
    database = AsyncMongoMockClient()["gestionale_cert_oversize"]
    endpoints = _router(database, max_bytes=64)

    async def scenario():
        upload = UploadFile(filename="large.json", file=io.BytesIO(b"x" * 65))
        with pytest.raises(HTTPException) as error:
            await endpoints["backup_restore_file"](upload)
        return error.value, await database.backup_snapshots.count_documents({})

    error, backups = asyncio.run(scenario())
    assert error.status_code == 413
    assert backups == 0


def test_external_restore_unknown_collections_still_rejected():
    payload = _backup_payload()
    payload["collections"]["unknown_collection"] = []
    with pytest.raises(HTTPException) as error:
        server._validate_backup_file(payload)
    assert error.value.status_code == 422
    assert "sconosciute" in error.value.detail

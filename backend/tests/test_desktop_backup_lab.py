"""Gate 4A full authenticated backup and restore on disposable real MongoDB."""
import os
from pathlib import Path
from uuid import uuid4

import pytest
from pymongo import MongoClient

from desktop_runtime.backup_lab import (
    BackupLaboratoryError, encrypted_backup,
    read_encrypted_backup, restore_into_empty_lab,
)
from backend.tests.test_desktop_staging_replica import _safe_target

PASSWORD = "test-only-long-passphrase-never-use-in-production"


def test_encrypted_backup_restore_and_fail_closed(tmp_path):
    uri = _safe_target()
    client = MongoClient(uri, serverSelectionTimeoutMS=4000)
    source_name = "desktop_backup_cert_" + uuid4().hex
    target_name = "desktop_restore_cert_" + uuid4().hex
    source = client[source_name]
    target = client[target_name]
    archive = tmp_path / "sample.gtbk"
    try:
        source.sync_metadata.insert_one({"_id": "backup-laboratory", "value": "disposable-only"})
        source.desktop_outbox.insert_many([
            {"_id": "sale-1", "status": "PENDING", "importo_cents": 1234},
            {"_id": "sale-2", "status": "STAGED", "importo_cents": 5678},
        ])
        source.lab_inventory.insert_one({"_id": "product-a", "quantity": 5})
        source.desktop_outbox.create_index("status", name="status_unique", unique=True)
        source.create_collection(
            "validated_lab",
            validator={"$jsonSchema": {"bsonType": "object", "required": ["_id"]}},
            validationAction="error",
        )
        result = encrypted_backup(source, archive, PASSWORD)
        assert result["verified"] and result["collections"]["desktop_outbox"] == 2
        assert archive.read_bytes().startswith(b"GTBK-LAB")
        with pytest.raises(FileExistsError):
            encrypted_backup(source, archive, PASSWORD)
        with pytest.raises(BackupLaboratoryError, match="Wrong password"):
            read_encrypted_backup(archive, "incorrect-password-that-is-long")

        damaged = tmp_path / "altered.gtbk"
        bits = bytearray(archive.read_bytes())
        bits[-3] ^= 1
        damaged.write_bytes(bits)
        with pytest.raises(BackupLaboratoryError, match="modified archive"):
            restore_into_empty_lab(target, damaged, PASSWORD)
        assert not target.list_collection_names()
        with pytest.raises(BackupLaboratoryError, match="Wrong password"):
            restore_into_empty_lab(target, archive, "incorrect-password-that-is-long")
        assert not target.list_collection_names()

        restored = restore_into_empty_lab(target, archive, PASSWORD)
        assert restored["restored"] and restored["verified"]
        assert restored["counts"]["desktop_outbox"] == 2
        assert target.desktop_outbox.find_one({"_id": "sale-2"})["status"] == "STAGED"
        restored_index = target.desktop_outbox.index_information()["status_unique"]
        assert restored_index["unique"] is True
        restored_opts = list(target.list_collections(filter={"name": "validated_lab"}))[0]["options"]
        assert "validator" in restored_opts
        # Unique constraint is live after recovery, not just in backup metadata.
        with pytest.raises(Exception):
            target.desktop_outbox.insert_one({"_id": "sale-3", "status": "STAGED"})
        with pytest.raises(BackupLaboratoryError, match="not empty"):
            restore_into_empty_lab(target, archive, PASSWORD)
        assert target.desktop_outbox.count_documents({}) == 2
        # Neither backup nor restore ever changes original business data.
        assert source.desktop_outbox.count_documents({}) == 2
        assert source.lab_inventory.find_one({"_id": "product-a"})["quantity"] == 5
    finally:
        client.drop_database(source_name)
        client.drop_database(target_name)
        client.close()


def test_backup_refuses_unmarked_and_nonlab_source(tmp_path):
    uri = _safe_target()
    client = MongoClient(uri, serverSelectionTimeoutMS=4000)
    name = "desktop_backup_cert_" + uuid4().hex
    try:
        with pytest.raises(BackupLaboratoryError, match="marker"):
            encrypted_backup(client[name], tmp_path / "no.gtbk", PASSWORD)
        with pytest.raises(BackupLaboratoryError, match="disposable"):
            encrypted_backup(client["gestionale_desktop"], tmp_path / "no.gtbk", PASSWORD)
    finally:
        client.drop_database(name)
        client.close()

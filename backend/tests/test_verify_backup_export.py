"""Off-line verifier only; test fixtures contain no operational data."""
import hashlib
import json

import pytest

from backend.backup_router import BACKUP_COLLECTIONS as SERVER_NAMES
from scripts.verify_backup_export import (
    BACKUP_COLLECTIONS, BackupValidationError, inspect_export,
)


def _payload():
    docs = {name: [] for name in BACKUP_COLLECTIONS}
    docs["prodotti"] = [{"codice": "SYNTHETIC-P1", "giacenza_negozio": 1}]
    checksums = {}
    for name, values in docs.items():
        h = hashlib.sha256()
        for doc in values:
            h.update(json.dumps(doc, sort_keys=True, ensure_ascii=False,
                                separators=(",", ":")).encode("utf-8"))
            h.update(b"\n")
        checksums[name] = h.hexdigest()
    return {
        "format": "gestionale-tabacchi-backup",
        "version": 1,
        "created_at": "2026-11-20T00:00:00+00:00",
        "collections": docs,
        "checksums": checksums,
        "counts": {name: len(rows) for name, rows in docs.items()},
    }


def _write(tmp_path, payload):
    p = tmp_path / "fixture.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_export_verifier_schema_keeps_up_with_application():
    assert set(BACKUP_COLLECTIONS) == set(SERVER_NAMES)


def test_offline_backup_verifier_hashes_all_collections_without_leaking_docs(tmp_path):
    report = inspect_export(_write(tmp_path, _payload()))
    assert report["result"] == "pass"
    assert report["collections_verified"] == len(BACKUP_COLLECTIONS)
    assert report["total_documents_verified"] == 1
    assert report["document_hashes_verified"] is True
    assert report["independent_external_copy_verified"] is False
    assert report["restore_verified"] is False
    assert "SYNTHETIC-P1" not in str(report)
    assert len(report["file_sha256"]) == 64


@pytest.mark.parametrize("change", [
    lambda p: p["collections"]["prodotti"][0].update(giacenza_negozio=100),
    lambda p: p["counts"].update(prodotti=2),
    lambda p: p["checksums"].pop("prodotti"),
    lambda p: p["collections"].update(unexpected=[{"value": "synthetic"}]),
    lambda p: p["collections"].pop("prodotti"),
    lambda p: p.update(format="unknown"),
])
def test_offline_backup_verifier_rejects_tampering(tmp_path, change):
    data = _payload()
    change(data)
    with pytest.raises(BackupValidationError):
        inspect_export(_write(tmp_path, data))


def test_offline_backup_verifier_refuses_symlink_and_untrusted_files(tmp_path):
    file = _write(tmp_path, _payload())
    target = tmp_path / "symlink.json"
    target.symlink_to(file)
    with pytest.raises(BackupValidationError):
        inspect_export(target)
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{garbled", encoding="utf-8")
    with pytest.raises(BackupValidationError):
        inspect_export(invalid)

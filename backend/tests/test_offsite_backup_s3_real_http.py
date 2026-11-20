"""S3 HTTP integration on disposable Moto only; never contacts AWS or Atlas.

Encryption and actual MongoDB restore are exercised separately by the
staging-encrypted-backup-real-restore CI pipeline.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import offsite_backup_s3 as offsite


pytestmark = pytest.mark.skipif(
    os.environ.get("CERT_DISPOSABLE_S3") != "1",
    reason="Only run against an explicitly configured disposable local S3 server",
)


def _synthetic_encrypted_archive(*, db_name, config_file, output_dir, recipient):
    # Transport-only fixture: deliberately NOT a real Mongo dump or GPG packet.
    # GPG + mongodump + mongorestore are certified in the separate 25k workflow.
    payload = b"transport-only synthetic encrypted archive sentinel" * 37
    archive = Path(output_dir) / "synthetic-ci.archive.gz.gpg"
    archive.write_bytes(payload)
    return {
        "filename": archive.name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "database": db_name,
        "created_at": "2026-11-20T00:00:00+00:00",
        "status": "encrypted",
        "restore_certified": False,
    }


def _s3_bytes(uri):
    proc = subprocess.run(
        ["aws", "s3", "cp", uri, "-", "--only-show-errors"],
        check=True, capture_output=True,
    )
    return proc.stdout


def _s3_exists(uri):
    proc = subprocess.run(
        ["aws", "s3", "ls", uri],
        check=True, capture_output=True, text=True,
    )
    return bool(proc.stdout.strip())


def test_disposable_s3_upload_readback_manifest_and_sse(monkeypatch):
    monkeypatch.setattr(offsite, "backup", _synthetic_encrypted_archive)
    result = offsite.upload_encrypted_backup(
        db_name="gestionale_staging",
        config_file="/unused/transport-only",
        recipient="A" * 40,
        bucket="cert-gestionale-backups",
        prefix="cert/transport-pass",
    )
    assert result["offcluster_verified"] is True
    assert result["restore_certified"] is False
    archive = _s3_bytes(result["archive_key"])
    assert hashlib.sha256(archive).hexdigest() == result["archive_sha256"]
    manifest = json.loads(_s3_bytes(result["manifest_key"]).decode("utf-8"))
    assert manifest["sha256"] == result["archive_sha256"]
    assert manifest["bytes"] == len(archive)
    assert manifest["offcluster_verified"] is True
    assert manifest["restore_certified"] is False
    assert manifest["offcluster_destination"] == result["archive_key"]
    assert "password" not in json.dumps(manifest).lower()

    # Moto implements the same S3 HTTP API and exposes the SSE metadata.
    # Never remove the SSE requirement from the production S3 script.
    bucket = "cert-gestionale-backups"
    key = result["archive_key"].split(f"s3://{bucket}/", 1)[1]
    head = subprocess.run(
        ["aws", "s3api", "head-object", "--bucket", bucket, "--key", key],
        check=True, capture_output=True, text=True,
    )
    assert json.loads(head.stdout)["ServerSideEncryption"] == "AES256"


def test_corrupted_remote_readback_never_replaces_last_success(monkeypatch, tmp_path):
    monkeypatch.setattr(offsite, "backup", _synthetic_encrypted_archive)
    original = offsite._run_aws
    changed = {"done": False}

    def corrupt_after_upload(*args):
        original(*args)
        if (
            not changed["done"]
            and args[:2] == ("s3", "cp")
            and args[2].endswith(".archive.gz.gpg")
            and str(args[3]).startswith("s3://")
        ):
            changed["done"] = True
            corrupt = tmp_path / "corrupted.archive.gz.gpg"
            corrupt.write_bytes(b"corrupted remote object")
            original("s3", "cp", str(corrupt), args[3],
                     "--only-show-errors", "--sse", "AES256")

    monkeypatch.setattr(offsite, "_run_aws", corrupt_after_upload)
    with pytest.raises(RuntimeError, match="read-back verification failed"):
        offsite.upload_encrypted_backup(
            db_name="gestionale_staging",
            config_file="/unused/transport-only",
            recipient="A" * 40,
            bucket="cert-gestionale-backups",
            prefix="cert/transport-corrupt",
        )
    assert changed["done"] is True
    assert not _s3_exists(
        "s3://cert-gestionale-backups/cert/transport-corrupt/last-success.json"
    )

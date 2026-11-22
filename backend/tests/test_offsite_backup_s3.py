"""No real Atlas, AWS credentials or customer data are used in these tests."""
import hashlib
from pathlib import Path
import subprocess

import pytest

from scripts import offsite_backup_s3 as offsite


@pytest.mark.parametrize("bucket,prefix", [
    ("", "gestionale/staging"),
    ("UPPER", "gestionale/staging"),
    ("valid-bucket", "../secrets"),
    ("valid-bucket", "one//two"),
    ("valid-bucket", "one;rm -rf"),
    ("https://s3.example", "staging"),
    ("valid-bucket", ""),
])
def test_invalid_offsite_destination_is_refused(bucket, prefix):
    with pytest.raises(ValueError):
        offsite.s3_location(bucket, prefix)


def _synthetic_backup(archive_bytes=b"synthetic-encrypted-test-fixture", corrupt_record=False):
    def create(*, db_name, config_file, output_dir, recipient):
        source = Path(output_dir) / "test.archive.gz.gpg"
        source.write_bytes(archive_bytes)
        digest = hashlib.sha256(archive_bytes).hexdigest()
        return {
            "database": db_name, "filename": source.name, "sha256": (
                "0" * 64 if corrupt_record else digest
            ),
            "bytes": len(archive_bytes), "status": "encrypted",
            "created_at": "2026-11-19T00:00:00+00:00",
            "restore_certified": False,
        }
    return create


def _fake_s3(remote, *, fail_on_archive_upload=False, corrupt_readback=False):
    def run(*args):
        assert args[0:2] == ("s3", "cp")
        src, dest = args[2:4]
        if src.startswith("s3://"):
            data = remote[src]
            if corrupt_readback and "archive.gz.gpg" in src:
                data += b"wrong"
            Path(dest).write_bytes(data)
        else:
            if fail_on_archive_upload and "archive.gz.gpg" in src:
                raise subprocess.CalledProcessError(2, "aws")
            remote[dest] = Path(src).read_bytes()
    return run


def test_encrypted_offsite_roundtrip_and_manifest_are_verified(monkeypatch):
    remote = {}
    monkeypatch.setattr(offsite, "backup", _synthetic_backup())
    monkeypatch.setattr(offsite, "_run_aws", _fake_s3(remote))
    result = offsite.upload_encrypted_backup(
        db_name="gestionale_staging",
        config_file="/not-used/secret-config",
        recipient="A" * 40,
        bucket="staging-backups-example",
        prefix="gestionale/staging",
    )
    assert result["offcluster_verified"] is True
    assert result["restore_certified"] is False
    assert result["manifest_key"] in remote
    assert result["archive_key"] in remote
    assert b"private" not in remote[result["manifest_key"]]


def test_upload_failure_does_not_publish_success_manifest(monkeypatch):
    remote = {"s3://staging-backups-example/staging/last-success.json": b"previous-success"}
    monkeypatch.setattr(offsite, "backup", _synthetic_backup())
    monkeypatch.setattr(offsite, "_run_aws", _fake_s3(remote, fail_on_archive_upload=True))
    with pytest.raises(subprocess.CalledProcessError):
        offsite.upload_encrypted_backup(
            db_name="gestionale_staging", config_file="/not-used",
            recipient="A" * 40, bucket="staging-backups-example", prefix="staging",
        )
    assert remote["s3://staging-backups-example/staging/last-success.json"] == b"previous-success"


def test_mismatched_archive_after_offsite_readback_does_not_publish_manifest(monkeypatch):
    remote = {}
    monkeypatch.setattr(offsite, "backup", _synthetic_backup())
    monkeypatch.setattr(offsite, "_run_aws", _fake_s3(remote, corrupt_readback=True))
    with pytest.raises(RuntimeError, match="read-back verification failed"):
        offsite.upload_encrypted_backup(
            db_name="gestionale_staging", config_file="/not-used",
            recipient="A" * 40, bucket="staging-backups-example", prefix="staging",
        )
    assert "s3://staging-backups-example/staging/last-success.json" not in remote


def test_source_archive_hash_mismatch_is_blocked_before_upload(monkeypatch):
    remote = {}
    monkeypatch.setattr(offsite, "backup", _synthetic_backup(corrupt_record=True))
    monkeypatch.setattr(offsite, "_run_aws", _fake_s3(remote))
    with pytest.raises(RuntimeError, match="before upload"):
        offsite.upload_encrypted_backup(
            db_name="gestionale_staging", config_file="/not-used",
            recipient="A" * 40, bucket="staging-backups-example", prefix="staging",
        )
    assert remote == {}

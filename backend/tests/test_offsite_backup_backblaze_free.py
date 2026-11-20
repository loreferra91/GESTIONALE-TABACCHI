"""Backblaze free-tier integration guards; tests never access B2 or AWS."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scripts import offsite_backup_s3 as offsite


B2 = "https://s3.eu-central-003.backblazeb2.com"


@pytest.mark.parametrize("endpoint", [
    "http://s3.eu-central-003.backblazeb2.com",
    "https://other.example.com",
    "https://s3.eu-central-003.backblazeb2.com/other",
    "https://s3.eu-central-003.backblazeb2.com?x=1",
    "https://person:token@s3.eu-central-003.backblazeb2.com",
    "https://s3.eu-central-003.backblazeb2.com:443",
    "https://127.0.0.1:5000",
    "https://s3.eu-central-003.backblazeb2.com.evil.example",
])
def test_rejects_non_official_b2_endpoints(endpoint):
    with pytest.raises(ValueError):
        offsite.validate_endpoint(endpoint)


def test_official_backblaze_endpoint_and_aws_default_allowed():
    assert offsite.validate_endpoint(None) is None
    assert offsite.validate_endpoint(B2) == B2


def test_aws_cli_forwards_explicit_b2_endpoint_without_credentials(monkeypatch):
    calls = []

    def capture(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(offsite.subprocess, "run", capture)
    offsite._run_aws("s3api", "list-object-versions", "--bucket", "cert-private", endpoint=B2)
    assert calls[0][:3] == ["aws", "--endpoint-url", B2]
    assert "secret" not in str(calls[0])
    assert calls[0][3:] == ["s3api", "list-object-versions", "--bucket", "cert-private"]


def test_capacity_guard_includes_versioned_objects(monkeypatch):
    history = {
        "Versions": [
            {"Key": "one", "Size": 800_000_000},
            {"Key": "one", "Size": 800_000_000},
            {"Key": "two", "Size": 100_000_000},
        ],
        "DeleteMarkers": [{"Key": "old"}],
    }

    monkeypatch.setattr(offsite, "_run_aws", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 0, stdout=json.dumps(history)))
    assert offsite._remote_version_bytes("cert-private", endpoint=B2) == 1_700_000_000


def test_bad_version_inventory_fails_closed(monkeypatch):
    monkeypatch.setattr(offsite, "_run_aws", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 0, stdout='{"Versions":[{"Size":-4}]}'))
    with pytest.raises(RuntimeError, match="inventory"):
        offsite._remote_version_bytes("cert-private", endpoint=B2)


def test_storage_budget_blocks_before_upload_and_does_not_update_manifest(monkeypatch):
    operations = []

    def make_backup(*, db_name, config_file, output_dir, recipient):
        path = Path(output_dir) / "cert.archive.gz.gpg"
        path.write_bytes(b"synthetic encrypted payload placeholder")
        return {
            "filename": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "database": db_name,
            "created_at": "2026-11-20T00:00:00+00:00",
            "status": "encrypted",
            "restore_certified": False,
        }

    monkeypatch.setattr(offsite, "backup", make_backup)
    monkeypatch.setattr(
        offsite, "_remote_version_bytes",
        lambda bucket, *, endpoint: offsite.BACKBLAZE_FREE_GUARD_BYTES - 20,
    )
    monkeypatch.setattr(offsite, "_run_aws", lambda *args, **kwargs: operations.append(args))
    with pytest.raises(RuntimeError, match="safety budget exceeded"):
        offsite.upload_encrypted_backup(
            db_name="gestionale_staging",
            config_file="/unused",
            recipient="A" * 40,
            bucket="cert-private",
            prefix="gestionale/staging",
            endpoint=B2,
        )
    assert operations == []

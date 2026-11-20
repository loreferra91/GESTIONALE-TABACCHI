"""Offline unit checks for the Backblaze B2 independent restore runner."""
import hashlib
import json
import pytest

from scripts.certify_macos_b2_backup import checked_local_archive, validate_target


def prepared(tmp_path):
    encrypted = tmp_path / "gestionale-20261120T163430Z-id.archive.gz.gpg"
    encrypted.write_bytes(b"encrypted-content-fixture")
    record = {
        "filename": encrypted.name,
        "bytes": encrypted.stat().st_size,
        "sha256": hashlib.sha256(encrypted.read_bytes()).hexdigest(),
    }
    (tmp_path / "last-success.json").write_text(json.dumps(record))
    return encrypted, record


def test_verified_local_manifest(tmp_path):
    encrypted, record = prepared(tmp_path)
    assert checked_local_archive(tmp_path) == (encrypted, record)


def test_detects_tampering(tmp_path):
    encrypted, _ = prepared(tmp_path)
    encrypted.write_bytes(b"other-content")
    with pytest.raises(ValueError, match="differs"):
        checked_local_archive(tmp_path)


def test_prevents_manifest_path_traversal(tmp_path):
    _, record = prepared(tmp_path)
    record["filename"] = "../secrets.archive.gz.gpg"
    (tmp_path / "last-success.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="filename"):
        checked_local_archive(tmp_path)


def test_rejects_backup_symlink(tmp_path):
    original = tmp_path / "real"
    original.mkdir()
    fake = tmp_path / "fake"
    fake.symlink_to(original, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        checked_local_archive(fake)


def test_rejects_unsafe_target():
    validate_target(
        "gestionale-tabacchi-recovery-2026",
        "https://s3.eu-central-003.backblazeb2.com",
        "gestionale-b2",
    )
    for bad in [
        "https://evil.example.com",
        "http://s3.eu-central-003.backblazeb2.com",
        "https://s3.eu-central-003.backblazeb2.com/secret",
    ]:
        with pytest.raises(ValueError):
            validate_target("gestionale-tabacchi-recovery-2026", bad, "gestionale-b2")
    with pytest.raises(ValueError):
        validate_target(
            "BUCKET", "https://s3.eu-central-003.backblazeb2.com", "gestionale-b2"
        )


def test_rejects_unverified_manifest(tmp_path):
    with pytest.raises(ValueError):
        checked_local_archive(tmp_path)

"""Offline unit checks for the Backblaze B2 independent restore runner."""
import hashlib
import json
from pathlib import Path
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


def test_mock_b2_roundtrip_checks_downloaded_bytes(monkeypatch, tmp_path):
    import shutil
    from scripts import certify_macos_b2_backup as m

    encrypted, record = prepared(tmp_path)
    calls = []

    def fake_aws(profile, endpoint, *args, **kwargs):
        calls.append(args)
        if args[0:2] == ("s3api", "head-object"):
            return json.dumps({"ContentLength": record["bytes"], "Metadata": {"sha256": record["sha256"]}})
        if (args[0:2] == ("s3", "cp") and
                str(args[2]).startswith("s3://") and
                str(args[3]).endswith("archive.gz.gpg")):
            shutil.copyfile(encrypted, args[3])
        return ""

    monkeypatch.setattr(m, "aws_call", fake_aws)
    directory = tmp_path / "remote"
    directory.mkdir()
    downloaded, key = m.remote_roundtrip(
        encrypted, record, bucket="gestionale-tabacchi-recovery-2026",
        endpoint=m.DEFAULT_ENDPOINT, profile="gestionale-b2", destination=directory
    )
    assert m.digest(downloaded) == record["sha256"]
    assert key.startswith("gestionale/production/recovery-drills/")
    assert not any("delete" in str(call).lower() for call in calls)


def test_mock_b2_roundtrip_rejects_tampered_download(monkeypatch, tmp_path):
    from scripts import certify_macos_b2_backup as m

    encrypted, record = prepared(tmp_path)

    def fake_aws(profile, endpoint, *args, **kwargs):
        if args[0:2] == ("s3api", "head-object"):
            return json.dumps({"ContentLength": record["bytes"], "Metadata": {"sha256": record["sha256"]}})
        if (args[0:2] == ("s3", "cp") and
                str(args[2]).startswith("s3://") and
                str(args[3]).endswith("archive.gz.gpg")):
            (Path(args[3])).write_bytes(b"X" * record["bytes"])
        return ""

    monkeypatch.setattr(m, "aws_call", fake_aws)
    directory = tmp_path / "remote"
    directory.mkdir()
    with pytest.raises(RuntimeError, match="differs"):
        m.remote_roundtrip(
            encrypted, record, bucket="gestionale-tabacchi-recovery-2026",
            endpoint=m.DEFAULT_ENDPOINT, profile="gestionale-b2", destination=directory
        )

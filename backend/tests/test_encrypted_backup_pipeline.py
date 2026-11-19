"""Isolated pipeline tests: synthetic bytes, disposable GPG key, NO real MongoDB."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts.encrypted_backup import backup
from scripts.backup_health import check_backup


@pytest.fixture
def local_gpg(tmp_path):
    if not shutil.which("gpg"):
        pytest.skip("GnuPG is not installed")
    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    subprocess.run(
        ["gpg", "--homedir", str(home), "--batch", "--pinentry-mode", "loopback",
         "--passphrase", "", "--quick-generate-key",
         "Isolated Backup Test <backup-test@example.invalid>", "default", "default", "never"],
        check=True, capture_output=True, timeout=40,
    )
    keys = subprocess.run(
        ["gpg", "--homedir", str(home), "--batch", "--with-colons", "--list-keys"],
        check=True, capture_output=True, text=True, timeout=15,
    ).stdout
    fingerprint = next(row.split(":")[9] for row in keys.splitlines() if row.startswith("fpr:"))
    return home, fingerprint


def fake_dump_command(tmp_path, payload: bytes, exit_code: int = 0):
    script = tmp_path / f"fake-dump-{exit_code}"
    script.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        + f"sys.stdout.buffer.write({payload!r})\n"
        + f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    return str(script)


def test_encrypted_backup_stream_and_failure_does_not_overwrite_last_success(tmp_path, local_gpg):
    home, fingerprint = local_gpg
    config = tmp_path / "mongodump.yaml"
    config.write_text("uri: mongodb://127.0.0.1:27017\n", encoding="utf-8")
    config.chmod(0o600)
    target = tmp_path / "independent"
    payload = b"SYNTHETIC_ONLY_NOT_A_MONGODB_ARCHIVE"
    record = backup(
        db_name="gestionale_dev", config_file=str(config), output_dir=str(target),
        recipient=fingerprint, gpg_home=str(home),
        mongodump_cmd=fake_dump_command(tmp_path, payload),
    )
    encrypted = target / record["filename"]
    assert encrypted.exists() and encrypted.read_bytes() != payload
    assert record["restore_certified"] is False
    assert record["sha256"] == hashlib.sha256(encrypted.read_bytes()).hexdigest()
    decrypted = subprocess.run(
        ["gpg", "--homedir", str(home), "--batch", "--decrypt", str(encrypted)],
        check=True, capture_output=True, timeout=20,
    ).stdout
    assert decrypted == payload
    assert check_backup(str(target), 24, verify_hash=True)["backup_fresh"] is True

    previous_manifest = (target / "last-success.json").read_bytes()
    with pytest.raises(RuntimeError, match="Backup or encryption failed"):
        backup(
            db_name="gestionale_dev", config_file=str(config), output_dir=str(target),
            recipient=fingerprint, gpg_home=str(home),
            mongodump_cmd=fake_dump_command(tmp_path, b"FAIL", exit_code=5),
        )
    assert previous_manifest == (target / "last-success.json").read_bytes()
    assert not list(target.glob("*.partial"))
    assert not list(target.glob(".*.partial"))


def test_backup_rejects_insecure_config_and_health_missing(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("uri: mongodb://example.invalid")
    config.chmod(0o644)
    with pytest.raises(RuntimeError, match="0600"):
        backup(
            db_name="gestionale_dev", config_file=str(config),
            output_dir=str(tmp_path / "out"), recipient="f" * 40,
        )
    assert check_backup(str(tmp_path), 24)["backup_fresh"] is False

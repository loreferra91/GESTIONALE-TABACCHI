"""Offline safety checks for changing the macOS archive path and launchd schedule."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "reconfigure_macos_backup.py"
spec = importlib.util.spec_from_file_location("backup_reconfigure", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def sample_home(tmp_path):
    home = tmp_path / "mac"
    home.mkdir(mode=0o700)
    source = home / "Gestionale-Backups"
    source.mkdir(mode=0o700)
    archive = source / "gestionale-20261120T163430Z-archive.archive.gz.gpg"
    archive.write_bytes(b"GPG ENCRYPTED TEST ARCHIVE" * 21)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (source / "last-success.json").write_text(json.dumps({
        "filename": archive.name, "bytes": archive.stat().st_size,
        "sha256": digest,
    }))
    plist = home / "Library/LaunchAgents" / f"{module.LABEL}.plist"
    plist.parent.mkdir(parents=True)
    plist.write_bytes(plistlib.dumps({
        "Label": module.LABEL,
        "ProgramArguments": [
            "/usr/bin/python3",
            str(home / "GESTIONALE-TABACCHI-BACKUP/scripts/macos_backup_runner.py"),
            "--allow-production",
        ],
        "StartCalendarInterval": {"Hour": 3, "Minute": 0},
    }))
    return home, source, archive, plist


def test_migration_preserves_source_and_moves_schedule(sample_home, monkeypatch):
    home, source, archive, plist = sample_home
    calls = []

    def fake_launchctl(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="state = waiting\n")

    monkeypatch.setattr(module, "launchctl", fake_launchctl)
    previous_archive = archive.read_bytes()
    module.apply(home=home, destination=home / "Backup-Gestionale-Tabacchi")
    destination = home / "Backup-Gestionale-Tabacchi"
    assert (destination / archive.name).read_bytes() == previous_archive
    assert module.source_files(destination)
    new_plist = plistlib.loads(plist.read_bytes())
    assert new_plist["StartCalendarInterval"] == {"Hour": 11, "Minute": 0}
    assert new_plist["ProgramArguments"][-2:] == ["--output-dir", str(destination)]
    assert archive.read_bytes() == previous_archive
    assert list(plist.parent.glob("*.bak"))
    assert ("bootout", f"gui/{os.getuid()}", str(plist)) in calls
    assert ("bootstrap", f"gui/{os.getuid()}", str(plist)) in calls


def test_migration_refuses_conflicting_archive(sample_home, monkeypatch):
    home, source, archive, plist = sample_home
    target = home / "Backup-Gestionale-Tabacchi"
    target.mkdir(mode=0o700)
    (target / archive.name).write_bytes(b"ALTERED")
    previous_plist = plist.read_bytes()
    with pytest.raises(RuntimeError, match="Conflicting destination"):
        module.apply(home=home, destination=target)
    assert plist.read_bytes() == previous_plist


def test_migration_does_not_stop_running_job(sample_home, monkeypatch):
    home, source, archive, plist = sample_home
    monkeypatch.setattr(module, "launchctl", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout="state = running\n"
    ))
    previous_plist = plist.read_bytes()
    with pytest.raises(RuntimeError, match="currently running"):
        module.apply(home=home, destination=home / "Backup-Gestionale-Tabacchi")
    assert plist.read_bytes() == previous_plist


def test_bootstrap_failure_restores_previous_configuration(sample_home, monkeypatch):
    home, source, archive, plist = sample_home
    previous_plist = plist.read_bytes()
    calls = []

    def fake_launchctl(*args, **kwargs):
        calls.append(args)
        if args[0] == "bootstrap" and calls.count(args) == 1:
            raise subprocess.CalledProcessError(5, ["launchctl"])
        return SimpleNamespace(returncode=0, stdout="state = waiting\n")

    monkeypatch.setattr(module, "launchctl", fake_launchctl)
    with pytest.raises(RuntimeError, match="restored previous configuration"):
        module.apply(home=home, destination=home / "Backup-Gestionale-Tabacchi")
    assert plist.read_bytes() == previous_plist
    assert (home / "Backup-Gestionale-Tabacchi" / archive.name).read_bytes() == archive.read_bytes()

"""Offline macOS backup automation tests; no access to Atlas, Keychain or Docker."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import sys
import types

import pytest


@pytest.fixture
def modules(monkeypatch):
    # Import the launchd layer independently of the existing backup pipeline;
    # integration with the real backup functions is exercised in its own tests.
    stub_backup = types.ModuleType("encrypted_backup")
    stub_backup.backup = lambda **kwargs: None
    stub_health = types.ModuleType("backup_health")
    stub_health.check_backup = lambda *args, **kwargs: {"backup_fresh": False}
    monkeypatch.setitem(sys.modules, "encrypted_backup", stub_backup)
    monkeypatch.setitem(sys.modules, "backup_health", stub_health)
    base = Path(__file__).resolve().parents[2] / "scripts"
    result = []
    for name in ("macos_backup_runner", "install_macos_backup_schedule"):
        spec = importlib.util.spec_from_file_location("isolated_" + name, base / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result.append(module)
    return result


def test_private_config_escapes_password_and_is_removed(modules, tmp_path):
    runner, _ = modules
    secret = 'abc:"#$\\\\\\nwith spaces and quote'
    root = tmp_path / "private"
    with runner.private_mongodump_config(secret, root) as config:
        assert config.stat().st_mode & 0o777 == 0o600
        contents = config.read_text()
        assert json.loads(contents.splitlines()[0][5:].strip()) == runner.URI
        assert json.loads(contents.splitlines()[1][10:].strip()) == secret
        location = config
    assert not location.exists()
    assert root.stat().st_mode & 0o777 == 0o700


def test_reject_world_readable_and_symlinked_output(modules, tmp_path):
    runner, _ = modules
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    with pytest.raises(RuntimeError, match="0700"):
        runner._require_private_directory(shared)
    link = tmp_path / "link"
    link.symlink_to(shared)
    with pytest.raises(RuntimeError, match="symlink"):
        runner._require_private_directory(link)


def test_keychain_password_never_in_cli(modules, monkeypatch):
    runner, _ = modules
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        return types.SimpleNamespace(returncode=0, stdout="private value\n", stderr="")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.read_keychain_password() == "private value"
    assert calls[0] == ["/usr/bin/security", "find-generic-password", "-a",
                        runner.ACCOUNT, "-s", runner.SERVICE, "-w"]


def test_successful_local_backup_and_no_deletion(modules, monkeypatch, tmp_path):
    runner, _ = modules
    root = tmp_path / "local"
    calls = []
    def fake_backup(**kwargs):
        calls.append(kwargs)
        dest = Path(kwargs["output_dir"])
        file = dest / "gestionale-test.archive.gz.gpg"
        file.write_bytes(b"ENCRYPTED_ARCHIVE_FIXTURE")
        data = {"filename": file.name, "sha256": runner.sha256(file),
                "bytes": file.stat().st_size, "status": "encrypted"}
        (dest / "last-success.json").write_text(json.dumps(data))
        return data
    monkeypatch.setattr(runner, "backup", fake_backup)
    monkeypatch.setattr(runner, "read_keychain_password", lambda: "not-a-real-secret")
    monkeypatch.setattr(runner, "check_backup", lambda *a, **kw: {"backup_fresh": False if kw.get('max_age_hours') == 12 else True})
    monkeypatch.setattr(runner.shutil, "which", lambda *a: "/fake/tool")
    result = runner.run_backup(output_dir=root / "output", temp_root=root / "temp",
                               lock_dir=root / "locks", usb_volume=None)
    assert result["status"] == "local_verified"
    assert result["usb_verified"] is False
    assert result["restore_certified"] is False
    assert calls[0]["db_name"] == "gestionale"
    assert calls[0]["recipient"] == runner.FINGERPRINT
    assert not list((root / "temp").glob("mongo-config-*"))
    assert (root / "output" / result["file"]).exists()


def test_recent_backup_is_skipped_before_keychain(modules, monkeypatch, tmp_path):
    runner, _ = modules
    monkeypatch.setattr(runner, "check_backup", lambda *a, **kw: {"backup_fresh": True})
    monkeypatch.setattr(runner, "read_keychain_password",
                        lambda: (_ for _ in ()).throw(AssertionError("keychain must not be touched")))
    result = runner.run_backup(output_dir=tmp_path / "out", temp_root=tmp_path / "temp",
                               lock_dir=tmp_path / "locks", usb_volume=None)
    assert result["backup_created"] is False


def test_usb_requires_mount_and_refuses_mismatch(modules, monkeypatch, tmp_path):
    runner, _ = modules
    archive = tmp_path / "x.gpg"
    archive.write_bytes(b"encrypted")
    record = {"bytes": 9, "sha256": runner.sha256(archive)}
    volume = tmp_path / "unmounted"
    volume.mkdir()
    with pytest.raises(RuntimeError, match="not mounted"):
        runner.mirror_to_removable_volume(archive, record, volume)
    monkeypatch.setattr(Path, "is_mount", lambda p: p == volume)
    target = runner.mirror_to_removable_volume(archive, record, volume)
    assert target.read_bytes() == b"encrypted"
    target.write_bytes(b"corrupted")
    with pytest.raises(RuntimeError, match="different contents"):
        runner.mirror_to_removable_volume(archive, record, volume)


def test_schedule_preview_and_plist_shape(modules, tmp_path):
    _, installer = modules
    python = tmp_path / "python3"
    runner = tmp_path / "backup.py"
    python.touch()
    runner.touch()
    agent = installer.launch_agent(python=python, runner=runner, logs=tmp_path,
                                   hour=3, minute=0, usb_volume=Path("/Volumes/MAG256"))
    assert agent["StartCalendarInterval"] == {"Hour": 3, "Minute": 0}
    assert agent["RunAtLoad"] is True
    assert agent["KeepAlive"] is False
    assert agent["ProgramArguments"][-2:] == ["--usb-volume", "/Volumes/MAG256"]
    assert plistlib.loads(plistlib.dumps(agent)) == agent
    with pytest.raises(ValueError):
        installer.launch_agent(python=python, runner=runner, logs=tmp_path, hour=24)


def test_no_production_access_without_explicit_flag(modules, capsys):
    runner, _ = modules
    with pytest.raises(SystemExit) as exc:
        runner.main(["--preflight"])
    assert exc.value.code == 2
    assert "--allow-production" in capsys.readouterr().err


def test_local_diagnostics_do_not_retrieve_or_expose_password(modules, monkeypatch, tmp_path):
    runner, _ = modules
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        if args[0] == "gpg":
            return types.SimpleNamespace(returncode=0, stdout=f"fpr:::::::::{runner.FINGERPRINT}:\n", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="SECRET_SHOULD_NOT_APPEAR", stderr="")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner.shutil, "which", lambda binary: "/bin/" + binary)
    root = tmp_path / "missing"
    data = runner.local_diagnostics(root / "output", root / "tmp", root / "lock")
    assert data["keychain_item_found"] is True
    assert data["gpg_public_key_found"] is True
    assert data["directories"]["backup"] == "not_created_yet"
    assert not root.exists()
    assert "SECRET_SHOULD_NOT_APPEAR" not in json.dumps(data)
    assert "-w" not in calls[-1]


def test_failure_displays_only_sanitized_stage(modules, monkeypatch, capsys):
    runner, _ = modules
    def bad_run(**kwargs):
        kwargs["on_stage"]("keychain_access")
        raise RuntimeError("CONTAINS_SENSITIVE_MONGODB_PASSWORD")
    monkeypatch.setattr(runner, "run_backup", bad_run)
    monkeypatch.setattr(runner, "notify_failure", lambda: None)
    assert runner.main(["--allow-production", "--force"]) == 2
    err = capsys.readouterr().err
    assert "ERROR [keychain_access]" in err
    assert "CONTAINS_SENSITIVE_MONGODB_PASSWORD" not in err

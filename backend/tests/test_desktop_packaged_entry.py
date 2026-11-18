"""No Mongo/network needed: packaged entry must fail closed."""
from pathlib import Path
from unittest.mock import patch

import pytest

from desktop_runtime import packaged_entry as entry


def test_preflight_requires_frontend_and_backend(tmp_path):
    with pytest.raises(entry.SidecarPreflightError, match="React"):
        entry.preflight_assets(tmp_path)
    build = tmp_path / "frontend" / "build"
    (build / "static").mkdir(parents=True)
    (build / "index.html").write_text("<!doctype html>")
    assert entry.preflight_assets(tmp_path) == {
        "assets": "ok", "backend_import": "ok", "production_writes": False,
    }


def test_self_test_has_no_side_effects(tmp_path, capsys):
    with patch.object(entry, "bundle_root", return_value=tmp_path), patch.object(
        entry, "preflight_assets", return_value={"assets": "ok"}
    ) as verify:
        assert entry.main(["--self-test"]) == 0
        verify.assert_called_once_with(tmp_path)
    assert "no network" in capsys.readouterr().out


def test_bad_args_never_start_backend():
    with patch.object(entry, "run_service") as serve:
        with pytest.raises(SystemExit):
            entry.main([])
        serve.assert_not_called()


def test_secure_mongo_requires_preprovisioned_data_dir(tmp_path):
    device = "2b5bda8c-7022-4de9-93c6-7898eb796087"
    with pytest.raises(entry.SidecarPreflightError, match="provisioned separately"):
        entry.prepare_existing_secure_mongo(tmp_path, device)


def test_run_service_fails_before_server_import_without_auth_setup(tmp_path, monkeypatch):
    from desktop_runtime import local_mongo
    monkeypatch.setattr(local_mongo, "app_data_dir", lambda: tmp_path)
    with patch.object(entry, "preflight_assets", return_value={}), patch.object(
        entry, "prepare_existing_secure_mongo", side_effect=entry.SidecarPreflightError("No secure DB")
    ), patch.object(entry, "prepare_http_auth") as http_auth:
        with pytest.raises(entry.SidecarPreflightError, match="No secure DB"):
            entry.run_service()
        http_auth.assert_not_called()

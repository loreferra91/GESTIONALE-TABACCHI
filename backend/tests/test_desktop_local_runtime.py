"""Non-destructive checks for desktop bootstrap; no real mongod needed."""
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from desktop_runtime.local_mongo import LocalMongoError, app_data_dir, loopback_port_available, check_transactions
from desktop_runtime.run_local import credentials, wait_api


def test_explicit_local_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GESTIONALE_DESKTOP_DATA_DIR", str(tmp_path))
    assert app_data_dir() == tmp_path.resolve()


def test_credentials_persist_across_restarts(tmp_path):
    user, first = credentials(tmp_path)
    assert user == "desktop"
    assert credentials(tmp_path) == (user, first)
    assert len(first) >= 32


def test_invalid_existing_credentials_fail_closed(tmp_path):
    (tmp_path / "auth.secret").write_text("short")
    with pytest.raises(LocalMongoError, match="Invalid desktop auth secret"):
        credentials(tmp_path)


def test_invalid_port_fails_closed():
    with pytest.raises(LocalMongoError):
        loopback_port_available(80)


def test_real_transaction_required():
    client = MagicMock()
    client.admin.command.return_value = {"setName": None}
    with pytest.raises(LocalMongoError, match="Transactions unavailable"):
        check_transactions(client, "gestionale_desktop")


def test_healthcheck_rejects_exited_process():
    process = MagicMock()
    process.poll.return_value = 5
    with pytest.raises(LocalMongoError, match="FastAPI stopped"):
        wait_api("http://127.0.0.1:1/api/", process, seconds=0.5)

"""Pure keyfile and fail-closed provisioning validations."""
import os
import stat
from unittest.mock import MagicMock

import pytest

from desktop_runtime.mongo_auth import (
    MongoSecurityError, create_or_validate_keyfile, authenticated_uri,
    bootstrap_first_users,
)


def test_secure_keyfile_created_once_and_retained(tmp_path):
    key = tmp_path / "mongo.key"
    create_or_validate_keyfile(key)
    first = key.read_bytes()
    assert 6 <= len(first.strip()) <= 1024
    assert create_or_validate_keyfile(key) == key
    assert key.read_bytes() == first
    if os.name != "nt":
        assert stat.S_IMODE(key.stat().st_mode) & 0o077 == 0


def test_rejects_symlink_and_insecure_keyfile(tmp_path):
    real = tmp_path / "actual.key"
    create_or_validate_keyfile(real)
    symlink = tmp_path / "link.key"
    symlink.symlink_to(real)
    with pytest.raises(MongoSecurityError, match="symlink"):
        create_or_validate_keyfile(symlink)
    if os.name != "nt":
        real.chmod(0o644)
        with pytest.raises(MongoSecurityError, match="readable"):
            create_or_validate_keyfile(real)


def test_invalid_auth_target_fails_closed():
    with pytest.raises(MongoSecurityError, match="Invalid"):
        authenticated_uri(27020, "production", "a"*40, "rsDesktopAuthCert")
    with pytest.raises(MongoSecurityError, match="short"):
        authenticated_uri(27020, "desktop_auth_cert_test", "short", "rsDesktopAuthCert")
    result = authenticated_uri(27020, "desktop_auth_cert_test",
                               "special!pass:word/aaaaaaaaaaaaaaaaaaaaa", "rsDesktopAuthCert")
    assert "special%21pass%3Aword%2F" in result or "special%21pass%3Aword%2F".lower() in result.lower()


def test_mismatched_member_port_refused_before_any_server_command():
    mongo = MagicMock()
    mongo.address = ("127.0.0.1", 27020)
    mongo.options.direct_connection = True
    with pytest.raises(MongoSecurityError, match="port does not match"):
        bootstrap_first_users(mongo, replica_set="rsDesktopAuthCert",
            database="desktop_auth_cert_test", admin_password="a"*40,
            app_password="b"*40, member_host="localhost:27019")
    mongo.admin.command.assert_not_called()

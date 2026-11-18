"""Secure preview policy: no implicit legacy migration or plaintext fallback."""
from uuid import uuid4
import os

import pytest

from desktop_runtime.mongo_auth import MongoSecurityError
from desktop_runtime.secure_runtime import (
    SERVICE, prepare_secure_profile, _native_store,
)


class FakeNativeStore:
    def __init__(self):
        self.values = {}
    def get_password(self, service, account):
        return self.values.get((service, account))
    def set_password(self, service, account, value):
        self.values[(service, account)] = value


def profile(root, device, store):
    return prepare_secure_profile(
        root, device, store=store, platform_id="darwin",
        backend_name="keyring.backends.macOS",
    )


def test_first_install_retains_secrets_and_keyfile(tmp_path):
    store = FakeNativeStore()
    identity = str(uuid4())
    path, admin, app, fresh = profile(tmp_path, identity, store)
    assert fresh and path.is_dir()
    assert len(admin) >= 32 and len(app) >= 32
    assert (tmp_path / "mongo-internal.key").is_file()
    assert profile(tmp_path, identity, store) == (path, admin, app, False)


def test_partial_installation_does_not_reset(tmp_path):
    store = FakeNativeStore()
    device = str(uuid4())
    store.set_password(SERVICE, f"{device}/admin", "a"*40)
    with pytest.raises(MongoSecurityError, match="Partial"):
        profile(tmp_path, device, store)
    assert not (tmp_path / "secure-mongo-data").exists()


def test_existing_insecure_database_is_never_migrated(tmp_path):
    (tmp_path / "mongo-data").mkdir()
    with pytest.raises(MongoSecurityError, match="Legacy"):
        profile(tmp_path, str(uuid4()), FakeNativeStore())


def test_keyring_backend_must_be_native():
    with pytest.raises(MongoSecurityError, match="Native"):
        _native_store(FakeNativeStore(), platform_id="linux",
                      backend_name="keyring.backends.fail")
    with pytest.raises(MongoSecurityError, match="Native"):
        _native_store(FakeNativeStore(), platform_id="darwin",
                      backend_name="keyring.backends.chainer")


def test_missing_keyfile_fails_closed(tmp_path):
    store = FakeNativeStore()
    device = str(uuid4())
    profile(tmp_path, device, store)
    (tmp_path / "mongo-internal.key").unlink()
    with pytest.raises(MongoSecurityError, match="Incomplete"):
        profile(tmp_path, device, store)


def test_invalid_device_id_rejected(tmp_path):
    with pytest.raises((ValueError, MongoSecurityError)):
        profile(tmp_path, "invalid-device-id", FakeNativeStore())

"""Prevent an accidental staging deployment from targeting the wrong MongoDB cluster."""
import pytest
from backend import server


def _set_staging(monkeypatch, db_name="gestionale_staging", url=None):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("APP_USERNAME", "test-operator")
    monkeypatch.setenv("APP_PASSWORD", "test-password")
    monkeypatch.setenv("CORS_ORIGINS", "https://gestionale-tabacchi-staging.onrender.com")
    monkeypatch.setenv("DB_NAME", db_name)
    monkeypatch.setenv(
        "MONGO_URL",
        url or "mongodb+srv://staging_user:synthetic_pass@gestionale-staging.example.mongodb.net/gestionale_staging",
    )
    monkeypatch.setenv("AGENT_INTEGRATION_ENABLED", "false")
    monkeypatch.setenv("STAGING_MONGO_ROLE_MODE", "legacy")


def test_staging_accepts_known_database_and_dedicated_cluster(monkeypatch):
    _set_staging(monkeypatch)
    server._validate_runtime_security()


@pytest.mark.parametrize("database", ["gestionale", "other", "", "gestionale_dev"])
def test_staging_refuses_unexpected_database(monkeypatch, database):
    _set_staging(monkeypatch, db_name=database)
    with pytest.raises(RuntimeError, match="DB_NAME"):
        server._validate_runtime_security()


@pytest.mark.parametrize("url", [
    "mongodb+srv://other_user:synthetic_pass@production.example.mongodb.net/gestionale",
    "mongodb://staging_user:synthetic_pass@localhost:27017/gestionale_staging",
    "mongodb+srv://staging_user:synthetic_pass@other-staging.example.mongodb.net/gestionale_staging",
    "mongodb+srv://staging_user@gestionale-staging.example.mongodb.net/gestionale_staging",
    "malformed",
])
def test_staging_refuses_unknown_cluster_or_auth(monkeypatch, url):
    _set_staging(monkeypatch, url=url)
    with pytest.raises(RuntimeError, match="cluster MongoDB"):
        server._validate_runtime_security()


def test_prod_does_not_inherit_staging_only_guard(monkeypatch):
    _set_staging(monkeypatch, db_name="gestionale")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb+srv://other_user:synthetic_pass@production.example.mongodb.net/gestionale")
    server._validate_runtime_security()


def test_staging_active_scoped_uri_is_valid_even_without_legacy_secret(monkeypatch):
    _set_staging(monkeypatch)
    monkeypatch.delenv("MONGO_URL")
    monkeypatch.setenv("STAGING_MONGO_ROLE_MODE", "active")
    monkeypatch.setenv(
        "STAGING_SCOPED_MONGO_URL",
        "mongodb+srv://scoped:synthetic@gestionale-staging.example.mongodb.net/gestionale_staging",
    )
    server._validate_runtime_security()


def test_staging_legacy_without_mongo_uri_still_fails_closed(monkeypatch):
    _set_staging(monkeypatch)
    monkeypatch.delenv("MONGO_URL")
    with pytest.raises(RuntimeError, match="cluster MongoDB"):
        server._validate_runtime_security()


def test_staging_active_rejects_wrong_cluster_even_if_legacy_is_valid(monkeypatch):
    _set_staging(monkeypatch)
    monkeypatch.setenv("STAGING_MONGO_ROLE_MODE", "active")
    monkeypatch.setenv(
        "STAGING_SCOPED_MONGO_URL",
        "mongodb+srv://scoped:synthetic@production.example.mongodb.net/gestionale",
    )
    with pytest.raises(RuntimeError):
        server._validate_runtime_security()

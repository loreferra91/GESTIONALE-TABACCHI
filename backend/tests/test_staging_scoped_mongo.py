"""Staging credential cutover is reversible and never prints secrets."""
import asyncio
from types import SimpleNamespace

import pytest

from backend.staging_scoped_mongo import (
    choose_staging_connection,
    verify_scoped_staging_roles,
    verify_scoped_staging_connection,
)


OLD = "mongodb+srv://legacy:protected@gestionale-staging.example.mongodb.net/gestionale_staging"
NEW = "mongodb+srv://scope:protected@gestionale-staging.example.mongodb.net/gestionale_staging"


def test_staging_scoped_selection_preserves_original_rollback():
    assert choose_staging_connection("staging", "legacy", OLD, None) == (OLD, "legacy")
    assert choose_staging_connection("staging", "probe", OLD, NEW) == (OLD, "probe")
    assert choose_staging_connection("staging", "active", OLD, NEW) == (NEW, "active")
    assert choose_staging_connection("production", "active", OLD, NEW) == (OLD, "legacy")


@pytest.mark.parametrize("mode,uri", [
    ("staged", NEW),
    ("active", None),
    ("probe", "mongodb://scope:secret@localhost:27017"),
    ("active", "mongodb+srv://scope:secret@production.example.mongodb.net/test"),
    ("active", "mongodb+srv://scope@gestionale-staging.example.mongodb.net/test"),
])
def test_staging_scoped_mode_rejects_unrecognized_target(mode, uri):
    with pytest.raises(RuntimeError) as err:
        choose_staging_connection("staging", mode, OLD, uri)
    assert "protected" not in str(err.value)
    assert "secret" not in str(err.value)


@pytest.mark.parametrize("roles", [
    [{"role": "readWriteAnyDatabase", "db": "admin"}],
    [{"role": "readWrite", "db": "gestionale"}],
    [{"role": "read", "db": "gestionale_staging"}],
    [{"role": "readWrite", "db": "gestionale_staging"},
     {"role": "read", "db": "admin"}],
    [],
])
def test_scoped_role_check_rejects_broad_or_incomplete_grants(roles):
    with pytest.raises(RuntimeError, match="exact database-only"):
        verify_scoped_staging_roles({
            "authInfo": {"authenticatedUserRoles": roles},
        })


def test_scoped_role_check_never_returns_username():
    result = verify_scoped_staging_roles({
        "authInfo": {
            "authenticatedUsers": [{"user": "private-user", "db": "admin"}],
            "authenticatedUserRoles": [
                {"role": "readWrite", "db": "gestionale_staging"},
            ],
        },
    })
    assert result == {"verified_role": "readWrite", "database": "gestionale_staging"}


def test_scoped_probe_reads_only_and_does_not_modify_other_collections():
    events = []
    class ReadonlyProducts:
        async def find_one(self, query, projection):
            events.append(("find_one", query, projection))
            return {"_id": "synthetic"}
    class ReadonlyDatabase:
        prodotti = ReadonlyProducts()
        async def command(self, name):
            events.append(("db_command", name))
            return {"ok": 1}
    class Admin:
        async def command(self, name):
            events.append(("auth_command", name))
            return {"authInfo": {"authenticatedUserRoles": [
                {"role": "readWrite", "db": "gestionale_staging"},
            ]}}
    class Client:
        admin = Admin()
        def __getitem__(self, database):
            assert database == "gestionale_staging"
            return ReadonlyDatabase()
    result = asyncio.run(verify_scoped_staging_connection(NEW, client=Client()))
    assert result["read_only_probe_passed"] is True
    assert events == [
        ("auth_command", "connectionStatus"),
        ("db_command", "ping"),
        ("find_one", {}, {"_id": 1}),
    ]


def test_staging_scoped_active_mode_does_not_need_legacy_uri():
    assert choose_staging_connection("staging", "active", "", NEW) == (NEW, "active")
    # Mode selection outside staging must never silently accept the staging URL.
    assert choose_staging_connection("production", "active", "", NEW) == ("", "legacy")

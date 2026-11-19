"""Staging startup must never repair or seed live records by accident."""
import asyncio
from types import SimpleNamespace

import pytest
from mongomock_motor import AsyncMongoMockClient

from backend import server
from backend.index_migrations import MIGRATION_ID
from backend.migration_preflight import KNOWN_MIGRATION_IDS
from backend.staging_readonly_startup import (
    REQUIRED_UNIQUE_INDEXES,
    verify_readonly_staging_startup,
)


async def _ready_fixture():
    db = AsyncMongoMockClient()["gestionale_cert_readonly_startup"]
    unique_keys = {
        ("prodotti", "codice"),
        ("vending", "colonna"),
        ("parametri", "nome"),
        *REQUIRED_UNIQUE_INDEXES,
    }
    for name, key in sorted(unique_keys):
        await db[name].create_index(key, unique=True)
    await db.prodotti.insert_one({
        "id": "CERT-P1", "codice": "CERT-P1",
        "giacenza_negozio": 10, "giacenza_vending": 0,
    })
    await db.parametri.insert_many(
        [{"nome": key, "valore": 1} for key in server.DEFAULT_PARAMS]
    )
    await db.vending.insert_one({
        "id": "CERT-COL", "colonna": "H01", "codice": "CERT-P1",
        "giacenza": 0,
    })
    await db.app_migrations.insert_many(
        [{"id": key, **({"completed": True} if key == MIGRATION_ID else {})}
         for key in KNOWN_MIGRATION_IDS]
    )
    return db


def test_staging_readonly_preflight_has_zero_database_writes():
    async def scenario():
        db = await _ready_fixture()
        names_before = await db.list_collection_names()
        documents = {name: await db[name].find({}).to_list(None) for name in names_before}
        indexes = {name: await db[name].index_information() for name in names_before}
        result = await verify_readonly_staging_startup(db, server.DEFAULT_PARAMS)
        assert result["mode"] == "read_only"
        assert result["startup_database_writes"] == 0
        assert result["verified_operation_indexes"] == len(REQUIRED_UNIQUE_INDEXES)
        assert result["verified_default_parameters"] == len(server.DEFAULT_PARAMS)
        assert await db.list_collection_names() == names_before
        for name in names_before:
            assert await db[name].find({}).to_list(None) == documents[name]
            assert await db[name].index_information() == indexes[name]

    asyncio.run(scenario())


def test_staging_readonly_preflight_blocks_missing_idempotency_index():
    async def scenario():
        db = await _ready_fixture()
        await db.cassa_rapida_batches.drop_index("idempotency_key_1")
        with pytest.raises(RuntimeError, match="cassa_rapida_batches.idempotency_key"):
            await verify_readonly_staging_startup(db, server.DEFAULT_PARAMS)
    asyncio.run(scenario())


def test_staging_readonly_preflight_rejects_missing_parameter_and_legacy_column():
    async def scenario():
        db = await _ready_fixture()
        await db.parametri.delete_one({"nome": "LOTTO_SIGARETTE"})
        await db.vending.insert_one({
            "id": "LEGACY", "colonna": "H2", "codice": "CERT-P1"
        })
        with pytest.raises(RuntimeError) as err:
            await verify_readonly_staging_startup(db, server.DEFAULT_PARAMS)
        assert "LOTTO_SIGARETTE" in str(err.value)
        assert "H01-H03" in str(err.value)
    asyncio.run(scenario())


def test_staging_readonly_preflight_blocks_incomplete_unique_index_migration():
    async def scenario():
        db = await _ready_fixture()
        await db.app_migrations.update_one(
            {"id": MIGRATION_ID}, {"$set": {"completed": False}}
        )
        with pytest.raises(RuntimeError, match="migrazione indici non completata"):
            await verify_readonly_staging_startup(db, server.DEFAULT_PARAMS)
    asyncio.run(scenario())


def test_staging_readonly_mode_skips_legacy_on_start(monkeypatch):
    db = AsyncMongoMockClient()["staging_readonly_lifespan"]
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("STAGING_STARTUP_MODE", "read_only")
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "client", SimpleNamespace(close=lambda: None))
    calls = []

    def validate():
        calls.append("security")

    async def verify_transactions():
        calls.append("transactions")
        return {"transactions": True}

    async def readiness(_db, _params):
        calls.append("readiness")
        return {"startup_database_writes": 0}

    async def forbidden_start():
        raise AssertionError("Legacy startup must not run in read-only mode")

    monkeypatch.setattr(server, "_validate_runtime_security", validate)
    monkeypatch.setattr(server, "_verify_transaction_capability", verify_transactions)
    monkeypatch.setattr(server, "verify_readonly_staging_startup", readiness)
    monkeypatch.setattr(server, "on_start", forbidden_start)

    async def scenario():
        async with server.lifespan(server.app):
            calls.append("ready")
    asyncio.run(scenario())
    assert calls == ["security", "transactions", "readiness", "ready"]


def test_failed_staging_initializer_is_not_silently_ignored(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")

    async def fail_seed():
        raise RuntimeError("Synthetic failure before writes")

    monkeypatch.setattr(server, "seed_if_empty", fail_seed)
    with pytest.raises(RuntimeError, match="Synthetic failure"):
        asyncio.run(server.on_start())


@pytest.mark.parametrize("mode,expected", [
    ("read_only", "read_only"), ("legacy", "legacy"),
])
def test_staging_startup_mode_selection(monkeypatch, mode, expected):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("STAGING_STARTUP_MODE", mode)
    assert server._staging_startup_mode() == expected


def test_staging_invalid_mode_fails_closed_but_production_is_unchanged(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("STAGING_STARTUP_MODE", "typo")
    with pytest.raises(RuntimeError, match="STAGING_STARTUP_MODE"):
        server._staging_startup_mode()
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert server._staging_startup_mode() == "legacy"

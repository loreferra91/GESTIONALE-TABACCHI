"""Explicit migrations remain offline, versioned and fail-closed."""
import asyncio

import pytest
from mongomock_motor import AsyncMongoMockClient

from backend.auto_order_migrations import AUTO_ORDER_2_SCHEMA_ID
from backend.staging_migration_runner import (
    inspect_migration_run, validate_restore_attestation, apply_additive_migration,
)

TEST_ATTESTATION = {
    "source_database": "gestionale_staging",
    "source_kind": "operational_staging_backup",
    "restore_target": "restored_independent_test",
    "independent_offcluster_archive": True,
    "restore_verified": True,
    "indexes_verified": True,
    "business_documents_verified": True,
    "archive_sha256": "a" * 64,
}


async def _staging_db():
    database = AsyncMongoMockClient()["gestionale_staging"]
    for collection, field in (
        ("prodotti", "codice"), ("vending", "colonna"), ("parametri", "nome")
    ):
        await database[collection].create_index(field, unique=True)
    await database.prodotti.insert_one({
        "id": "TEST-P", "codice": "TEST-P", "giacenza_negozio": 10,
    })
    return database


def test_migration_plan_reports_unversioned_writers_without_mutations():
    async def scenario():
        db = await _staging_db()
        await db.app_migrations.insert_one({"id": AUTO_ORDER_2_SCHEMA_ID})
        before = {
            col: await db[col].find({}).to_list(None)
            for col in await db.list_collection_names()
        }
        report = await inspect_migration_run(db)
        assert report["mode"] == "read_only_plan"
        assert report["automatic_repairs_allowed"] is False
        assert report["schema_candidates"][0]["status"] == "already_recorded"
        assert "reconcile_vending_cash_state" in report["legacy_data_repairs_not_executed"]
        assert "repair_smart_venue_auto_order_2026_10_12" in report["legacy_data_repairs_not_executed"]
        for col, docs in before.items():
            assert await db[col].find({}).to_list(None) == docs
    asyncio.run(scenario())


def test_migration_plan_blocks_while_staging_restore_incomplete():
    async def scenario():
        db = await _staging_db()
        await db.backup_restore_control.insert_one({
            "_id": "external-file-restore", "phase": "FAILED"
        })
        report = await inspect_migration_run(db)
        assert report["blockers"] == ["staging_restore_maintenance_active"]
        with pytest.raises(RuntimeError, match="blocked"):
            await apply_additive_migration(db, AUTO_ORDER_2_SCHEMA_ID, TEST_ATTESTATION)
    asyncio.run(scenario())


@pytest.mark.parametrize("broken", [
    {},
    {"source_kind": "synthetic_fixture"},
    {"source_database": "gestionale"},
    {"restore_target": "gestionale_staging"},
    {"restore_verified": False},
    {"indexes_verified": False},
    {"business_documents_verified": False},
    {"archive_sha256": "garbage"},
    {"independent_offcluster_archive": False},
])
def test_attestation_validation_fail_closed(broken):
    evidence = {**TEST_ATTESTATION, **broken}
    if not broken:
        evidence = {}
    with pytest.raises(RuntimeError, match="Independent staging backup"):
        validate_restore_attestation(evidence, "gestionale_staging")


def test_manual_only_migration_must_never_run_automatically():
    async def scenario():
        db = await _staging_db()
        with pytest.raises(RuntimeError, match="manual procedure"):
            await apply_additive_migration(db, "vending-column-zero-padding-h01-h03-v1", TEST_ATTESTATION)
        assert await db.app_migrations.count_documents({}) == 0
    asyncio.run(scenario())


def test_additive_schema_dry_run_and_explicit_apply_are_distinct():
    async def scenario():
        db = await _staging_db()
        plan = await inspect_migration_run(db)
        assert plan["schema_candidates"][0]["status"] == "requires_explicit_apply"
        assert await db.app_migrations.count_documents({}) == 0
        with pytest.raises(RuntimeError, match="Independent staging backup"):
            await apply_additive_migration(db, AUTO_ORDER_2_SCHEMA_ID, {})
        assert await db.app_migrations.count_documents({}) == 0
        applied = await apply_additive_migration(db, AUTO_ORDER_2_SCHEMA_ID, TEST_ATTESTATION)
        assert applied["status"] == "applied"
        assert await db.app_migrations.count_documents({"id": AUTO_ORDER_2_SCHEMA_ID}) == 1
        repeated = await apply_additive_migration(db, AUTO_ORDER_2_SCHEMA_ID, {})
        assert repeated["status"] == "already_recorded"
        assert repeated["database_writes"] == 0
    asyncio.run(scenario())

"""Explicit staging schema migration entrypoint; no startup migrations.

Only additive Auto-Order schema indexes can be applied by this runner.
Historical data repairs, vending renames, unique-index replacements and
opening balances remain manual-only until independently backed up and
certified with a separate reversible migration and approval.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient

if __package__:
    from .auto_order_migrations import AUTO_ORDER_2_SCHEMA_ID, migrate_auto_order_2_schema
    from .migration_preflight import (
        inspect_startup_migrations, validate_staging_cli_target,
        UNVERSIONED_STARTUP_WRITES,
    )
    from .staging_scoped_mongo import verify_scoped_staging_connection
else:
    from auto_order_migrations import AUTO_ORDER_2_SCHEMA_ID, migrate_auto_order_2_schema
    from migration_preflight import (
        inspect_startup_migrations, validate_staging_cli_target,
        UNVERSIONED_STARTUP_WRITES,
    )
    from staging_scoped_mongo import verify_scoped_staging_connection


SCHEMA_MIGRATIONS = {AUTO_ORDER_2_SCHEMA_ID}
MANUAL_ONLY = {
    "unique-business-keys-v1": "Index replacement may drop an existing index",
    "vending-column-zero-padding-h01-h03-v1": "Vending identifiers are operational data",
}
RESTORE_PHASES_BLOCKED = {"PREPARING", "RESTORING", "ROLLING_BACK", "FAILED"}


async def inspect_migration_run(database):
    preflight = await inspect_startup_migrations(database)
    control = await database.backup_restore_control.find_one(
        {"_id": "external-file-restore"}, {"phase": 1}
    )
    restore_phase = str((control or {}).get("phase") or "").upper()
    recorded = {
        item["id"]: bool(item["recorded"])
        for item in preflight["migration_records"]
    }
    candidates = [
        {
            "id": AUTO_ORDER_2_SCHEMA_ID,
            "status": "already_recorded" if recorded.get(AUTO_ORDER_2_SCHEMA_ID) else "requires_explicit_apply",
        }
    ]
    blockers = [
        "staging_restore_maintenance_active"
    ] if restore_phase in RESTORE_PHASES_BLOCKED else []
    return {
        "mode": "read_only_plan",
        "startup_writes_disabled_in_read_only_mode": True,
        "automatic_repairs_allowed": False,
        "safe_to_apply_without_offcluster_restore_evidence": False,
        "schema_candidates": candidates,
        "manual_only_migrations": sorted(MANUAL_ONLY),
        "legacy_data_repairs_not_executed": list(UNVERSIONED_STARTUP_WRITES),
        "blockers": blockers,
        "preflight_has_known_blockers": preflight["has_known_blockers"],
    }


def validate_restore_attestation(value, expected_database):
    """Require independent restore evidence; never accept a synthetic fixture."""
    if not isinstance(value, dict) or (
        value.get("source_database") != expected_database
        or value.get("source_kind") != "operational_staging_backup"
        or value.get("restore_target") == expected_database
        or not value.get("restore_target")
        or value.get("independent_offcluster_archive") is not True
        or value.get("restore_verified") is not True
        or value.get("indexes_verified") is not True
        or value.get("business_documents_verified") is not True
        or len(str(value.get("archive_sha256") or "")) != 64
        or any(c not in "0123456789abcdefABCDEF" for c in str(value.get("archive_sha256") or ""))
    ):
        raise RuntimeError("Independent staging backup and restore attestation required")
    # Evidence is an operator assertion, not cryptographic proof. The operator
    # must independently verify the archive and restore target before --apply.


async def apply_additive_migration(database, migration_id, attestation):
    plan = await inspect_migration_run(database)
    if plan["blockers"] or plan["preflight_has_known_blockers"]:
        raise RuntimeError("Staging migration blocked by restore or legacy preflight findings")
    if migration_id != AUTO_ORDER_2_SCHEMA_ID:
        raise RuntimeError("Migration requires a separately reviewed manual procedure")
    candidate = plan["schema_candidates"][0]
    if candidate["status"] == "already_recorded":
        return {"status": "already_recorded", "id": migration_id, "database_writes": 0}
    validate_restore_attestation(attestation, "gestionale_staging")
    await migrate_auto_order_2_schema(database)
    return {"status": "applied", "id": migration_id, "database_writes": "additive_indexes_and_marker"}


async def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--apply", choices=sorted(SCHEMA_MIGRATIONS))
    parser.add_argument("--confirm", choices=("APPLY_STAGING_MIGRATION",))
    parser.add_argument("--restore-attestation", type=Path)
    args = parser.parse_args()
    if args.plan == bool(args.apply):
        parser.error("Choose exactly one of --plan or --apply")
    # The historical broad-privilege MONGO_URL is NEVER used by this runner.
    url = os.environ.get("STAGING_SCOPED_MONGO_URL") or ""
    db_name = os.environ.get("DB_NAME") or ""
    # Require staging-scoped credentials independently of the webapp mode.
    validate_staging_cli_target(
        str(os.environ.get("ENVIRONMENT") or "").strip().lower(),
        db_name, url,
    )
    if args.apply and (args.confirm != "APPLY_STAGING_MIGRATION"
                       or not args.restore_attestation):
        parser.error("Explicit confirmation and independent restore attestation required")
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=6000)
    try:
        await verify_scoped_staging_connection(url, client=client)
        database = client[db_name]
        if args.plan:
            result = await inspect_migration_run(database)
        else:
            attestation = json.loads(args.restore_attestation.read_text(encoding="utf-8"))
            result = await apply_additive_migration(database, args.apply, attestation)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(_main())

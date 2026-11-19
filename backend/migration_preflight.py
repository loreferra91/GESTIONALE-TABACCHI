"""Read-only staging preflight for startup migrations and unique business indexes.

Never modifies indexes, inventory, orders, aliases or migration records.
This is evidence for a later, separately approved versioned migration plan.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from urllib.parse import urlsplit

from motor.motor_asyncio import AsyncIOMotorClient

from .index_migrations import UNIQUE_BUSINESS_INDEXES, MIGRATION_ID
from .auto_order_migrations import AUTO_ORDER_2_SCHEMA_ID
from .vending_column_migrations import (
    VENDING_COLUMN_ZERO_PADDING,
    VENDING_COLUMN_ZERO_PADDING_MIGRATION_ID,
)

KNOWN_MIGRATION_IDS = (
    MIGRATION_ID,
    AUTO_ORDER_2_SCHEMA_ID,
    VENDING_COLUMN_ZERO_PADDING_MIGRATION_ID,
)

# These historical operations currently execute from server.on_start().
# Preflight reports their presence; it does not claim that they are safe,
# idempotent, or complete for a particular dataset.
UNVERSIONED_STARTUP_WRITES = (
    "seed_if_empty",
    "insert_missing_default_parameters",
    "ensure_product_ids",
    "reconcile_adm_product_aliases",
    "restore_canonical_product_snapshots",
    "reconcile_all_vending_stock",
    "reconcile_vending_cash_state",
    "repair_smart_venue_auto_order_2026_10_12",
)

# These have migration markers or index definitions; never confuse them
# with a proven safe-to-auto-run migration on an arbitrary backup.
VERSIONED_STARTUP_OPERATIONS = (
    "migrate_unique_business_indexes",
    "migrate_auto_order_2_schema",
    "migrate_vending_column_zero_padding",
)


async def inspect_startup_migrations(database) -> dict:
    index_findings = []
    blockers = []
    for collection_name, field in UNIQUE_BUSINESS_INDEXES:
        collection = database[collection_name]
        indexes = await collection.index_information()
        already_unique = any(
            bool(info.get("unique"))
            and list(info.get("key") or []) == [(field, 1)]
            for info in indexes.values()
        )
        if already_unique:
            status = "unique_index_present"
            duplicate_values = []
        else:
            duplicate_rows = await collection.aggregate([
                {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
                {"$match": {"count": {"$gt": 1}}},
                {"$limit": 10},
            ]).to_list(10)
            duplicate_values = [str(row.get("_id")) for row in duplicate_rows]
            status = "duplicate_keys" if duplicate_values else "unique_index_missing"
            if duplicate_values:
                blockers.append(f"{collection_name}.{field}: duplicati presenti")
        index_findings.append({
            "collection": collection_name,
            "field": field,
            "status": status,
            "duplicate_examples": duplicate_values,
        })

    names = list(VENDING_COLUMN_ZERO_PADDING) + list(VENDING_COLUMN_ZERO_PADDING.values())
    current = {
        str(row.get("colonna") or "").strip()
        async for row in database.vending.find(
            {"colonna": {"$in": names}}, {"_id": 0, "colonna": 1},
        )
    }
    pending = []
    collisions = []
    for old, new in VENDING_COLUMN_ZERO_PADDING.items():
        if old in current and new in current:
            collisions.append({"from": old, "to": new})
            blockers.append(f"vending: conflitto colonne {old}/{new}")
        elif old in current:
            pending.append({"from": old, "to": new})

    existing = {
        str(row.get("id")): row
        async for row in database.app_migrations.find(
            {"id": {"$in": list(KNOWN_MIGRATION_IDS)}},
            {"_id": 0, "id": 1, "completed": 1},
        )
    }
    migration_status = [
        {
            "id": migration_id,
            "recorded": migration_id in existing,
            "completed": existing.get(migration_id, {}).get("completed"),
        }
        for migration_id in KNOWN_MIGRATION_IDS
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only",
        "safe_to_run_automatic_migration": False,
        "has_known_blockers": bool(blockers),
        "blockers": blockers,
        "business_indexes": index_findings,
        "vending_column_zero_padding": {
            "pending": pending, "collisions": collisions,
        },
        "migration_records": migration_status,
        "unversioned_startup_writes_require_review": list(UNVERSIONED_STARTUP_WRITES),
        "versioned_startup_operations_require_review": list(VERSIONED_STARTUP_OPERATIONS),
        "limitations": [
            "No values, counts, stocks or indexes are modified by this scan.",
            "An index may become invalid between this scan and a later migration.",
            "The scan does not certify legacy migrations, startup writes or business correctness.",
            "Nonunique business keys and vending collisions block automatic correction; do not synthesize IDs or stock.",
        ],
    }


def validate_staging_cli_target(environment, db_name, mongo_url):
    if environment != "staging" or db_name != "gestionale_staging":
        raise ValueError("Only the dedicated staging database may be scanned")
    try:
        parsed = urlsplit(mongo_url)
    except ValueError as exc:
        raise ValueError("Malformed MongoDB URI") from exc
    host = str(parsed.hostname or "").lower()
    if parsed.scheme != "mongodb+srv" or not (
        host.startswith("gestionale-staging.") and host.endswith(".mongodb.net")
    ):
        raise ValueError("Dedicated staging cluster required")


async def _main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--output", choices=("json",), default="json")
    args.parse_args()
    url = os.environ.get("MONGO_URL") or ""
    db_name = os.environ.get("DB_NAME") or ""
    validate_staging_cli_target(
        (os.environ.get("ENVIRONMENT") or "").strip().lower(), db_name, url,
    )
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=6000)
    try:
        result = await inspect_startup_migrations(client[db_name])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(_main())

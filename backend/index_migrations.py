"""Safe, idempotent migrations for business-key indexes."""
from datetime import datetime, timezone
import logging

from pymongo.errors import PyMongoError


UNIQUE_BUSINESS_INDEXES = (
    ("prodotti", "codice"),
    ("vending", "colonna"),
    ("parametri", "nome"),
)
MIGRATION_ID = "unique-business-keys-v1"


async def _duplicate_values(collection, field):
    pipeline = [
        {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
        {"$limit": 10},
    ]
    return await collection.aggregate(pipeline).to_list(10)


async def migrate_unique_business_indexes(database):
    """Replace legacy indexes only after proving the live collection is clean.

    A dirty collection remains available with its non-unique index and the
    migration reports what must be repaired, avoiding a deployment outage.
    """
    results = []
    for collection_name, field in UNIQUE_BUSINESS_INDEXES:
        collection = database[collection_name]
        indexes = await collection.index_information()
        matching = [
            (name, info)
            for name, info in indexes.items()
            if list(info.get("key") or []) == [(field, 1)]
        ]
        if any(info.get("unique") for _, info in matching):
            results.append({"collection": collection_name, "field": field, "status": "already_unique"})
            continue

        duplicates = await _duplicate_values(collection, field)
        if duplicates:
            if not matching:
                await collection.create_index(field)
            values = [row.get("_id") for row in duplicates]
            logging.error("Unique-index migration skipped for %s.%s; duplicates=%r", collection_name, field, values)
            results.append({
                "collection": collection_name,
                "field": field,
                "status": "duplicates",
                "values": values,
            })
            continue

        old_names = [name for name, _ in matching]
        try:
            for name in old_names:
                await collection.drop_index(name)
            await collection.create_index(field, unique=True, name=f"uniq_{collection_name}_{field}")
        except PyMongoError:
            # Restore query performance if replacement failed after the drop.
            current = await collection.index_information()
            if not any(list(info.get("key") or []) == [(field, 1)] for info in current.values()):
                await collection.create_index(field)
            raise
        results.append({"collection": collection_name, "field": field, "status": "migrated"})

    completed = all(result["status"] != "duplicates" for result in results)
    await database.app_migrations.update_one(
        {"id": MIGRATION_ID},
        {"$set": {
            "id": MIGRATION_ID,
            "completed": completed,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "results": results,
        }},
        upsert=True,
    )
    return results

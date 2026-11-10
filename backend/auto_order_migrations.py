"""Additive, versioned Auto-Order 2 indexes. No historical quantity backfill."""
from datetime import datetime, timezone

AUTO_ORDER_2_SCHEMA_ID = "auto-order-2-schema-v1"


async def migrate_auto_order_2_schema(db):
    """Safe to rerun at startup; legacy CREATO/orders remain untouched."""
    await db.ordini_fornitore.create_index(
        [("stato", 1), ("status", 1), ("data", -1)],
    )
    await db.ordini_fornitore_righe.create_index(
        [("codice", 1), ("stato", 1), ("data_consegna_prevista", 1)],
    )
    await db.app_migrations.update_one(
        {"id": AUTO_ORDER_2_SCHEMA_ID},
        {"$setOnInsert": {
            "id": AUTO_ORDER_2_SCHEMA_ID,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "type": "additive-indexes-and-lazy-legacy-read",
        }},
        upsert=True,
    )

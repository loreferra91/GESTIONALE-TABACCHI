"""Idempotent data migrations for legacy vending column identifiers."""

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict


VENDING_COLUMN_ZERO_PADDING_MIGRATION_ID = "vending-column-zero-padding-h01-h03-v1"
VENDING_COLUMN_ZERO_PADDING = {
    "H1": "H01",
    "H2": "H02",
    "H3": "H03",
}


async def migrate_vending_column_zero_padding(
    database,
    create_backup_snapshot: Callable[..., Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Rename H1-H3 to the zero-padded identifiers emitted by the vending CSV."""
    existing_migration = await database.app_migrations.find_one(
        {"id": VENDING_COLUMN_ZERO_PADDING_MIGRATION_ID}, {"_id": 0}
    )
    if existing_migration:
        return {"gia_applicata": True, **existing_migration}

    names = list(VENDING_COLUMN_ZERO_PADDING) + list(VENDING_COLUMN_ZERO_PADDING.values())
    columns = await database.vending.find(
        {"colonna": {"$in": names}}, {"_id": 0, "id": 1, "colonna": 1}
    ).to_list(len(names))
    by_name = {str(column.get("colonna") or ""): column for column in columns}

    conflicts = [
        target
        for source, target in VENDING_COLUMN_ZERO_PADDING.items()
        if source in by_name and target in by_name
    ]
    if conflicts:
        raise RuntimeError(
            "Rinomina colonne Vending annullata: destinazioni già presenti: "
            + ", ".join(sorted(conflicts))
        )

    pending = [
        (source, target, by_name[source])
        for source, target in VENDING_COLUMN_ZERO_PADDING.items()
        if source in by_name
    ]
    backup_id = None
    if pending:
        backup = await create_backup_snapshot(
            "Prima della rinomina colonne Vending H01-H03",
            "migrazione-colonne-vending-zero-padding",
            ["vending"],
            metadata={
                "rinomine": {
                    source: target for source, target, _column in pending
                }
            },
        )
        backup_id = backup["id"]

    now = datetime.now(timezone.utc).isoformat()
    renamed = []
    for source, target, column in pending:
        result = await database.vending.update_one(
            {"id": column["id"], "colonna": source},
            {"$set": {
                "colonna": target,
                "colonna_aggiornata_il": now,
                "colonna_sorgente": "MIGRAZIONE_ZERO_PADDING",
            }},
        )
        if result.modified_count != 1:
            raise RuntimeError(
                f"Rinomina colonna Vending non applicata: {source} -> {target}"
            )
        renamed.append({"da": source, "a": target, "id": column["id"]})

    migration = {
        "id": VENDING_COLUMN_ZERO_PADDING_MIGRATION_ID,
        "created_at": now,
        "backup_id": backup_id,
        "rinominate": renamed,
    }
    await database.app_migrations.insert_one(migration.copy())
    return {"gia_applicata": False, **migration}

import asyncio
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient

from backend.vending_column_migrations import (
    VENDING_COLUMN_ZERO_PADDING_MIGRATION_ID,
    migrate_vending_column_zero_padding,
)


def test_zero_padding_migration_renames_columns_without_touching_stock_or_products():
    database = AsyncMongoMockClient()["vending_column_migration"]
    backup = AsyncMock(return_value={"id": "backup-vending-columns"})

    async def scenario():
        original = [
            {"id": "h1", "colonna": "H1", "codice": "AMMS2602", "giacenza": 5},
            {"id": "h2", "colonna": "H2", "codice": "AMMS233", "giacenza": 4},
            {"id": "h3", "colonna": "H3", "codice": "AMMS2240", "giacenza": 4},
        ]
        await database.vending.insert_many(original)
        result = await migrate_vending_column_zero_padding(database, backup)
        rows = await database.vending.find(
            {}, {"_id": 0, "id": 1, "colonna": 1, "codice": 1, "giacenza": 1}
        ).sort("colonna", 1).to_list(None)
        migration = await database.app_migrations.find_one(
            {"id": VENDING_COLUMN_ZERO_PADDING_MIGRATION_ID}, {"_id": 0}
        )
        return result, rows, migration

    result, rows, migration = asyncio.run(scenario())

    assert result["gia_applicata"] is False
    assert [(row["colonna"], row["codice"], row["giacenza"]) for row in rows] == [
        ("H01", "AMMS2602", 5),
        ("H02", "AMMS233", 4),
        ("H03", "AMMS2240", 4),
    ]
    assert migration["backup_id"] == "backup-vending-columns"
    backup.assert_awaited_once()
    assert backup.await_args.args[2] == ["vending"]


def test_zero_padding_migration_is_idempotent():
    database = AsyncMongoMockClient()["vending_column_migration_idempotent"]
    backup = AsyncMock(return_value={"id": "backup-vending-columns"})

    async def scenario():
        await database.vending.insert_one(
            {"id": "h1", "colonna": "H1", "codice": "AMMS2602", "giacenza": 5}
        )
        first = await migrate_vending_column_zero_padding(database, backup)
        second = await migrate_vending_column_zero_padding(database, backup)
        return first, second

    first, second = asyncio.run(scenario())

    assert first["gia_applicata"] is False
    assert second["gia_applicata"] is True
    backup.assert_awaited_once()


def test_zero_padding_migration_refuses_destination_conflicts_before_backup():
    database = AsyncMongoMockClient()["vending_column_migration_conflict"]
    backup = AsyncMock(return_value={"id": "unused"})

    async def scenario():
        await database.vending.insert_many([
            {"id": "legacy", "colonna": "H1"},
            {"id": "target", "colonna": "H01"},
        ])
        await migrate_vending_column_zero_padding(database, backup)

    with pytest.raises(RuntimeError, match="H01"):
        asyncio.run(scenario())
    backup.assert_not_awaited()

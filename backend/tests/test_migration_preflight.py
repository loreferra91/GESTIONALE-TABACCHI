"""Read-only staging startup preflight never mutates source collections."""
import asyncio

import pytest
from mongomock_motor import AsyncMongoMockClient

from backend.migration_preflight import (
    inspect_startup_migrations, validate_staging_cli_target,
)


def test_preflight_detects_business_key_duplicates_and_column_collision():
    async def scenario():
        db = AsyncMongoMockClient()["gestionale_cert_migration_readonly"]
        await db.prodotti.insert_many([
            {"codice": "DUP", "prezzo": 5},
            {"codice": "DUP", "prezzo": 7},
        ])
        await db.vending.insert_many([
            {"colonna": "H1", "codice": "DUP"},
            {"colonna": "H01", "codice": "DUP"},
            {"colonna": "H2", "codice": "OTHER"},
        ])
        await db.parametri.insert_one({"nome": "TEST", "valore": 1})
        await db.app_migrations.insert_one({
            "id": "auto-order-2-schema-v1", "completed": True,
        })
        collection_names = await db.list_collection_names()
        snapshots = {
            name: await db[name].find({}).to_list(None)
            for name in collection_names
        }
        indexes = {
            name: await db[name].index_information()
            for name in collection_names
        }
        result = await inspect_startup_migrations(db)
        after_names = await db.list_collection_names()
        for name in collection_names:
            assert snapshots[name] == await db[name].find({}).to_list(None)
            assert indexes[name] == await db[name].index_information()
        assert after_names == collection_names
        assert result["has_known_blockers"]
        assert result["mode"] == "read_only"
        assert result["safe_to_run_automatic_migration"] is False
        indexed = {row["collection"]: row for row in result["business_indexes"]}
        assert indexed["prodotti"]["status"] == "duplicate_keys"
        assert indexed["prodotti"]["duplicate_examples"] == ["DUP"]
        assert indexed["parametri"]["status"] == "unique_index_missing"
        assert result["vending_column_zero_padding"]["collisions"] == [
            {"from": "H1", "to": "H01"}
        ]
        assert result["vending_column_zero_padding"]["pending"] == [
            {"from": "H2", "to": "H02"}
        ]
        migrations = {
            row["id"]: row for row in result["migration_records"]
        }
        assert migrations["auto-order-2-schema-v1"]["recorded"] is True
        assert migrations["unique-business-keys-v1"]["recorded"] is False
        assert "ensure_product_ids" in result["unversioned_startup_writes_require_review"]

    asyncio.run(scenario())


def test_preflight_understands_existing_unique_indexes():
    async def scenario():
        db = AsyncMongoMockClient()["gestionale_cert_existing_indexes"]
        await db.prodotti.create_index("codice", unique=True)
        await db.vending.create_index("colonna", unique=True)
        await db.parametri.create_index("nome", unique=True)
        result = await inspect_startup_migrations(db)
        assert result["has_known_blockers"] is False
        assert [row["status"] for row in result["business_indexes"]] == [
            "unique_index_present"
        ] * 3
        assert result["vending_column_zero_padding"]["collisions"] == []

    asyncio.run(scenario())


@pytest.mark.parametrize("environment,database,url", [
    ("production", "gestionale_staging",
     "mongodb+srv://user:pass@gestionale-staging.example.mongodb.net/test"),
    ("staging", "gestionale",
     "mongodb+srv://user:pass@gestionale-staging.example.mongodb.net/test"),
    ("staging", "gestionale_staging",
     "mongodb+srv://user:pass@production.example.mongodb.net/test"),
    ("staging", "gestionale_staging",
     "mongodb://localhost:27017/test"),
])
def test_cli_refuses_non_staging_targets(environment, database, url):
    with pytest.raises(ValueError):
        validate_staging_cli_target(environment, database, url)


def test_cli_accepts_dedicated_staging_database_only():
    validate_staging_cli_target(
        "staging", "gestionale_staging",
        "mongodb+srv://user:synthetic@gestionale-staging.example.mongodb.net/gestionale_staging",
    )

import asyncio

from mongomock_motor import AsyncMongoMockClient

from backend.index_migrations import migrate_unique_business_indexes


def test_business_indexes_become_unique_and_migration_is_idempotent():
    database = AsyncMongoMockClient()["unique_indexes"]

    async def scenario():
        await database.prodotti.insert_one({"codice": "P1"})
        await database.vending.insert_one({"colonna": 1})
        await database.parametri.insert_one({"nome": "IVA"})
        await database.prodotti.create_index("codice")
        first = await migrate_unique_business_indexes(database)
        second = await migrate_unique_business_indexes(database)
        indexes = {
            name: await database[name].index_information()
            for name in ("prodotti", "vending", "parametri")
        }
        return first, second, indexes

    first, second, indexes = asyncio.run(scenario())
    assert {row["status"] for row in first} == {"migrated"}
    assert {row["status"] for row in second} == {"already_unique"}
    assert all(any(item.get("unique") for item in info.values()) for info in indexes.values())


def test_business_index_migration_preserves_nonunique_index_on_duplicates():
    database = AsyncMongoMockClient()["duplicate_indexes"]

    async def scenario():
        await database.prodotti.insert_many([{"codice": "P1"}, {"codice": "P1"}])
        return await migrate_unique_business_indexes(database), await database.prodotti.index_information()

    results, indexes = asyncio.run(scenario())
    product = next(row for row in results if row["collection"] == "prodotti")
    assert product["status"] == "duplicates"
    assert product["values"] == ["P1"]
    assert any(info.get("key") == [("codice", 1)] and not info.get("unique") for info in indexes.values())

"""Staging-only recovery suite: all data synthetic and held in mock MongoDB."""
import asyncio

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

from backend.staging_restore import (
    apply_staging_restore, recover_staging_restore,
    replace_and_verify, restore_status,
)


def _database(name):
    return AsyncMongoMockClient()[name]


async def _checkpoint(database, names):
    previous = {
        name: await database[name].find({}, {"_id": 0}).to_list(None)
        for name in names
    }

    async def create():
        return {"id": "checkpoint-synthetic"}

    async def load(identifier):
        assert identifier == "checkpoint-synthetic"
        return previous

    return create, load


def test_staging_restore_replaces_all_collections_without_cross_document_transaction():
    db = _database("gestionale_cert_staging_synthetic_success")

    async def scenario():
        await db.prodotti.insert_one({"id": "old", "codice": "OLD"})
        await db.vendite.insert_one({"id": "old-sale", "codice": "OLD"})
        make_checkpoint, load_checkpoint = await _checkpoint(db, ["prodotti", "vendite"])
        incoming = {
            "prodotti": [{"id": "new", "codice": "NEW"}],
            "vendite": [{"id": "new-sale", "codice": "NEW"}],
        }
        result = await apply_staging_restore(
            database=db, collections=incoming,
            make_checkpoint=make_checkpoint, load_checkpoint=load_checkpoint,
        )
        products = await db.prodotti.find({}, {"_id": 0}).to_list(None)
        sales = await db.vendite.find({}, {"_id": 0}).to_list(None)
        return result, products, sales, incoming, await restore_status(db)

    result, products, sales, incoming, status = asyncio.run(scenario())
    assert result["ok"] is True
    assert result["restored_docs"] == 2
    assert result["pre_restore_backup"] == "checkpoint-synthetic"
    assert products == [{"id": "new", "codice": "NEW"}]
    assert sales == [{"id": "new-sale", "codice": "NEW"}]
    assert "_id" not in incoming["prodotti"][0]
    assert status["phase"] == "READY"


def test_restore_failure_rolls_back_all_written_collections_and_verifies_snapshot():
    db = _database("gestionale_cert_staging_synthetic_rollback")

    async def scenario():
        await db.prodotti.insert_one({"id": "old", "codice": "OLD"})
        await db.vendite.insert_one({"id": "old-sale", "codice": "OLD"})
        await db.vendite.create_index("id", unique=True)
        create, load = await _checkpoint(db, ["prodotti", "vendite"])
        bad_file = {
            "prodotti": [{"id": "changed", "codice": "NEW"}],
            "vendite": [{"id": "duplicate"}, {"id": "duplicate"}],
        }
        with pytest.raises(HTTPException) as error:
            await apply_staging_restore(
                database=db, collections=bad_file,
                make_checkpoint=create, load_checkpoint=load,
            )
        return (
            error.value,
            await db.prodotti.find({}, {"_id": 0}).to_list(None),
            await db.vendite.find({}, {"_id": 0}).to_list(None),
            await restore_status(db),
        )

    error, products, sales, status = asyncio.run(scenario())
    assert error.status_code == 503
    assert "recuperato e verificato" in error.detail
    assert products == [{"id": "old", "codice": "OLD"}]
    assert sales == [{"id": "old-sale", "codice": "OLD"}]
    assert status["phase"] == "READY"


def test_failed_rollback_fails_closed_until_explicit_recovery():
    db = _database("gestionale_cert_staging_synthetic_recovery")

    async def scenario():
        await db.prodotti.insert_one({"id": "old"})
        await db.vendite.insert_one({"id": "old-sale"})
        await db.vendite.create_index("id", unique=True)
        create, load = await _checkpoint(db, ["prodotti", "vendite"])

        async def fail_load(_identifier):
            raise RuntimeError("Simulated temporary checkpoint fetch failure")

        with pytest.raises(HTTPException) as error:
            await apply_staging_restore(
                database=db,
                collections={
                    "prodotti": [{"id": "new"}],
                    "vendite": [{"id": "duplicate"}, {"id": "duplicate"}],
                },
                make_checkpoint=create,
                load_checkpoint=fail_load,
            )
        failed_state = await restore_status(db)
        with pytest.raises(HTTPException) as repeated:
            await apply_staging_restore(
                database=db, collections={"prodotti": []},
                make_checkpoint=create, load_checkpoint=load,
            )

        recovery = await recover_staging_restore(database=db, load_checkpoint=load)
        restored_products = await db.prodotti.find({}, {"_id": 0}).to_list(None)
        restored_sales = await db.vendite.find({}, {"_id": 0}).to_list(None)
        return error.value, repeated.value, failed_state, recovery, restored_products, restored_sales, await restore_status(db)

    error, repeated, failed, recovery, products, sales, state = asyncio.run(scenario())
    assert error.status_code == 503
    assert failed["phase"] == "FAILED"
    assert repeated.status_code == 423
    assert recovery["restored"] is True
    assert products == [{"id": "old"}]
    assert sales == [{"id": "old-sale"}]
    assert state["phase"] == "READY"


def test_interrupted_checkpoint_preparation_recovers_without_changing_stock():
    db = _database("gestionale_cert_staging_synthetic_prepare")
    async def scenario():
        await db.prodotti.insert_one({"id": "unchanged"})
        await db.backup_restore_control.insert_one({
            "_id": "external-file-restore", "phase": "PREPARING",
            "checkpoint_id": "",
        })
        recovered = await recover_staging_restore(
            database=db, load_checkpoint=lambda _id: None,
        )
        return recovered, await db.prodotti.find({}, {"_id": 0}).to_list(None)
    recovered, products = asyncio.run(scenario())
    assert recovered == {"ok": True, "restored": False}
    assert products == [{"id": "unchanged"}]


def test_restore_multiset_verification_ignores_mongo_natural_order():
    db = _database("gestionale_cert_staging_synthetic_order")
    async def scenario():
        incoming = {"prodotti": [{"id": "A"}, {"id": "B"}, {"id": "A"}]}
        await replace_and_verify(db, incoming)
        return incoming, await db.prodotti.count_documents({})
    incoming, count = asyncio.run(scenario())
    assert count == 3
    assert incoming["prodotti"][0] == {"id": "A"}

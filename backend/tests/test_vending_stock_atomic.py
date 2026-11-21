"""Vending manual correction must preserve product totals and audit invariants."""
import asyncio
import os
from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")
os.environ.setdefault("ENVIRONMENT", "test")

from backend import server


def test_manual_stock_correction_records_exactly_one_audit_and_no_shop_movement(monkeypatch):
    db = AsyncMongoMockClient()["gestionale_cert_vending_atomic_unit"]
    monkeypatch.setattr(server, "db", db)

    async def scenario():
        await db.vending.insert_many([
            {"id": "V1", "colonna": "A01", "codice": "P1",
             "giacenza": 2, "capacita_max": 5},
            {"id": "V2", "colonna": "A02", "codice": "P1",
             "giacenza": 1, "capacita_max": 5},
        ])
        await db.prodotti.insert_one({
            "codice": "P1", "giacenza_negozio": 9, "giacenza_vending": 3,
        })
        first = await server.update_vending_giacenza("V1", {"giacenza": 4})
        again = await server.update_vending_giacenza("V1", {"giacenza": 4})
        product = await db.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        audit = await db.audit_events.find({}, {"_id": 0}).to_list(None)
        return first, again, product, audit

    first, again, product, audit = asyncio.run(scenario())
    assert first["giacenza_precedente"] == 2
    assert first["giacenza"] == 4
    assert again["giacenza_precedente"] == 4
    assert again["giacenza"] == 4
    assert product["giacenza_vending"] == 5
    assert product["giacenza_negozio"] == 9
    assert len(audit) == 1
    assert audit[0]["azione"] == "GIACENZA_CORRETTA"
    assert audit[0]["entita"] == "VENDING_COLONNA"
    assert audit[0]["prima"]["giacenza"] == 2
    assert audit[0]["dopo"]["giacenza"] == 4


def test_manual_correction_rejects_missing_product_instead_of_silent_success(monkeypatch):
    db = AsyncMongoMockClient()["gestionale_cert_vending_missing_product"]
    monkeypatch.setattr(server, "db", db)

    async def scenario():
        await db.vending.insert_one({
            "id": "V1", "colonna": "A01", "codice": "MISSING",
            "giacenza": 1, "capacita_max": 5,
        })
        with pytest.raises(HTTPException) as error:
            await server.update_vending_giacenza("V1", {"giacenza": 2})
        assert error.value.status_code == 409
        unchanged = await db.vending.find_one({"id": "V1"})
        assert unchanged["giacenza"] == 1
        assert await db.audit_events.count_documents({}) == 0

    asyncio.run(scenario())


@pytest.mark.certification
def test_real_replica_set_rolls_back_column_when_product_update_fails(monkeypatch):
    """No Atlas credential accepted: only loopback disposable cert databases."""
    mongo_url = os.environ.get("CERT_MONGO_URL")
    if not mongo_url:
        pytest.skip("requires disposable CERT_MONGO_URL")
    target = urlsplit(mongo_url)
    if target.scheme != "mongodb" or target.hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("Refusing non-loopback MongoDB certification")
    db_name = os.environ.get("CERT_DB_NAME", "gestionale_cert_vending_atomic")
    if not db_name.startswith("gestionale_cert_"):
        pytest.fail("Refusing to write outside disposable certification database")

    async def scenario():
        client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
        db = client[db_name]
        monkeypatch.setattr(server, "db", db)
        try:
            hello = await client.admin.command("hello")
            assert hello.get("setName"), "Replica set required for transaction proof"
            await client.drop_database(db_name)
            await db.vending.insert_many([
                {"id": "V1", "colonna": "A01", "codice": "P1",
                 "giacenza": 2, "capacita_max": 5},
                {"id": "V2", "colonna": "A02", "codice": "P1",
                 "giacenza": 1, "capacita_max": 5},
            ])
            await db.prodotti.insert_one({
                "codice": "P1", "giacenza_negozio": 9, "giacenza_vending": 3,
            })
            await db.command({
                "collMod": "prodotti",
                "validator": {
                    "$jsonSchema": {
                        "bsonType": "object",
                        "properties": {
                            "giacenza_vending": {"bsonType": "int", "maximum": 4},
                        },
                    },
                },
                "validationAction": "error",
            })
            with pytest.raises(OperationFailure):
                await server.update_vending_giacenza("V1", {"giacenza": 4})
            after_error = await db.vending.find_one({"id": "V1"})
            product = await db.prodotti.find_one({"codice": "P1"})
            assert after_error["giacenza"] == 2
            assert product["giacenza_vending"] == 3
            assert product["giacenza_negozio"] == 9
            assert await db.audit_events.count_documents({}) == 0

            first = await server.update_vending_giacenza("V1", {"giacenza": 3})
            repeated = await server.update_vending_giacenza("V1", {"giacenza": 3})
            product = await db.prodotti.find_one({"codice": "P1"})
            assert first["giacenza_precedente"] == 2
            assert repeated["giacenza_precedente"] == 3
            assert product["giacenza_vending"] == 4
            assert product["giacenza_negozio"] == 9
            assert await db.audit_events.count_documents({
                "entita": "VENDING_COLONNA", "azione": "GIACENZA_CORRETTA"
            }) == 1
        finally:
            await client.drop_database(db_name)
            client.close()

    asyncio.run(scenario())

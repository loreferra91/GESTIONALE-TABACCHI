"""Real disposable MongoDB test for large staging restore without long transactions."""
import asyncio
import os
from urllib.parse import urlsplit

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from backend.staging_restore import apply_staging_restore, restore_status


pytestmark = pytest.mark.certification


def test_large_restore_on_real_replica_set_without_a_single_huge_transaction():
    url = os.environ.get("CERT_MONGO_URL")
    if not url:
        pytest.skip("requires CERT_MONGO_URL")
    target = urlsplit(url)
    if target.scheme != "mongodb" or target.hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("Refusing any non-loopback MongoDB connection")
    db_name = os.environ.get("CERT_DB_NAME", "gestionale_cert_bulk_staging")
    if not db_name.startswith("gestionale_cert"):
        pytest.fail("Refusing a database outside disposable gestionale_cert*")

    async def scenario():
        client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
        db = client[db_name]
        try:
            hello = await client.admin.command("hello")
            assert hello.get("setName"), "Transactions-capable replica set required"
            await client.drop_database(db_name)
            await db.prodotti.insert_one({"id": "CERT-BEFORE", "codice": "CERT-BEFORE"})
            previous = {"prodotti": [{"id": "CERT-BEFORE", "codice": "CERT-BEFORE"}],
                        "db_storico_vend": [], "vendite": []}

            async def make_checkpoint():
                return {"id": "CERT-PREVIOUS"}

            async def load_checkpoint(identifier):
                assert identifier == "CERT-PREVIOUS"
                return previous

            incoming = {
                "prodotti": [{"id": "CERT-NEW", "codice": "CERT-NEW"}],
                "db_storico_vend": [
                    {"id": f"CERT-LEGACY-{i}", "codice": "CERT-NEW", "quantita": i % 6 + 1}
                    for i in range(12000)
                ],
                "vendite": [
                    {"id": f"CERT-SALE-{i}", "codice": "CERT-NEW", "quantita": i % 6 + 1}
                    for i in range(13500)
                ],
            }
            result = await apply_staging_restore(
                database=db, collections=incoming,
                make_checkpoint=make_checkpoint,
                load_checkpoint=load_checkpoint,
            )
            assert result["ok"] is True
            assert result["restored_docs"] == 25501
            assert await db.prodotti.count_documents({}) == 1
            assert await db.db_storico_vend.count_documents({}) == 12000
            assert await db.vendite.count_documents({}) == 13500
            assert (await restore_status(db))["phase"] == "READY"
        finally:
            await client.drop_database(db_name)
            client.close()

    asyncio.run(scenario())

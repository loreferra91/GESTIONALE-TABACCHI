"""Integration drill: real disposable MongoDB replica set, synthetic JSON only.

Never run against Atlas/production. Only loopback localhost and a named
gestionale_cert* database are allowed. The database is dropped before/after.
"""
import asyncio
import hashlib
import io
import json
import os
from urllib.parse import urlparse

import pytest
from fastapi import UploadFile
from motor.motor_asyncio import AsyncIOMotorClient

from backend.backup_router import (
    BACKUP_COLLECTIONS,
    BACKUP_FILE_FORMAT,
    BACKUP_FILE_VERSION,
    create_backup_router,
)


pytestmark = pytest.mark.certification


def test_external_json_restore_on_real_disposable_replica_set():
    mongo_url = os.environ.get("CERT_MONGO_URL")
    if not mongo_url:
        pytest.skip("CERT_MONGO_URL is required for isolated replica-set drill")
    parsed = urlparse(mongo_url)
    if parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("Refusing restore drill outside a loopback-only MongoDB")
    db_name = os.environ.get("CERT_DB_NAME", "gestionale_cert_external_restore")
    if not db_name.startswith("gestionale_cert"):
        pytest.fail("Refusing restore drill outside the gestionale_cert* namespace")

    async def scenario():
        client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
        database = client[db_name]
        try:
            hello = await client.admin.command("hello")
            assert hello.get("setName") and hello.get("logicalSessionTimeoutMinutes") is not None
            await client.drop_database(db_name)

            # Explicitly create indexes before restore; delete/insert must preserve them.
            await database.prodotti.create_index("codice", unique=True)
            await database.vendite.create_index("id", unique=True)
            await database.prodotti.insert_one({
                "id": "CERT-OLD", "codice": "CERT-OLD", "giacenza_negozio": 5,
            })
            await database.vendite.insert_one({
                "id": "CERT-OLD-SALE", "codice": "CERT-OLD", "quantita": 1,
            })

            collections = {name: [] for name in BACKUP_COLLECTIONS}
            collections["prodotti"] = [{
                "id": "CERT-P1", "codice": "CERT-P1",
                "giacenza_negozio": 12, "giacenza_vending": 3,
            }]
            collections["vendite"] = [{
                "id": "CERT-S1", "codice": "CERT-P1", "quantita": 2,
            }]
            collections["vending"] = [{
                "id": "CERT-COL1", "colonna": "CERT-COL1",
                "codice": "CERT-P1", "giacenza": 3,
            }]
            collections["vendite_bulk_imports"] = [{
                "id": "CERT-BATCH", "batch_id": "CERT-BATCH",
            }]
            collections["vending_operations"] = [{
                "id": "CERT-RELOAD", "codice": "CERT-P1",
            }]
            collections["cassa_rapida_batches"] = [{
                "id": "CERT-QUICK", "batch_id": "CERT-QUICK",
            }]
            collections["accounting_baselines"] = [{
                "id": "CERT-BASELINE", "source": "synthetic",
            }]
            collections["audit_events"] = [{
                "id": "CERT-AUDIT", "action": "synthetic",
            }]

            def checksum(rows):
                digest = hashlib.sha256()
                for row in rows:
                    digest.update(json.dumps(
                        row, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), default=str,
                    ).encode("utf-8"))
                    digest.update(b"\n")
                return digest.hexdigest()

            payload = {
                "format": BACKUP_FILE_FORMAT,
                "version": BACKUP_FILE_VERSION,
                "created_at": "2026-11-19T10:00:00+00:00",
                "label": "Synthetic certified restore",
                "counts": {key: len(rows) for key, rows in collections.items()},
                "checksums": {key: checksum(rows) for key, rows in collections.items()},
                "collections": collections,
            }

            def upload():
                return UploadFile(
                    filename="gestionale-backup-cert.json",
                    file=io.BytesIO(json.dumps(payload).encode("utf-8")),
                )

            async def transact(operation):
                async with await client.start_session() as session:
                    async with session.start_transaction():
                        return await operation(session)

            async def capped(upload_file):
                data = await upload_file.read(20 * 1024 * 1024 + 1)
                if len(data) > 20 * 1024 * 1024:
                    raise ValueError("Fixture exceeds the web upload limit")
                return data

            api = create_backup_router(
                get_database=lambda: database,
                session_kwargs=lambda session: {"session": session} if session else {},
                run_transactionally=transact,
                read_capped=capped,
            )
            preview = await api["backup_restore_file_preview"](upload())
            assert preview["can_restore"] is True
            assert preview["total_docs"] == 8
            assert await database.backup_snapshots.count_documents({}) == 0

            result = await api["backup_restore_file"](upload())
            assert result["ok"] is True
            assert result["restored_docs"] == 8
            for name, rows in collections.items():
                assert await database[name].count_documents({}) == len(rows), name

            product = await database.prodotti.find_one({"codice": "CERT-P1"})
            assert product["giacenza_negozio"] == 12
            assert product["giacenza_vending"] == 3
            assert not await database.prodotti.find_one({"codice": "CERT-OLD"})
            for name in (
                "vendite_bulk_imports", "vending_operations",
                "cassa_rapida_batches", "accounting_baselines", "audit_events",
            ):
                assert await database[name].find_one({"id": collections[name][0]["id"]})

            snapshot = await database.backup_snapshots.find_one(
                {"id": result["pre_restore_backup"]}
            )
            assert snapshot["counts"]["prodotti"] == 1
            assert snapshot["counts"]["vendite"] == 1
            old_snapshot = await database.backup_snapshot_items.find_one({
                "snapshot_id": result["pre_restore_backup"], "collection": "prodotti",
            })
            assert old_snapshot["doc"]["codice"] == "CERT-OLD"

            product_indexes = await database.prodotti.index_information()
            sale_indexes = await database.vendite.index_information()
            assert any(info.get("unique") for info in product_indexes.values())
            assert any(info.get("unique") for info in sale_indexes.values())
        finally:
            await client.drop_database(db_name)
            client.close()

    asyncio.run(scenario())

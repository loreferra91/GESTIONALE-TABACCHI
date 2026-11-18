"""Real-Mongo, isolated rsDesktopCert escrow conservation and manual projection."""
import asyncio
import os
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from backend.desktop_sync.local_outbox import payload_hash
from backend.desktop_sync.staging_receiver import StagingReceiver
from backend.desktop_sync.staging_authority import EscrowLaboratory, LaboratoryConflict
from backend.tests.test_desktop_staging_replica import _safe_target, sample_event


async def _scenario(url):
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=4000)
    db_name = "desktop_sync_staging_escrow_cert_" + uuid4().hex
    db = client[db_name]
    try:
        hello = await client.admin.command("hello")
        if hello.get("setName") != "rsDesktopCert" or not hello.get("isWritablePrimary"):
            pytest.fail("Only disposable rsDesktopCert allowed")
        await db.sync_metadata.insert_many([
            {"_id": "purpose", "value": "desktop-sync-staging"},
            {"_id": "escrow-lab-opt-in", "value": "escrow-lab-only"},
        ])
        device1, device2, workspace = str(uuid4()), str(uuid4()), str(uuid4())
        receiver1 = StagingReceiver(db, client, workspace_id=workspace, device_id=device1)
        receiver2 = StagingReceiver(db, client, workspace_id=workspace, device_id=device2)
        await receiver1.prepare()
        await receiver2.prepare()
        lab = EscrowLaboratory(db, client, workspace_id=workspace)
        await lab.prepare()
        await lab.seed_inventory("TEST001", 5)
        duplicate = await lab.seed_inventory("TEST001", 5)
        assert duplicate["replayed"] is True
        with pytest.raises(LaboratoryConflict, match="LAB_INVENTORY_ALREADY_INITIALIZED"):
            await lab.seed_inventory("TEST001", 10)
        await lab.allocate(device1, "TEST001", 3)
        await lab.allocate(device2, "TEST001", 2)
        with pytest.raises(LaboratoryConflict, match="LAB_STOCK_ALLOCATION_EXCEEDS_POOL"):
            await lab.allocate(device2, "TEST001", 1)
        assert (await lab.stock_report("TEST001"))["reserved"] == 5

        first = sample_event(device1)
        staged = await receiver1.stage(first)
        assert staged["status"] == "STAGED"
        applied = await lab.apply_staged(first["operation_id"])
        assert applied == {"operation_id": first["operation_id"], "lab_applied": True,
                           "business_applied": False, "replayed": False}
        replay = await lab.apply_staged(first["operation_id"])
        assert replay["replayed"] is True
        assert (await lab.stock_report("TEST001"))["consumed"] == 2

        second = sample_event(device2)
        await receiver2.stage(second)
        await lab.apply_staged(second["operation_id"])
        assert (await lab.stock_report("TEST001"))["consumed"] == 4

        # Device 1 has 1 remaining; a 2-pack sale must be quarantined.
        third = sample_event(device1)
        await receiver1.stage(third)
        with pytest.raises(LaboratoryConflict, match="LAB_DEVICE_ESCROW_EXHAUSTED"):
            await lab.apply_staged(third["operation_id"])
        with pytest.raises(LaboratoryConflict, match="LAB_OPEN_CONFLICT_REQUIRES_RECONCILIATION"):
            await lab.apply_staged(third["operation_id"])
        assert await db.lab_conflicts.count_documents({"status": "OPEN"}) == 1
        report = await lab.stock_report("TEST001")
        assert report == {"codice": "TEST001", "initial": 5, "unallocated": 0,
                          "reserved": 1, "consumed": 4, "balanced": True,
                          "business_applied": False}
        assert await db.lab_sales.count_documents({}) == 2
        assert await db.sync_receipts.count_documents({"status": "STAGED"}) == 3
        assert await db.vendite.count_documents({}) == 0
        assert await db.prodotti.count_documents({}) == 0
        assert await db.cassa.count_documents({}) == 0
        assert await db.ordini.count_documents({}) == 0
    finally:
        await client.drop_database(db_name)
        client.close()


def test_disposable_escrow_authority_and_conflicts():
    asyncio.run(_scenario(_safe_target()))


def test_requires_explicit_lab_markers(monkeypatch):
    with pytest.raises(ValueError):
        EscrowLaboratory(None, None, workspace_id="bad-id")

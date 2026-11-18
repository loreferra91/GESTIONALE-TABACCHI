"""Real, isolated MongoDB replica-set proof for the offline manual-sale outbox.

Never run against production. This test refuses any non-loopback host, any
replica-set name other than rsDesktopCert, and any run without explicit opt-in.
It only drops its own randomly-named disposable cert database.
"""
import asyncio
import os
import uuid
from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from backend.desktop_sync.local_outbox import create_indexes
from backend.sales_router import VenditaIn, create_sales_router

pytestmark = pytest.mark.certification


def _safe_url() -> str:
    url = os.environ.get("CERT_MONGO_URL", "")
    if os.environ.get("CERT_DESKTOP_ALLOW_TEST") != "1" or not url:
        pytest.skip("requires explicitly enabled disposable MongoDB replica set")
    parsed = urlsplit(url)
    if parsed.scheme != "mongodb" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("desktop certification refuses non-loopback/non-mongodb URI")
    if parsed.username or parsed.password or not parsed.port:
        pytest.fail("desktop certification refuses authenticated or implicit MongoDB target")
    return url


async def _scenario(url):
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=3000)
    database_name = "gestionale_cert_desktop_" + uuid.uuid4().hex
    database = client[database_name]
    try:
        hello = await client.admin.command("hello")
        if hello.get("setName") != "rsDesktopCert" or hello.get("logicalSessionTimeoutMinutes") is None:
            pytest.fail("expected disposable rsDesktopCert with transaction support")
        # No destructive work is done until the isolated replica set is verified.
        await database.prodotti.create_index("codice", unique=True)
        await database.vendite.create_index("idempotency_key", unique=True, sparse=True)
        await create_indexes(database)
        await database.prodotti.insert_one({
            "codice": "TEST001", "descrizione": "Cert item",
            "giacenza_negozio": 10, "venduti_negozio": 0,
        })

        async def run_tx(operation):
            async with await client.start_session() as session:
                return await session.with_transaction(operation)

        async def sync_vending(*args, **kwargs):
            raise AssertionError("vending projection must not run for shop sale")

        routes = create_sales_router(
            get_database=lambda: database,
            cap_limit=lambda value: value,
            product_code_text=lambda value: str(value).strip(),
            normalize_vending_column=lambda value: str(value).strip(),
            session_kwargs=lambda session: {"session": session} if session else {},
            run_transactionally=run_tx,
            sync_product_vending_stock=sync_vending,
            normalize_nonnegative_money=lambda amount: amount,
            decimal_money=lambda amount, **kw: amount,
            normalize_money=lambda amount: amount,
            request_operator=lambda request: "cert",
            cash_movement_model=object,
            create_backup_snapshot=lambda *args, **kwargs: None,
            desktop_device_id=str(uuid.uuid4()),
        )
        add_sale = routes["add_vendita"]
        original = VenditaIn(data="2026-11-18", codice="TEST001", quantita=2, importo=12)
        key = "desktop-cert-idempotent-001"
        first = await add_sale(original, key)
        same = await add_sale(original, key)
        assert first["id"] == same["id"]
        assert await database.vendite.count_documents({}) == 1
        assert await database.desktop_outbox.count_documents({}) == 1
        receipt = await database.desktop_outbox.find_one({}, {"_id": 0})
        assert receipt["status"] == "PENDING"
        assert receipt["event_payload"]["sale_id"] == first["id"]
        assert receipt["event_payload"]["importo_cents"] == 1200
        stock = await database.prodotti.find_one({"codice": "TEST001"})
        assert stock["giacenza_negozio"] == 8
        assert stock["venduti_negozio"] == 2

        with pytest.raises(HTTPException) as mismatch:
            await add_sale(VenditaIn(data="2026-11-18", codice="TEST001",
                                    quantita=3, importo=18), key)
        assert mismatch.value.status_code == 409
        with pytest.raises(HTTPException) as insufficient:
            await add_sale(VenditaIn(data="2026-11-18", codice="TEST001",
                                    quantita=50, importo=100), "desktop-cert-insufficient-002")
        assert insufficient.value.status_code == 409
        assert await database.vendite.count_documents({}) == 1
        assert await database.desktop_outbox.count_documents({}) == 1

        # Simulate an exception after stock decrement and sale insert but before
        # the outbox write. The real transaction MUST roll everything back.
        import backend.sales_router as sales_module
        original_record = sales_module.record_sale_outbox

        async def fail_outbox(*args, **kwargs):
            raise RuntimeError("simulated crash before outbox receipt")

        sales_module.record_sale_outbox = fail_outbox
        try:
            with pytest.raises(RuntimeError, match="simulated crash"):
                await add_sale(VenditaIn(data="2026-11-18", codice="TEST001",
                                        quantita=1, importo=6), "desktop-cert-rollback-003")
        finally:
            sales_module.record_sale_outbox = original_record
        assert await database.vendite.count_documents({}) == 1
        assert await database.desktop_outbox.count_documents({}) == 1
        stock = await database.prodotti.find_one({"codice": "TEST001"})
        assert stock["giacenza_negozio"] == 8

        recovered = await add_sale(VenditaIn(data="2026-11-18", codice="TEST001",
                                            quantita=1, importo=6), "desktop-cert-rollback-003")
        assert recovered["codice"] == "TEST001"
        assert await database.vendite.count_documents({}) == 2
        assert await database.desktop_outbox.count_documents({}) == 2
        stock = await database.prodotti.find_one({"codice": "TEST001"})
        assert stock["giacenza_negozio"] == 7
    finally:
        # The generated DB name is unguessable, guaranteed by this test.
        await client.drop_database(database_name)
        client.close()


def test_real_desktop_sale_outbox_commit_replay_conflict_and_rollback():
    asyncio.run(_scenario(_safe_url()))

"""Scratch-card inventory tests. All writes use isolated in-memory MongoDB."""
import asyncio

import httpx
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

from backend.gratta_vinci_router import create_gratta_vinci_router
from backend.backup_router import BACKUP_COLLECTIONS, OPTIONAL_BACKUP_COLLECTIONS


def scenario_runner(test):
    async def scenario():
        db = AsyncMongoMockClient()["gratta_vinci_isolated"]
        await db.gratta_vinci_prodotti.create_index("codice", unique=True)
        await db.gratta_vinci_prodotti.create_index("id", unique=True)
        await db.gratta_vinci_movimenti.create_index("idempotency_key", unique=True)
        app = FastAPI()
        app.include_router(create_gratta_vinci_router(
            get_database=lambda: db,
            session_kwargs=lambda session: {} if session is None else {"session": session},
            run_transactionally=lambda operation: operation(None),
            request_operator=lambda request: "test-operator",
        ), prefix="/api")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await test(client, db)
    asyncio.run(scenario())


def product(codice="GV001", stock=5, price="5.00"):
    return {
        "codice": codice,
        "descrizione": "Biglietto prova",
        "prezzo": price,
        "quantita_magazzino": stock,
    }


def movement(codice="GV001", quantity=1, key="unique-operation-0001"):
    return {"codice": codice, "quantita": quantity, "idempotency_key": key}


def test_product_creation_is_separate_and_duplicate_code_rejected():
    async def check(client, db):
        created = await client.post("/api/gratta-e-vinci/prodotti", json=product())
        assert created.status_code == 201, created.text
        row = created.json()
        assert row["quantita_magazzino"] == row["giacenza_iniziale"] == 5
        assert row["prezzo_centesimi"] == 500
        duplicate = await client.post("/api/gratta-e-vinci/prodotti", json=product())
        assert duplicate.status_code == 409
        bad_price = await client.post("/api/gratta-e-vinci/prodotti", json=product("GV002", price="5.001"))
        assert bad_price.status_code == 422
        negative = await client.post("/api/gratta-e-vinci/prodotti", json=product("GV002", stock=-1))
        assert negative.status_code == 422
        assert await db.prodotti.count_documents({}) == 0
        assert await db.vendite.count_documents({}) == 0
    scenario_runner(check)


def test_sale_replays_once_and_reports_price_snapshot():
    async def check(client, db):
        created = await client.post("/api/gratta-e-vinci/prodotti", json=product())
        assert created.status_code == 201
        sale = movement(quantity=2)
        first = await client.post("/api/gratta-e-vinci/vendite", json=sale)
        replay = await client.post("/api/gratta-e-vinci/vendite", json=sale)
        assert first.status_code == replay.status_code == 201
        assert first.json()["gia_registrato"] is False
        assert replay.json()["gia_registrato"] is True
        assert first.json()["id"] == replay.json()["id"]
        assert (await db.gratta_vinci_prodotti.find_one({"codice": "GV001"}))["quantita_magazzino"] == 3

        conflict = await client.post("/api/gratta-e-vinci/vendite", json=movement(quantity=3))
        assert conflict.status_code == 409
        update = await client.put("/api/gratta-e-vinci/prodotti/" + created.json()["id"], json={
            "descrizione": "Prezzo aggiornato", "prezzo": "10.00", "version": 1,
        })
        assert update.status_code == 200, update.text
        second = await client.post("/api/gratta-e-vinci/vendite", json=movement(
            quantity=1, key="unique-operation-0002",
        ))
        assert second.status_code == 201, second.text
        total = await client.get("/api/gratta-e-vinci/riepilogo")
        assert total.status_code == 200
        assert total.json()["totale_biglietti"] == 3
        assert total.json()["totale_centesimi"] == 2000  # 2*5 + 1*10
        assert total.json()["righe"][0]["quantita_magazzino"] == 2
        assert await db.prodotti.count_documents({}) == 0
        assert await db.cassa.count_documents({}) == 0
    scenario_runner(check)


def test_sale_blocks_insufficient_stock_and_replenishment_is_idempotent():
    async def check(client, db):
        await client.post("/api/gratta-e-vinci/prodotti", json=product(stock=1))
        invalid = await client.post("/api/gratta-e-vinci/vendite", json=movement(quantity=2))
        assert invalid.status_code == 409
        assert await db.gratta_vinci_movimenti.count_documents({}) == 0
        restock = movement(quantity=3, key="receipt-operation-001")
        first = await client.post("/api/gratta-e-vinci/carichi", json=restock)
        replay = await client.post("/api/gratta-e-vinci/carichi", json=restock)
        assert first.status_code == replay.status_code == 201
        assert replay.json()["gia_registrato"] is True
        assert await db.gratta_vinci_movimenti.count_documents({}) == 1
        assert (await db.gratta_vinci_prodotti.find_one({"codice": "GV001"}))["quantita_magazzino"] == 4
        wrong_type = await client.post("/api/gratta-e-vinci/vendite", json=restock)
        assert wrong_type.status_code == 409
    scenario_runner(check)


def test_concurrent_sales_never_oversell():
    async def check(client, db):
        await client.post("/api/gratta-e-vinci/prodotti", json=product(stock=1))
        results = await asyncio.gather(*[
            client.post("/api/gratta-e-vinci/vendite", json=movement(key="parallel-sale-" + str(n).zfill(4)))
            for n in range(2)
        ])
        assert sorted(item.status_code for item in results) == [201, 409]
        row = await db.gratta_vinci_prodotti.find_one({"codice": "GV001"})
        assert row["quantita_magazzino"] == 0
        assert row["venduti_totale"] == 1
        assert await db.gratta_vinci_movimenti.count_documents({"tipo": "VENDITA"}) == 1
    scenario_runner(check)


def test_report_date_validation_and_history():
    async def check(client, db):
        await client.post("/api/gratta-e-vinci/prodotti", json=product())
        await client.post("/api/gratta-e-vinci/vendite", json=movement())
        invalid_date = await client.get("/api/gratta-e-vinci/riepilogo", params={"giorno": "2026-99-01"})
        assert invalid_date.status_code == 422
        no_sales = await client.get("/api/gratta-e-vinci/riepilogo", params={"giorno": "2020-01-01"})
        assert no_sales.json()["righe"][0]["venduti"] == 0
        history = await client.get("/api/gratta-e-vinci/movimenti", params={"codice": "GV001"})
        assert history.status_code == 200
        assert len(history.json()) == 1
        assert history.json()[0]["operatore"] == "test-operator"
    scenario_runner(check)


def test_backup_includes_new_inventory_and_history():
    assert {"gratta_vinci_prodotti", "gratta_vinci_movimenti"} <= set(BACKUP_COLLECTIONS)
    assert {"gratta_vinci_prodotti", "gratta_vinci_movimenti"} <= set(OPTIONAL_BACKUP_COLLECTIONS)

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from pymongo.errors import BulkWriteError

from backend import server
from backend.server import _bulk_upsert
from backend.sales_router import is_bulk_category_summary


class FakeCollection:
    def __init__(self, results):
        self.results = iter(results)
        self.batch_sizes = []

    async def bulk_write(self, operations, ordered):
        self.batch_sizes.append(len(operations))
        assert ordered is False
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def test_bulk_upsert_batches_and_counts_results():
    collection = FakeCollection([
        SimpleNamespace(upserted_count=100, matched_count=900),
        SimpleNamespace(upserted_count=5, matched_count=495),
    ])

    result = asyncio.run(_bulk_upsert(collection, [object()] * 1500))

    assert collection.batch_sizes == [1000, 500]
    assert result == {"inseriti": 105, "aggiornati": 1395, "errori": 0}


def test_bulk_upsert_reports_partial_write_errors():
    error = BulkWriteError({
        "nUpserted": 1,
        "nMatched": 1,
        "writeErrors": [{"index": 2, "code": 11000}],
    })
    collection = FakeCollection([error])

    result = asyncio.run(_bulk_upsert(collection, [object()] * 3))

    assert result == {"inseriti": 1, "aggiornati": 1, "errori": 1}


def test_bulk_upsert_accepts_empty_input():
    collection = FakeCollection([])

    result = asyncio.run(_bulk_upsert(collection, []))

    assert collection.batch_sizes == []
    assert result == {"inseriti": 0, "aggiornati": 0, "errori": 0}


@pytest.mark.parametrize(("codice", "descrizione"), [
    ("10209", "ACCESSORI TABACCHI"),
    ("10008", "InalazioneSenzaCombustione"),
    ("10007", "TrinciatiPerSigarette"),
    ("10004", "Sigari"),
])
def test_bulk_category_summaries_are_recognized(codice, descrizione):
    assert is_bulk_category_summary(codice, descrizione)


def test_bulk_category_summary_requires_matching_code_and_description():
    assert not is_bulk_category_summary("10008", "OCB FILTRI")


def test_bulk_sales_skip_category_summaries_without_touching_stock(monkeypatch):
    database = AsyncMongoMockClient()["bulk_sales_category_summary_test"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1", "descrizione": "Prodotto uno",
            "giacenza_negozio": 5, "venduti_negozio": 0,
        })
        result = await server.bulk_vendite(server.BulkVenditaIn(righe=[
            {"codice": "10008", "descrizione": "InalazioneSenzaCombustione", "quantita": 8, "importo": 56.70},
            {"codice": "P1", "descrizione": "Prodotto uno", "quantita": 1, "importo": 5},
        ]))
        product = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        sales = await database.vendite.find({}, {"_id": 0}).to_list(None)
        return result, product, sales

    result, product, sales = asyncio.run(scenario())

    assert result["inseriti"] == 1
    assert result["saltati"] == 1
    assert product["giacenza_negozio"] == 4
    assert [sale["codice"] for sale in sales] == ["P1"]


def test_bulk_sales_can_be_undone_and_stock_is_restored(monkeypatch):
    database = AsyncMongoMockClient()["bulk_sales_undo_test"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1",
            "descrizione": "Prodotto uno",
            "giacenza_negozio": 10,
            "venduti_negozio": 0,
            "giacenza_vending": 0,
            "venduti_vending": 0,
        })
        await database.prodotti.insert_one({
            "codice": "P2",
            "descrizione": "Prodotto due",
            "giacenza_negozio": 4,
            "venduti_negozio": 0,
            "giacenza_vending": 0,
            "venduti_vending": 0,
        })
        imported = await server.bulk_vendite(server.BulkVenditaIn(
            canale="NEGOZIO",
            pagamento="CONTANTI",
            righe=[
                {"data": "2026-10-06", "codice": "P1", "descrizione": "", "quantita": 2, "importo": 12},
                {"data": "2026-10-06", "codice": "P2", "descrizione": "Altro", "quantita": 1, "importo": 3},
            ],
        ))
        product_after_import = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        latest = await server.ultimo_bulk_vendite()
        undone = await server.annulla_bulk_vendite(imported["batch_id"])
        product_after_undo = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        remaining_sales = await database.vendite.find({"batch_id": imported["batch_id"]}).to_list(10)
        metadata = await database.vendite_bulk_imports.find_one({"id": imported["batch_id"]}, {"_id": 0})
        latest_after_undo = await server.ultimo_bulk_vendite()
        return imported, product_after_import, latest, undone, product_after_undo, remaining_sales, metadata, latest_after_undo

    imported, product_after_import, latest, undone, product_after_undo, remaining_sales, metadata, latest_after_undo = asyncio.run(scenario())

    assert imported["inseriti"] == 2
    assert imported["batch_id"]
    assert product_after_import["giacenza_negozio"] == 8
    assert product_after_import["venduti_negozio"] == 2
    assert latest["id"] == imported["batch_id"]
    assert undone["rimossi"] == 2
    assert product_after_undo["giacenza_negozio"] == 10
    assert product_after_undo["venduti_negozio"] == 0
    assert remaining_sales == []
    assert metadata["status"] == "annulled"
    assert latest_after_undo is None


def test_bulk_sales_cannot_be_undone_twice(monkeypatch):
    database = AsyncMongoMockClient()["bulk_sales_double_undo_test"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1", "descrizione": "Prodotto uno",
            "giacenza_negozio": 2, "venduti_negozio": 0,
        })
        imported = await server.bulk_vendite(server.BulkVenditaIn(
            righe=[{"data": "2026-10-06", "codice": "P1", "quantita": 1, "importo": 5}],
        ))
        await server.annulla_bulk_vendite(imported["batch_id"])
        await server.annulla_bulk_vendite(imported["batch_id"])

    with pytest.raises(HTTPException, match="già annullato") as exc:
        asyncio.run(scenario())

    assert exc.value.status_code == 404


def test_bulk_sales_reject_unknown_product_without_partial_writes(monkeypatch):
    database = AsyncMongoMockClient()["bulk_sales_atomic_validation_test"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1", "descrizione": "Prodotto uno",
            "giacenza_negozio": 10, "venduti_negozio": 0,
        })
        with pytest.raises(HTTPException) as caught:
            await server.bulk_vendite(server.BulkVenditaIn(righe=[
                {"codice": "P1", "quantita": 2, "importo": 10},
                {"codice": "SCONOSCIUTO", "quantita": 1, "importo": 5},
            ]))
        product = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        sales = await database.vendite.count_documents({})
        return caught.value, product, sales

    error, product, sales = asyncio.run(scenario())
    assert error.status_code == 422
    assert product["giacenza_negozio"] == 10
    assert product["venduti_negozio"] == 0
    assert sales == 0


def test_manual_sale_can_be_undone_and_stock_is_restored(monkeypatch):
    database = AsyncMongoMockClient()["manual_sale_undo_test"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1",
            "descrizione": "Prodotto uno",
            "giacenza_negozio": 10,
            "venduti_negozio": 0,
        })
        sale = await server.add_vendita(server.VenditaIn(
            data="2026-10-06",
            codice="P1",
            quantita=2,
            importo=12,
        ))
        latest = await server.ultima_vendita_manuale()
        product_after_import = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        undone = await server.annulla_vendita_manuale(sale["id"])
        product_after_undo = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        remaining = await database.vendite.find_one({"id": sale["id"]})
        return sale, latest, product_after_import, undone, product_after_undo, remaining

    sale, latest, product_after_import, undone, product_after_undo, remaining = asyncio.run(scenario())

    assert latest["id"] == sale["id"]
    assert product_after_import["giacenza_negozio"] == 8
    assert product_after_import["venduti_negozio"] == 2
    assert undone["rimossi"] == 1
    assert product_after_undo["giacenza_negozio"] == 10
    assert product_after_undo["venduti_negozio"] == 0
    assert remaining is None

@pytest.mark.parametrize('quantity', ['1.5', '1,5', -1, 0, True, 'NaN', '2x', '9007199254740992'])
def test_bulk_rejects_invalid_quantities(monkeypatch, quantity):
    database = AsyncMongoMockClient()['bulk_quantity_test']
    monkeypatch.setattr(server, 'db', database)
    async def scenario():
        await database.prodotti.insert_one({'codice': 'P1', 'giacenza_negozio': 10, 'venduti_negozio': 0})
        with pytest.raises(HTTPException) as exc:
            await server.bulk_vendite(server.BulkVenditaIn(righe=[{'codice': 'P1', 'quantita': quantity, 'importo': 5}]))
        assert exc.value.status_code == 422
        assert (await database.prodotti.find_one({'codice': 'P1'}))['giacenza_negozio'] == 10
        assert await database.vendite.count_documents({}) == 0
    asyncio.run(scenario())


def test_bulk_replay_conflict_and_undo(monkeypatch):
    database = AsyncMongoMockClient()['bulk_replay_test']
    monkeypatch.setattr(server, 'db', database)
    async def scenario():
        await database.prodotti.insert_one({'codice': 'P1', 'giacenza_negozio': 5, 'venduti_negozio': 0})
        body = server.BulkVenditaIn(idempotency_key='test-bulk-key', righe=[
            {'codice': 'P1', 'quantita': '2.0', 'importo': 10},
            {'codice': 'P1', 'quantita': 3, 'importo': 15},
        ])
        first = await server.bulk_vendite(body)
        assert await server.bulk_vendite(body) == first
        assert (await database.prodotti.find_one({'codice': 'P1'}))['giacenza_negozio'] == 0
        assert await database.vendite.count_documents({}) == 2
        changed = body.model_copy(deep=True)
        changed.righe[0]['quantita'] = 1
        with pytest.raises(HTTPException) as exc:
            await server.bulk_vendite(changed)
        assert exc.value.status_code == 409
        await server.annulla_bulk_vendite(first['batch_id'])
        with pytest.raises(HTTPException) as exc:
            await server.bulk_vendite(body)
        assert exc.value.status_code == 409
        assert (await database.prodotti.find_one({'codice': 'P1'}))['giacenza_negozio'] == 5
    asyncio.run(scenario())


@pytest.mark.parametrize('sale_date', ['2026-02-30', '19/11/2026', 'garbage'])
def test_bulk_rejects_invalid_dates(monkeypatch, sale_date):
    database = AsyncMongoMockClient()['bulk_dates_test']
    monkeypatch.setattr(server, 'db', database)
    async def scenario():
        await database.prodotti.insert_one({'codice': 'P1', 'giacenza_negozio': 10})
        with pytest.raises(HTTPException) as exc:
            await server.bulk_vendite(server.BulkVenditaIn(righe=[{'codice': 'P1', 'quantita': 1, 'data': sale_date}]))
        assert exc.value.status_code == 422
        assert await database.vendite.count_documents({}) == 0
    asyncio.run(scenario())


def test_sales_totals_exceed_page_limit(monkeypatch):
    database = AsyncMongoMockClient()['sales_totals_test']
    monkeypatch.setattr(server, 'db', database)
    async def scenario():
        await database.vendite.insert_many([{'data': '2026-11-19', 'quantita': 2, 'importo': 3} for _ in range(501)])
        await database.vendite.insert_one({'data': '2026-11-18', 'quantita': 99, 'importo': 99})
        assert len(await server.list_vendite(giorno='2026-11-19')) == 500
        assert await server.totali_vendite(giorno='2026-11-19') == {'importo': 1503, 'pezzi': 1002, 'righe': 501}
    asyncio.run(scenario())


def test_bulk_concurrent_claim_applies_once(monkeypatch):
    database = AsyncMongoMockClient()['bulk_concurrent_test']
    monkeypatch.setattr(server, 'db', database)
    async def scenario():
        await database.prodotti.insert_one({'codice': 'P1', 'giacenza_negozio': 10, 'venduti_negozio': 0})
        # Both requests pass validation before either claims the batch.
        ready = asyncio.Event()
        arrivals = 0
        async def backup(*args, **kwargs):
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2:
                ready.set()
            await ready.wait()
            return {'id': 'isolated-backup'}
        monkeypatch.setattr(server, 'create_backup_snapshot', backup)
        body = server.BulkVenditaIn(idempotency_key='concurrent-key', righe=[{'codice': 'P1', 'quantita': 2}])
        results = await asyncio.gather(server.bulk_vendite(body), server.bulk_vendite(body))
        assert results[0] == results[1]
        assert await database.vendite.count_documents({}) == 1
        assert (await database.prodotti.find_one({'codice': 'P1'}))['giacenza_negozio'] == 8
    asyncio.run(scenario())


def test_bulk_failed_write_releases_claim_and_restores_stock(monkeypatch):
    database = AsyncMongoMockClient()['bulk_rollback_test']
    monkeypatch.setattr(server, 'db', database)
    async def scenario():
        await database.prodotti.insert_one({'codice': 'P1', 'giacenza_negozio': 10, 'venduti_negozio': 0})
        body = server.BulkVenditaIn(idempotency_key='rollback-key', righe=[{'codice': 'P1', 'quantita': 2}])
        collection_type = type(database.vendite)
        original = collection_type.insert_one
        async def fail(collection, *args, **kwargs):
            if collection.name == 'vendite':
                raise RuntimeError('simulated write failure')
            return await original(collection, *args, **kwargs)
        monkeypatch.setattr(collection_type, 'insert_one', fail)
        with pytest.raises(RuntimeError, match='simulated'):
            await server.bulk_vendite(body)
        assert await database.vendite_bulk_imports.count_documents({}) == 0
        assert (await database.prodotti.find_one({'codice': 'P1'}))['giacenza_negozio'] == 10
        monkeypatch.setattr(collection_type, 'insert_one', original)
        await server.bulk_vendite(body)
        assert (await database.prodotti.find_one({'codice': 'P1'}))['giacenza_negozio'] == 8
    asyncio.run(scenario())

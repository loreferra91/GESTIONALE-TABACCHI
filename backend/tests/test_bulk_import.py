import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from pymongo.errors import BulkWriteError

from backend import server
from backend.server import _bulk_upsert


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
        imported = await server.bulk_vendite(server.BulkVenditaIn(
            canale="NEGOZIO",
            pagamento="CONTANTI",
            righe=[
                {"data": "2026-10-06", "codice": "P1", "descrizione": "", "quantita": 2, "importo": 12},
                {"data": "2026-10-06", "codice": "SCONOSCIUTO", "descrizione": "Altro", "quantita": 1, "importo": 3},
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
        imported = await server.bulk_vendite(server.BulkVenditaIn(
            righe=[{"data": "2026-10-06", "codice": "P1", "quantita": 1, "importo": 5}],
        ))
        await server.annulla_bulk_vendite(imported["batch_id"])
        await server.annulla_bulk_vendite(imported["batch_id"])

    with pytest.raises(HTTPException, match="già annullato") as exc:
        asyncio.run(scenario())

    assert exc.value.status_code == 404

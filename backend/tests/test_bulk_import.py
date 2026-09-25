import asyncio
from types import SimpleNamespace

from pymongo.errors import BulkWriteError

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

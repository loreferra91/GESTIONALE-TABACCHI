import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

import backend.server as server


class FakeCollection:
    def __init__(self):
        self.batches = []

    async def bulk_write(self, operations, ordered):
        self.batches.append((operations, ordered))
        return SimpleNamespace(
            matched_count=len(operations),
            modified_count=len(operations) - 1,
        )


def test_bulk_update_batches_adm_product_updates():
    collection = FakeCollection()
    operations = [object() for _ in range(2_501)]

    result = asyncio.run(server._bulk_update(collection, operations, batch_size=1_000))

    assert [len(batch) for batch, _ordered in collection.batches] == [1_000, 1_000, 501]
    assert all(ordered is False for _batch, ordered in collection.batches)
    assert result == {"trovati": 2_501, "modificati": 2_498, "errori": 0}

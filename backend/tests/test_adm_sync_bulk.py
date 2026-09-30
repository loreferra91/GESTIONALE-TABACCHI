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


class AsyncCursor:
    def __init__(self, documents):
        self._documents = iter(documents)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._documents)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class SyncCollection:
    def __init__(self, documents=None):
        self.documents = documents or []
        self.batch_sizes = []
        self.inserted = []

    def find(self, *_args, **_kwargs):
        return AsyncCursor(self.documents)

    async def bulk_write(self, operations, ordered):
        self.batch_sizes.append(len(operations))
        return SimpleNamespace(
            upserted_count=len(operations),
            matched_count=len(operations),
            modified_count=len(operations),
        )

    async def update_many(self, *_args, **_kwargs):
        return SimpleNamespace(modified_count=0)

    async def count_documents(self, *_args, **_kwargs):
        return 1

    async def insert_one(self, document):
        self.inserted.append(document)


def test_adm_sync_processes_one_category_at_a_time_and_only_matching_products(monkeypatch):
    categories = [
        {"categoria": "A", "url": "https://example.test/a.pdf"},
        {"categoria": "B", "url": "https://example.test/b.pdf"},
    ]
    rows = {
        "A": [{
            "adm_codice": "1", "codice": "AMMS1", "descrizione": "Uno",
            "confezione": "Pacco", "prezzo": 1.0, "categoria_adm": "A",
            "fonte": "ADM", "adm_pdf_url": "https://example.test/a.pdf",
            "adm_aggiornato_il": None,
        }],
        "B": [{
            "adm_codice": "2", "codice": "AMMS2", "descrizione": "Due",
            "confezione": "Pacco", "prezzo": 2.0, "categoria_adm": "B",
            "fonte": "ADM", "adm_pdf_url": "https://example.test/b.pdf",
            "adm_aggiornato_il": None,
        }],
    }
    parse_order = []

    def fake_parse(category):
        parse_order.append(category["categoria"])
        return rows[category["categoria"]]

    fake_db = SimpleNamespace(
        prodotti=SyncCollection([{"codice": "AMMS1"}]),
        listino_adm=SyncCollection(),
        adm_sync=SyncCollection(),
    )

    monkeypatch.setattr(server, "db", fake_db)
    monkeypatch.setattr(server, "fetch_adm_categories_sync", lambda: categories)
    monkeypatch.setattr(server, "parse_adm_pdf_sync", fake_parse)

    async def fake_apply_categories():
        return 0

    monkeypatch.setattr(server, "_apply_electronic_inhalation_categories", fake_apply_categories)

    result = asyncio.run(server.adm_sync())

    assert parse_order == ["A", "B"]
    assert fake_db.listino_adm.batch_sizes == [1, 1]
    assert fake_db.prodotti.batch_sizes == [1]
    assert result["categorie"] == 2
    assert result["righe"] == 2
    assert result["prodotti_aggiornati"] == 1
    assert len(fake_db.adm_sync.inserted) == 1

import asyncio
from types import SimpleNamespace

import backend.server as server


class FakeIgnoredAnomalies:
    def __init__(self):
        self.keys = set()

    async def distinct(self, _field, query=None):
        if not query:
            return list(self.keys)
        requested = set(query.get("key", {}).get("$in", []))
        return list(self.keys & requested)

    async def insert_many(self, documents):
        self.keys.update(document["key"] for document in documents)

    async def update_one(self, query, update, upsert=False):
        assert upsert is True
        self.keys.add(update["$set"]["key"])


def anomaly_payload(*keys):
    items = [{"id": key, "tipo": "test", "codice": key} for key in keys]
    return {"items": items, "summary": {"totale": len(items)}}


def test_dismiss_single_anomaly(monkeypatch):
    ignored = FakeIgnoredAnomalies()

    async def fake_anomalies():
        return anomaly_payload("one", "two")

    monkeypatch.setattr(server, "anomalie", fake_anomalies)
    monkeypatch.setattr(server, "db", SimpleNamespace(anomalie_ignorate=ignored))

    result = asyncio.run(server.dismiss_anomaly("one"))

    assert result == {"ok": True}
    assert ignored.keys == {"one"}


def test_dismiss_all_anomalies_is_idempotent(monkeypatch):
    ignored = FakeIgnoredAnomalies()

    async def fake_anomalies():
        return anomaly_payload("one", "two")

    monkeypatch.setattr(server, "anomalie", fake_anomalies)
    monkeypatch.setattr(server, "db", SimpleNamespace(anomalie_ignorate=ignored))

    first = asyncio.run(server.dismiss_all_anomalies())
    second = asyncio.run(server.dismiss_all_anomalies())

    assert first == {"ok": True, "eliminate": 2}
    assert second == {"ok": True, "eliminate": 2}
    assert ignored.keys == {"one", "two"}

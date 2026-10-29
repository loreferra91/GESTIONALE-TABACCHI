"""Disposable worker used by the SIGKILL transaction certification test.

It deliberately opens a real transaction against a database whose name is
guarded by the certification harness, writes two synthetic documents, signals
the parent process, and then waits to be killed before commit.
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlparse

from motor.motor_asyncio import AsyncIOMotorClient


async def main() -> None:
    mongo_url = os.environ["CERT_MONGO_URL"]
    db_name = os.environ["CERT_DB_NAME"]
    if not db_name.startswith("gestionale_cert"):
        raise RuntimeError("refusing non-certification database")
    if urlparse(mongo_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("refusing non-loopback MongoDB target")

    from backend import server

    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
    server.db = client[db_name]

    async def interrupted_write(session):
        kwargs = {"session": session}
        collection = server.db["_cert_process_kill"]
        await collection.insert_one({"_id": "first", "value": 1}, **kwargs)
        await collection.insert_one({"_id": "second", "value": 2}, **kwargs)
        print("TRANSACTION_OPEN", flush=True)
        await asyncio.sleep(300)

    await server._run_transactionally(interrupted_write)


if __name__ == "__main__":
    asyncio.run(main())

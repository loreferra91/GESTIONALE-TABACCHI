"""Three-node replica-set failover certification.

Run only against a disposable three-node set. The explicit environment gate
prevents an accidental step-down on any shared environment.
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import pytest
from pymongo import MongoClient
from pymongo.errors import AutoReconnect, ConnectionFailure, OperationFailure
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern


MONGO_URL = os.environ.get("CERT_MONGO_URL", "")
DB_NAME = os.environ.get("CERT_DB_NAME", "gestionale_cert_failover")
pytestmark = pytest.mark.certification


def test_three_node_primary_stepdown_preserves_acknowledged_writes():
    if os.environ.get("CERT_REQUIRE_THREE_NODE") != "1":
        pytest.skip("requires explicit disposable three-node certification target")
    if not MONGO_URL or not DB_NAME.startswith("gestionale_cert"):
        pytest.fail("unsafe certification target")

    node_urls = [item.strip() for item in os.environ.get("CERT_MONGO_NODES", "").split(",") if item.strip()]
    if len(node_urls) != 3:
        pytest.fail("CERT_MONGO_NODES must list exactly three disposable nodes")
    if any(urlparse(url).hostname not in {"127.0.0.1", "localhost", "::1"} for url in node_urls):
        pytest.fail("failover certification refuses non-loopback MongoDB nodes")
    clients = [
        MongoClient(url, directConnection=True, serverSelectionTimeoutMS=2000)
        for url in node_urls
    ]

    def current_primary():
        for index, candidate in enumerate(clients):
            try:
                if candidate.admin.command("hello").get("isWritablePrimary"):
                    return index, candidate
            except (AutoReconnect, ConnectionFailure, OperationFailure):
                continue
        return None, None

    try:
        original_index, client = current_primary()
        assert client is not None, "three-node set has no writable primary"
        status = client.admin.command("replSetGetStatus")
        voting_members = [m for m in status["members"] if m.get("stateStr") != "ARBITER"]
        assert len(voting_members) >= 3, status["members"]

        database = client[DB_NAME]
        collection = database.get_collection(
            "_cert_failover",
            write_concern=WriteConcern("majority"),
            read_concern=ReadConcern("majority"),
        )
        collection.drop()
        collection.insert_one({"_id": "before-stepdown", "acknowledged": True})

        try:
            client.admin.command({"replSetStepDown": 30, "force": True})
        except (AutoReconnect, ConnectionFailure, OperationFailure):
            # The command normally closes the primary connection immediately.
            pass

        new_index = None
        new_client = None
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            new_index, new_client = current_primary()
            if new_client is not None and new_index != original_index:
                break
            time.sleep(0.5)

        assert new_client is not None and new_index != original_index
        new_collection = new_client[DB_NAME].get_collection(
            "_cert_failover",
            write_concern=WriteConcern("majority"),
            read_concern=ReadConcern("majority"),
        )
        new_collection.insert_one({"_id": "after-stepdown", "acknowledged": True})
        assert new_collection.count_documents({}) == 2
        assert new_collection.count_documents({"acknowledged": True}) == 2
    finally:
        cleanup_client = None
        deadline = time.monotonic() + 10
        while cleanup_client is None and time.monotonic() < deadline:
            _, cleanup_client = current_primary()
            if cleanup_client is None:
                time.sleep(0.25)
        if cleanup_client is not None and DB_NAME.startswith("gestionale_cert"):
            cleanup_client.drop_database(DB_NAME)
        for client in clients:
            client.close()

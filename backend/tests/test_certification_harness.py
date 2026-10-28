"""Staging certification scenarios against a real, disposable MongoDB replica set.

These tests are deliberately excluded from the default suite: they require
``CERT_MONGO_URL`` to point at a replica set or sharded cluster with logical
sessions. A standalone MongoDB skips the whole module instead of silently
weakening the transaction assertions.

Never point ``CERT_MONGO_URL`` at production, staging or any shared database.
The module drops ``CERT_DB_NAME`` on entry and exit.
"""
import asyncio
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest

from backend.tests.certification_harness import (
    DEFAULT_DB_NAME,
    CertificationBlocked,
    CertificationHarness,
    backup_restore_drill,
    duplicate_idempotency_probe,
    rpo_rto_estimate,
    transaction_commit_and_abort,
    write_conflict_probe,
)

MONGO_URL = os.environ.get("CERT_MONGO_URL", "")
pytestmark = pytest.mark.certification


def _require_target() -> CertificationHarness:
    if not MONGO_URL:
        pytest.skip("requires CERT_MONGO_URL pointing at a disposable replica set")
    harness = CertificationHarness(mongo_url=MONGO_URL, db_name=DEFAULT_DB_NAME)
    harness.connect()
    try:
        harness.require_transactions()
    except CertificationBlocked as exc:
        harness.close()
        pytest.skip(str(exc))
    return harness


@pytest.fixture(scope="module")
def harness():
    h = _require_target()
    h.reset()
    try:
        yield h
    finally:
        h.reset()
        h.close()


# ---------------------------------------------------------------------------
# 1. Topology and transaction primitives
# ---------------------------------------------------------------------------
class TestReplicaSetPrimitives:
    def test_topology_is_replica_set_with_sessions(self, harness):
        topo = harness.require_transactions()
        assert topo["logicalSessionTimeoutMinutes"] is not None
        assert topo["setName"] or topo["msg"] == "isdbgrid"
        assert topo["isWritablePrimary"] is True

    def test_multi_document_commit_persists_every_write(self, harness):
        result = transaction_commit_and_abort(harness.client, harness.db_name)
        assert result["commit_result"] == "committed"
        assert result["after_commit"] == 2

    def test_aborted_transaction_leaves_no_partial_writes(self, harness):
        result = transaction_commit_and_abort(harness.client, harness.db_name)
        assert result["abort_raised"] is True
        assert result["after_abort"] == 2

    def test_concurrent_transactions_do_not_lose_updates(self, harness):
        result = write_conflict_probe(harness.client, harness.db_name)
        assert result["final_counter"] == result["expected_if_serialized"], result
        assert result["lost_update"] is False, result


# ---------------------------------------------------------------------------
# 2. Duplicate / concurrency on business keys
# ---------------------------------------------------------------------------
class TestDuplicateConcurrency:
    def test_unique_business_indexes_migrate_cleanly(self, harness):
        harness.reset()
        harness.seed_minimal_catalog()
        results = harness.seed_business_indexes()
        by_collection = {row["collection"]: row for row in results}
        assert by_collection["prodotti"]["status"] in {"migrated", "already_unique"}
        assert by_collection["vending"]["status"] in {"migrated", "already_unique"}
        assert by_collection["parametri"]["status"] in {"migrated", "already_unique"}

        prodotti = harness.indexes("prodotti")
        unique_codice = [i for i in prodotti.values() if i.get("unique") and list(i.get("key")) == [("codice", 1)]]
        assert unique_codice, prodotti

    def test_duplicate_business_key_is_rejected(self, harness):
        with pytest.raises(Exception) as exc:
            harness.db.prodotti.insert_one({"codice": "CERT-P1", "descrizione": "dup"})
        assert "duplicate" in type(exc.value).__name__.lower() or "E11000" in str(exc.value)

    def test_concurrent_same_idempotency_key_inserts_once(self, harness):
        result = duplicate_idempotency_probe(harness.client, harness.db_name)
        assert result["rows"] == 1, result
        assert result["inserted"] == 1, result
        assert result["duplicates_rejected"] == 7, result

    def test_dirty_collection_blocks_unique_migration(self, harness):
        from backend.index_migrations import migrate_unique_business_indexes

        harness.reset()
        harness.db.vending.insert_many([
            {"colonna": "Z01", "codice": "CERT-P1"},
            {"colonna": "Z01", "codice": "CERT-P2"},
        ])
        results = harness.run_async(migrate_unique_business_indexes)
        vending = next(r for r in results if r["collection"] == "vending")
        assert vending["status"] == "duplicates", vending
        assert vending["values"] == ["Z01"], vending
        indexes = harness.indexes("vending")
        assert not any(i.get("unique") for i in indexes.values()), indexes


# ---------------------------------------------------------------------------
# 3. Application idempotency via the real routers
# ---------------------------------------------------------------------------
class TestApplicationIdempotency:
    """Drive the real router coroutines against the replica set database."""

    def _run_router(self, harness, scenario):
        """Run ``scenario(server)`` with the router module pointed at Motor.

        ``backend.server`` binds ``db``/``client`` at import time; the scenario
        receives them rebound to a Motor client on the scenario's own loop.
        """
        os.environ.setdefault("MONGO_URL", harness.mongo_url)
        os.environ.setdefault("DB_NAME", harness.db_name)
        from backend import server

        def _fn(async_db):
            server.db = async_db
            return scenario(server)

        return harness.run_async(_fn)

    def test_manual_order_replay_is_once_only(self, harness):
        harness.reset()
        harness.seed_minimal_catalog()
        harness.seed_business_indexes()

        async def scenario(server):
            payload = server.OrdineIn(
                data="2026-10-28", codice="CERT-P1", descrizione="Cert order",
                quantita=3, prezzo=5, idempotency_key="cert-order-key-0001",
            )
            first = await server.add_ordine(payload)
            replay = await server.add_ordine(payload)
            return first, replay

        first, replay = self._run_router(harness, scenario)
        assert first["gia_registrato"] is False
        assert replay["gia_registrato"] is True
        assert replay["id"] == first["id"]
        assert harness.db.storico_ordini.count_documents(
            {"idempotency_key": "cert-order-key-0001"}
        ) == 1
        assert harness.db.storico_ordini.find_one(
            {"idempotency_key": "cert-order-key-0001"}
        )["annullato"] is False
        product = harness.db.prodotti.find_one({"codice": "CERT-P1"})
        assert product["giacenza_negozio"] == 63

    def test_manual_order_payload_mismatch_conflicts(self, harness):
        harness.reset()
        harness.seed_minimal_catalog()
        harness.seed_business_indexes()

        async def scenario(server):
            payload = server.OrdineIn(
                data="2026-10-28", codice="CERT-P1", descrizione="Cert order",
                quantita=3, prezzo=5, idempotency_key="cert-order-key-0002",
            )
            await server.add_ordine(payload)
            with pytest.raises(server.HTTPException) as exc:
                await server.add_ordine(payload.model_copy(update={"quantita": 9}))
            return exc.value

        exc = self._run_router(harness, scenario)
        assert exc.status_code == 409
        assert harness.db.storico_ordini.count_documents({}) == 1
        assert harness.db.prodotti.find_one({"codice": "CERT-P1"})["giacenza_negozio"] == 63

    def test_cash_movement_replay_is_once_only(self, harness):
        harness.reset()
        harness.seed_business_indexes()

        async def scenario(server):
            movement = server.MovimentoIn(
                data="2026-10-28", tipo="ENTRATA", importo=25.5,
                descrizione="Cert cash", pagamento="CONTANTI",
                idempotency_key="cert-cash-key-0001",
            )
            first = await server.add_cassa(movement)
            replay = await server.add_cassa(movement)
            return first, replay

        first, replay = self._run_router(harness, scenario)
        assert replay["id"] == first["id"]
        assert harness.db.cassa.count_documents(
            {"idempotency_key": "cert-cash-key-0001"}
        ) == 1

    def test_cash_movement_mismatch_conflicts(self, harness):
        harness.reset()
        harness.seed_business_indexes()

        async def scenario(server):
            movement = server.MovimentoIn(
                data="2026-10-28", tipo="ENTRATA", importo=25.5,
                descrizione="Cert cash", pagamento="CONTANTI",
                idempotency_key="cert-cash-key-0002",
            )
            await server.add_cassa(movement)
            with pytest.raises(server.HTTPException) as exc:
                await server.add_cassa(movement.model_copy(update={"importo": 99}))
            return exc.value

        exc = self._run_router(harness, scenario)
        assert exc.status_code == 409
        assert harness.db.cassa.count_documents({}) == 1


# ---------------------------------------------------------------------------
# 4. Interruption / restart semantics
# ---------------------------------------------------------------------------
class TestInterruptionAndRestart:
    def test_in_flight_transaction_is_invisible_to_other_sessions(self, harness):
        """A session that never commits must not expose its writes."""
        db = harness.db
        coll = db["_cert_interrupt"]
        coll.drop()
        coll.insert_one({"_id": "state", "n": 0})

        inserted = threading.Event()
        release = threading.Event()
        observed = {}

        def writer():
            with harness.client.start_session() as session:
                def cb(sess):
                    coll.insert_one({"_id": "pending"}, session=sess)
                    coll.update_one({"_id": "state"}, {"$inc": {"n": 1}}, session=sess)
                    inserted.set()
                    release.wait(timeout=10)
                    # Never commit: force abort by raising.
                    raise RuntimeError("cert-interrupted")
                try:
                    session.with_transaction(cb)
                except RuntimeError:
                    pass

        thread = threading.Thread(target=writer)
        thread.start()
        assert inserted.wait(timeout=10), "writer never reached its transaction body"

        observed["pending_visible"] = coll.count_documents({"_id": "pending"})
        observed["counter_during"] = coll.find_one({"_id": "state"})["n"]

        release.set()
        thread.join(timeout=15)

        observed["pending_after"] = coll.count_documents({"_id": "pending"})
        observed["counter_after"] = coll.find_one({"_id": "state"})["n"]
        coll.drop()

        assert observed["pending_visible"] == 0, observed
        assert observed["counter_during"] == 0, observed
        assert observed["pending_after"] == 0, observed
        assert observed["counter_after"] == 0, observed

    def test_committed_write_survives_new_client_session(self, harness):
        db = harness.db
        coll = db["_cert_restart"]
        coll.drop()

        with harness.client.start_session() as session:
            def cb(sess):
                coll.insert_one({"_id": "durable", "v": 1}, session=sess)
                return "ok"
            session.with_transaction(cb)

        # A brand new client is the closest local proxy for a process restart.
        from pymongo import MongoClient as FreshClient

        fresh = FreshClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            assert fresh[harness.db_name]["_cert_restart"].count_documents({"_id": "durable"}) == 1
        finally:
            fresh.close()
        coll.drop()


# ---------------------------------------------------------------------------
# 5. Backup / restore drill
# ---------------------------------------------------------------------------
class TestBackupRestoreDrill:
    def test_disposable_backup_restore_preserves_counts_and_indexes(self, harness):
        harness.reset()
        harness.seed_minimal_catalog()
        harness.seed_business_indexes()
        result = backup_restore_drill(harness.client, harness.db_name)
        assert result["counts_match"] is True, result
        assert result["indexes_preserved"] is True, result
        assert result["snapshot_rows"] >= 250, result
        assert result["restore_seconds"] < 60, result

    def test_rpo_rto_estimate_is_derived_from_observed_timings(self, harness):
        result = backup_restore_drill(harness.client, harness.db_name)
        estimate = rpo_rto_estimate(result["backup_seconds"], result["restore_seconds"])
        assert estimate["rto_observed_restore_s"] == result["restore_seconds"]
        assert estimate["rto_with_2x_safety_s"] == round(result["restore_seconds"] * 2, 3)
        assert "24h" in estimate["rpo_target"]
"""Reusable staging-certification harness for a real MongoDB replica set.

Design rules:
- Synthetic data only. Never point this at production or staging databases.
- The harness refuses to run unless ``CERT_MONGO_URL`` proves a replica set or
  sharded topology with logical sessions, so transaction assertions are real.
- Every scenario drops the database it owns before and after running.
- No network egress, no credentials, no writes outside the isolated database.

Run against a disposable local replica set::

    CERT_MONGO_URL="mongodb://127.0.0.1:27018/?directConnection=true" \\
    CERT_DB_NAME="gestionale_cert_harness" \\
    python3 -m pytest backend/tests/test_certification_harness.py -n 0 -q

The module exposes :class:`CertificationHarness`, which can also be imported by
one-off scripts for drills (backup/restore, interruption) that are awkward to
express as assertions.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure

DEFAULT_MONGO_URL = "mongodb://127.0.0.1:27018/?directConnection=true"
DEFAULT_DB_NAME = "gestionale_cert_harness"
SYNTHETIC_PREFIX = "CERT-"
# Any database the harness is allowed to drop MUST start with this prefix.
# This is the guard that keeps a mistyped CERT_DB_NAME from destroying a real
# database: reset() refuses anything that does not look like a cert database.
ALLOWED_DB_PREFIX = "gestionale_cert"



class CertificationBlocked(RuntimeError):
    """Raised when the target Mongo is not a transaction-capable replica set."""


@dataclass
class ScenarioResult:
    name: str
    status: str  # PASS | FAIL | SKIPPED
    detail: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class CertificationHarness:
    """Thin, explicit wrapper around a disposable replica-set database."""

    mongo_url: str = field(
        default_factory=lambda: os.environ.get("CERT_MONGO_URL", DEFAULT_MONGO_URL)
    )
    db_name: str = field(
        default_factory=lambda: os.environ.get("CERT_DB_NAME", DEFAULT_DB_NAME)
    )
    client: Optional[MongoClient] = field(default=None, init=False)
    db: Any = field(default=None, init=False)

    # -- lifecycle ---------------------------------------------------------
    def connect(self) -> "CertificationHarness":
        self.client = MongoClient(
            self.mongo_url,
            serverSelectionTimeoutMS=5000,
            uuidRepresentation="standard",
        )
        self.db = self.client[self.db_name]
        return self

    def topology(self) -> Dict[str, Any]:
        if self.client is None:
            raise CertificationBlocked("harness not connected")
        hello = self.client.admin.command("hello")
        return {
            "setName": hello.get("setName"),
            "msg": hello.get("msg"),
            "isWritablePrimary": bool(hello.get("isWritablePrimary")),
            "logicalSessionTimeoutMinutes": hello.get("logicalSessionTimeoutMinutes"),
        }

    def require_transactions(self) -> Dict[str, Any]:
        topo = self.topology()
        capable = bool(
            topo["logicalSessionTimeoutMinutes"] is not None
            and (topo["setName"] or topo["msg"] == "isdbgrid")
        )
        if not capable:
            raise CertificationBlocked(
                f"target is not transaction-capable: {topo!r}. "
                "Point CERT_MONGO_URL at a disposable replica set."
            )
        return topo

    def assert_disposable(self) -> None:
        """Refuse to touch a database that is not obviously a cert database.

        Guards every destructive operation (``reset``) against a mistyped
        ``CERT_DB_NAME`` such as ``gestionale`` or a production database name.
        The prefix allowlist is deliberately strict: no override flag, no
        environment escape hatch, because the cost of being wrong is data loss.
        """
        if not self.db_name.startswith(ALLOWED_DB_PREFIX):
            raise CertificationBlocked(
                f"refusing to drop database {self.db_name!r}: name must start "
                f"with {ALLOWED_DB_PREFIX!r}. Set CERT_DB_NAME to a disposable "
                "certification database."
            )

    def reset(self) -> None:
        """Drop every collection in the isolated certification database."""
        if self.client is None:
            raise CertificationBlocked("harness not connected")
        self.assert_disposable()
        self.client.drop_database(self.db_name)
        self.db = self.client[self.db_name]

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

    def run_async(self, coro_fn: Callable[[Any], Any]) -> Any:
        """Run ``coro_fn(db)`` on a fresh Motor client bound to a fresh loop.

        Motor clients are bound to the event loop that first uses them, so a
        single long-lived client cannot survive repeated ``asyncio.run`` calls.
        Each call therefore owns its loop and its client.
        """

        async def _inner():
            client = AsyncIOMotorClient(
                self.mongo_url, serverSelectionTimeoutMS=5000
            )
            try:
                return await coro_fn(client[self.db_name])
            finally:
                client.close()

        return asyncio.run(_inner())

    # -- fixtures ----------------------------------------------------------
    def seed_business_indexes(self) -> Dict[str, str]:
        """Create the unique business-key indexes production startup requires."""
        from backend.index_migrations import migrate_unique_business_indexes

        return self.run_async(migrate_unique_business_indexes)

    def seed_minimal_catalog(self) -> None:
        """Synthetic product + vending column + parameter rows."""
        self.db.prodotti.insert_many([
            {
                "codice": f"{SYNTHETIC_PREFIX}P1",
                "descrizione": "Certification product 1",
                "categoria": "ACCESSORI",
                "prezzo": 5.0,
                "acquistati": 100,
                "giacenza_negozio": 60,
                "giacenza_vending": 0,
                "venduti_negozio": 0,
                "venduti_vending": 0,
            },
            {
                "codice": f"{SYNTHETIC_PREFIX}P2",
                "descrizione": "Certification product 2",
                "categoria": "ACCESSORI",
                "prezzo": 7.0,
                "acquistati": 50,
                "giacenza_negozio": 40,
                "giacenza_vending": 0,
                "venduti_negozio": 0,
                "venduti_vending": 0,
            },
        ])
        self.db.vending.insert_one({
            "id": str(uuid.uuid4()),
            "colonna": "Z01",
            "codice": f"{SYNTHETIC_PREFIX}P1",
            "descrizione": "Certification product 1",
            "giacenza": 5,
            "capacita_max": 10,
            "soglia_minima": 2,
        })
        self.db.parametri.insert_many([
            {"nome": "SCORTA_MINIMA_NEGOZIO_VENDING", "valore": 2},
            {"nome": "GIORNI_COPERTURA_MIN", "valore": 7},
        ])

    def indexes(self, collection: str) -> Dict[str, Any]:
        return self.db[collection].index_information()

    def counts(self, collections: Optional[List[str]] = None) -> Dict[str, int]:
        names = collections or sorted(self.db.list_collection_names())
        return {name: self.db[name].count_documents({}) for name in names}

    # -- generic scenario runner ------------------------------------------
    def run(self, name: str, fn: Callable[[], Dict[str, Any]]) -> ScenarioResult:
        try:
            evidence = fn() or {}
            return ScenarioResult(name, "PASS", evidence=evidence)
        except AssertionError as exc:
            return ScenarioResult(name, "FAIL", detail=str(exc) or "assertion failed")
        except CertificationBlocked:
            raise
        except Exception as exc:  # noqa: BLE001 - report, never swallow silently
            return ScenarioResult(name, "FAIL", detail=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Scenario helpers shared by the pytest module and ad-hoc drills
# ---------------------------------------------------------------------------
def canonical_fingerprint(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def transaction_commit_and_abort(client: MongoClient, db_name: str) -> Dict[str, Any]:
    """Prove real multi-document commit and rollback on the replica set."""
    db = client[db_name]
    coll = db["_cert_txn_probe"]
    coll.drop()

    def commit_cb(session):
        coll.insert_one({"_id": "a", "v": 1}, session=session)
        coll.insert_one({"_id": "b", "v": 2}, session=session)
        return "committed"

    with client.start_session() as session:
        committed = session.with_transaction(commit_cb)
    after_commit = coll.count_documents({})

    def abort_cb(session):
        coll.insert_one({"_id": "c", "v": 3}, session=session)
        raise RuntimeError("cert-abort")

    aborted = False
    try:
        with client.start_session() as session:
            session.with_transaction(abort_cb)
    except RuntimeError as exc:
        aborted = str(exc) == "cert-abort"
    after_abort = coll.count_documents({})
    coll.drop()
    return {
        "commit_result": committed,
        "after_commit": after_commit,
        "abort_raised": aborted,
        "after_abort": after_abort,
    }


def write_conflict_probe(client: MongoClient, db_name: str) -> Dict[str, Any]:
    """Two concurrent transactions touching one document: one must lose.

    The loser surfaces as a WriteConflict (TransientTransactionError) which the
    driver retries, or as an OperationFailure if retries are exhausted. Either
    outcome is acceptable; silent lost updates are not.
    """
    db = client[db_name]
    coll = db["_cert_conflict_probe"]
    coll.drop()
    coll.insert_one({"_id": "counter", "n": 0})

    started = time.monotonic()

    def bump(session, delay: float):
        coll.update_one({"_id": "counter"}, {"$inc": {"n": 1}}, session=session)
        time.sleep(delay)
        coll.update_one({"_id": "counter"}, {"$inc": {"n": 1}}, session=session)

    import threading

    errors: List[str] = []

    def worker(delay: float):
        try:
            with client.start_session() as session:
                session.with_transaction(lambda s: bump(s, delay))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")

    threads = [threading.Thread(target=worker, args=(d,)) for d in (0.4, 0.0)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not any(t.is_alive() for t in threads), "a conflict worker never finished"
    final = coll.find_one({"_id": "counter"})["n"]
    conflicts = sum("WriteConflict" in e or "TransientTransactionError" in e for e in errors)
    coll.drop()
    return {
        "final_counter": final,
        "expected_if_serialized": 4,
        "conflict_errors": conflicts,
        "other_errors": [e for e in errors if "WriteConflict" not in e and "TransientTransactionError" not in e],
        "elapsed_s": round(time.monotonic() - started, 3),
        "lost_update": final < 4 and conflicts == 0,
        "retried_transparently": final == 4 and conflicts == 0,
        "retries_exhausted": conflicts > 0,
    }


def duplicate_idempotency_probe(client: MongoClient, db_name: str) -> Dict[str, Any]:
    """Concurrent inserts of the same idempotency key must yield exactly one row."""
    db = client[db_name]
    coll = db["_cert_idem_probe"]
    coll.drop()
    coll.create_index("idempotency_key", unique=True, sparse=True)

    key = "cert-idem-" + uuid.uuid4().hex
    outcomes: List[str] = []
    import threading

    def worker():
        try:
            coll.insert_one({"idempotency_key": key, "payload": "same"})
            outcomes.append("inserted")
        except DuplicateKeyError:
            outcomes.append("duplicate")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not any(t.is_alive() for t in threads), "an idempotency worker never finished"
    total = coll.count_documents({"idempotency_key": key})
    coll.drop()
    return {
        "inserted": outcomes.count("inserted"),
        "duplicates_rejected": outcomes.count("duplicate"),
        "rows": total,
    }


def backup_restore_drill(client: MongoClient, db_name: str) -> Dict[str, Any]:
    """Disposable snapshot/restore drill with counts, indexes and timing.

    Mirrors the application backup contract (collections + counts) without
    touching the application backup routes, so it can run on any replica set.
    """
    db = client[db_name]
    collections = ["prodotti", "vending", "parametri", "_cert_drill"]
    db["_cert_drill"].drop()
    db["_cert_drill"].insert_many([{"i": i, "v": f"v{i}"} for i in range(250)])

    pre_counts = {c: db[c].count_documents({}) for c in collections}
    pre_indexes = {c: sorted(db[c].index_information().keys()) for c in collections}

    t0 = time.monotonic()
    snapshot = {c: list(db[c].find({})) for c in collections}
    backup_seconds = round(time.monotonic() - t0, 3)

    # Mutate: delete half the drill rows plus every product.
    db["_cert_drill"].delete_many({"i": {"$lt": 125}})
    db["prodotti"].delete_many({})
    mutated_counts = {c: db[c].count_documents({}) for c in collections}

    t1 = time.monotonic()
    for c in collections:
        db[c].delete_many({})
        if snapshot[c]:
            db[c].insert_many([{k: v for k, v in doc.items()} for doc in snapshot[c]])
    restore_seconds = round(time.monotonic() - t1, 3)

    post_counts = {c: db[c].count_documents({}) for c in collections}
    post_indexes = {c: sorted(db[c].index_information().keys()) for c in collections}
    db["_cert_drill"].drop()
    return {
        "collections": collections,
        "pre_counts": pre_counts,
        "mutated_counts": mutated_counts,
        "post_counts": post_counts,
        "counts_match": pre_counts == post_counts,
        "indexes_preserved": pre_indexes == post_indexes,
        "backup_seconds": backup_seconds,
        "restore_seconds": restore_seconds,
        "snapshot_rows": sum(len(v) for v in snapshot.values()),
    }


def rpo_rto_estimate(backup_seconds: float, restore_seconds: float) -> Dict[str, Any]:
    """Translate drill timings into RPO/RTO bands for the report.

    RPO is driven by backup cadence (daily automatic backup), not by the drill
    duration; RTO is the observed restore time plus a safety factor.
    """
    return {
        "rpo_target": "<= 24h (daily automatic backup)",
        "rpo_observed_data_loss_window": "up to 24h between automatic snapshots",
        "rto_observed_restore_s": restore_seconds,
        "rto_with_2x_safety_s": round(restore_seconds * 2, 3),
        "backup_capture_s": backup_seconds,
        "note": "Timings are from synthetic in-process snapshots, not provider snapshots.",
    }
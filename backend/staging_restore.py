"""Staging-only bounded, recoverable backup restore without a giant transaction.

Atlas Free imposes transaction lifetime limits. A full 25k+ document restore
cannot safely assume one multi-collection transaction will finish in time.

The operation is NOT cross-collection atomic: staging maintenance blocks
other API requests while it runs; a durable state record blocks requests
after process interruption. A verified pre-restore checkpoint enables
compensating rollback. Never use this pathway in production.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any

from fastapi import HTTPException


RESTORE_CONTROL_ID = "external-file-restore"
BLOCKING_PHASES = frozenset({"PREPARING", "RESTORING", "ROLLING_BACK", "FAILED"})
BATCH_SIZE = 500


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(document: dict) -> bytes:
    return hashlib.sha256(json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")).digest()


async def replace_and_verify(database, collections: dict[str, list[dict]]):
    """Write bounded batches without a cross-collection transaction.

    The caller MUST hold the distributed restore lock and the durable
    staging maintenance flag. A rollback checkpoint MUST already exist.
    """
    for name, expected in collections.items():
        await database[name].delete_many({})
        for start in range(0, len(expected), BATCH_SIZE):
            # PyMongo injects _id into passed docs. Keep the verified JSON
            # payload intact, including its checksum and recovery material.
            batch = [dict(document) for document in expected[start:start + BATCH_SIZE]]
            await database[name].insert_many(batch, ordered=True)

    for name, expected in collections.items():
        actual_count = await database[name].count_documents({})
        if actual_count != len(expected):
            raise RuntimeError(f"Restore count mismatch in {name}")
        hashes = Counter(_fingerprint(document) for document in expected)
        async for stored in database[name].find({}, {"_id": 0}):
            value = _fingerprint(stored)
            hashes[value] -= 1
            if hashes[value] < 0:
                raise RuntimeError(f"Restore content mismatch in {name}")
        if any(hashes.values()):
            raise RuntimeError(f"Restore content mismatch in {name}")


async def restore_status(database) -> dict[str, Any]:
    state = await database.backup_restore_control.find_one(
        {"_id": RESTORE_CONTROL_ID},
        {"_id": 0, "phase": 1, "checkpoint_id": 1, "updated_at": 1},
    )
    return state or {"phase": "READY", "checkpoint_id": None}


async def _update_status(database, phase: str, checkpoint_id=None):
    updates: dict[str, Any] = {
        "phase": phase,
        "updated_at": _timestamp(),
    }
    if checkpoint_id is not None:
        updates["checkpoint_id"] = checkpoint_id
    await database.backup_restore_control.update_one(
        {"_id": RESTORE_CONTROL_ID},
        {"$set": updates},
        upsert=True,
    )


async def _restore_checkpoint(database, checkpoint_id, load_checkpoint):
    if not checkpoint_id:
        raise RuntimeError("Pre-restore checkpoint unavailable")
    collections = await load_checkpoint(checkpoint_id)
    await replace_and_verify(database, collections)


async def apply_staging_restore(
    *, database, collections, make_checkpoint, load_checkpoint,
    vending_created_at=None,
) -> dict[str, Any]:
    """Run under backup_router.restore_lock, on staging ONLY."""
    state = await restore_status(database)
    if state["phase"] in BLOCKING_PHASES:
        raise HTTPException(423, "Staging in manutenzione per ripristino incompleto: recuperare prima il checkpoint.")

    await _update_status(database, "PREPARING", checkpoint_id="")
    try:
        checkpoint = await make_checkpoint()
    except Exception:
        # No operational collection has been changed at this point.
        await _update_status(database, "READY", checkpoint_id="")
        raise
    checkpoint_id = checkpoint["id"]
    await _update_status(database, "RESTORING", checkpoint_id)
    try:
        await replace_and_verify(database, collections)
        if "vending" in collections:
            await database.vending.update_many({}, {"$set": {
                "giacenza_aggiornata_il": vending_created_at or _timestamp(),
                "giacenza_sorgente": "BACKUP_FILE_RIPRISTINATO",
            }})
        await _update_status(database, "READY", checkpoint_id)
        return {
            "ok": True,
            "restored_docs": sum(len(docs) for docs in collections.values()),
            "pre_restore_backup": checkpoint_id,
            "restore_strategy": "staging_checkpoint_compensation",
        }
    except Exception as exc:
        logging.exception(
            "Staging backup restore aborted; restoring verified checkpoint %s",
            checkpoint_id,
        )
        try:
            await _update_status(database, "ROLLING_BACK", checkpoint_id)
            await _restore_checkpoint(database, checkpoint_id, load_checkpoint)
            await _update_status(database, "READY", checkpoint_id)
        except Exception:
            logging.exception(
                "CRITICAL: staging checkpoint recovery failed; leaving API in maintenance mode"
            )
            try:
                await _update_status(database, "FAILED", checkpoint_id)
            except Exception:
                logging.exception("CRITICAL: cannot persist staging FAILED phase")
            raise HTTPException(
                503,
                "Ripristino non completato e rollback non verificato. Staging bloccato: "
                "non ritentare il caricamento; recuperare il checkpoint tramite procedura di emergenza.",
            ) from exc
        raise HTTPException(
            503,
            "Ripristino non completato; stato precedente recuperato e verificato. "
            "Non riprovare finché la causa non è stata corretta.",
        ) from exc


async def recover_staging_restore(*, database, load_checkpoint):
    """Explicit recovery endpoint: only while a failed/incomplete job exists."""
    state = await restore_status(database)
    if state["phase"] not in BLOCKING_PHASES:
        raise HTTPException(409, "Nessun ripristino incompleto da recuperare")
    checkpoint_id = state.get("checkpoint_id")
    if state["phase"] == "PREPARING" and not checkpoint_id:
        # No operational writes start until RESTORING.
        await _update_status(database, "READY", checkpoint_id="")
        return {"ok": True, "restored": False}
    if not checkpoint_id:
        raise HTTPException(503, "Checkpoint assente: recupero manuale necessario")
    await _update_status(database, "ROLLING_BACK", checkpoint_id)
    try:
        await _restore_checkpoint(database, checkpoint_id, load_checkpoint)
    except Exception as exc:
        await _update_status(database, "FAILED", checkpoint_id)
        raise HTTPException(503, "Recupero fallito: staging resta bloccato") from exc
    await _update_status(database, "READY", checkpoint_id)
    return {"ok": True, "restored": True, "checkpoint_id": checkpoint_id}

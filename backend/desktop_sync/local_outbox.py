"""Desktop sale outbox: transactional write-ahead receipt for local-only sync.

This is not a cloud sender. Uses a device identity fixed for one installation,
and requires an existing Mongo session. No transactions -> no sale.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import uuid
from typing import Any, Mapping

from fastapi import HTTPException


def payload_hash(payload: Mapping[str, Any]) -> str:
    try:
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("Non-canonical desktop payload") from exc
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def sale_payload(*, data: str, codice: str, quantita: int,
                 importo: Any, canale: str, pagamento: str, colonna: str | None) -> dict[str, Any]:
    try:
        cents = Decimal(str(importo)) * 100
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid monetary amount") from exc
    if not cents.is_finite() or cents != cents.to_integral_value():
        raise ValueError("Sale amount must have exact integer cents")
    return {
        "data": data, "codice": codice, "quantita": quantita,
        "importo_cents": int(cents), "canale": canale,
        "pagamento": pagamento, "colonna": colonna,
    }


def operation_id(device_id: str, client_key: str) -> str:
    try:
        namespace = uuid.UUID(device_id)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid desktop device UUID") from exc
    return str(uuid.uuid5(namespace, client_key))


async def check_sale_replay(db: Any, *, op_id: str, digest: str,
                            session: Any = None) -> dict[str, Any] | None:
    if session is None:
        raise RuntimeError("Desktop sale replay requires an active transaction")
    record = await db.desktop_outbox.find_one({"operation_id": op_id},
                                               {"_id": 0}, session=session)
    if not record:
        return None
    if record["payload_hash"] != digest:
        raise HTTPException(409, "Chiave operazione già utilizzata con dati differenti")
    result = await db.vendite.find_one({"id": record["entity_id"]},
                                       {"_id": 0}, session=session)
    if not result:
        raise HTTPException(409, "Ricevuta offline presente senza vendita: riconciliazione necessaria")
    return result


async def record_sale_outbox(db: Any, *, session: Any, op_id: str,
                             device_id: str, digest: str, sale_id: str,
                             event_payload: Mapping[str, Any]) -> None:
    if session is None:
        raise RuntimeError("A real MongoDB transaction is mandatory for offline sale")
    await db.desktop_outbox.insert_one({
        "operation_id": op_id,
        "device_id": device_id,
        "entity_kind": "sale",
        "entity_id": sale_id,
        "kind": "sale.create",
        "payload_hash": digest,
        "event_payload": dict(event_payload),
        "schema_version": 1,
        "status": "PENDING",
        "attempts": 0,
        "server_cursor": None,
    }, session=session)


async def create_indexes(db: Any) -> None:
    await db.desktop_outbox.create_index("operation_id", unique=True)
    await db.desktop_outbox.create_index([("status", 1), ("device_id", 1)])

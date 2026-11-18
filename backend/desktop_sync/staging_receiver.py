"""Staging-only durable RECEIPT ledger. Never changes business stock or sales.

STAGED means received by an isolated server, NOT applied to accounting. A
future authoritative service must resolve stock ownership and revisions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
from typing import Any, Mapping
from uuid import UUID

from pymongo.errors import DuplicateKeyError

from .local_outbox import payload_hash

_PAYLOAD_FIELDS = frozenset({
    "data", "codice", "quantita", "importo_cents", "canale",
    "pagamento", "colonna", "sale_id", "created_at",
})
_RECORD_FIELDS = frozenset({
    "operation_id", "device_id", "entity_kind", "entity_id", "kind",
    "payload_hash", "event_payload", "schema_version",
})


class StagingConflict(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def canonical_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("UUID must be a string")
    try:
        canonical = str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Invalid UUID") from exc
    if canonical != value:
        raise ValueError("Noncanonical UUID")
    return value


def validate_event(record: Mapping[str, Any], expected_device: str) -> dict:
    """Strictly validate the immutable local outbox snapshot and its digest."""
    if not isinstance(record, Mapping) or set(record) != _RECORD_FIELDS:
        raise ValueError("Unsupported staging record shape")
    canonical_uuid(record["operation_id"])
    if canonical_uuid(record["device_id"]) != canonical_uuid(expected_device):
        raise ValueError("Unregistered device")
    if record["entity_kind"] != "sale" or record["kind"] != "sale.create":
        raise ValueError("Only manual sale.create is allowed in staging")
    if record["schema_version"] != 1 or type(record["schema_version"]) is not int:
        raise ValueError("Unsupported staging schema")
    payload = record["event_payload"]
    if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_FIELDS:
        raise ValueError("Unsupported event payload shape")
    canonical_uuid(payload["sale_id"])
    if record["entity_id"] != payload["sale_id"]:
        raise ValueError("Entity ID does not match sale ID")
    if not isinstance(payload["data"], str) or not 1 <= len(payload["data"]) <= 40:
        raise ValueError("Invalid sale date")
    if not isinstance(payload["codice"], str) or not 1 <= len(payload["codice"]) <= 80:
        raise ValueError("Invalid product code")
    if type(payload["quantita"]) is not int or not 1 <= payload["quantita"] <= 100000:
        raise ValueError("Invalid sale quantity")
    if type(payload["importo_cents"]) is not int or not 0 <= payload["importo_cents"] <= 99999999999999:
        raise ValueError("Invalid cent amount")
    if payload["canale"] != "NEGOZIO" or payload["colonna"] is not None:
        raise ValueError("Vending events are not supported")
    if not isinstance(payload["pagamento"], str) or not 1 <= len(payload["pagamento"]) <= 40:
        raise ValueError("Invalid payment label")
    if not isinstance(payload["created_at"], str) or len(payload["created_at"]) > 70:
        raise ValueError("Invalid creation timestamp")
    try:
        parsed = datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timestamp must contain a timezone")
    except ValueError as exc:
        raise ValueError("Invalid creation timestamp") from exc
    digest = record["payload_hash"]
    if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        raise ValueError("Invalid payload hash")
    core = {field: value for field, value in payload.items() if field not in {"sale_id", "created_at"}}
    if not secrets.compare_digest(payload_hash(core), digest):
        raise ValueError("Outbox payload fingerprint mismatch")
    return {**record, "event_payload": dict(payload)}


def _immutable_record_digest(record: Mapping[str, Any]) -> str:
    """Hash all wire fields, not only sale monetary fields. Fails closed on mutations."""
    canonical = json.dumps(dict(record), sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class StagingReceiver:
    """One explicitly registered staging device + isolated workspace only."""

    def __init__(self, db: Any, client: Any, *, workspace_id: str, device_id: str):
        self.db = db
        self.client = client
        self.workspace_id = canonical_uuid(workspace_id)
        self.device_id = canonical_uuid(device_id)
        if not db.name.startswith("desktop_sync_staging_"):
            raise RuntimeError("Refusing non-staging database name")

    async def prepare(self) -> None:
        hello = await self.client.admin.command("hello")
        if not hello.get("isWritablePrimary") or not hello.get("setName") or hello.get("logicalSessionTimeoutMinutes") is None:
            raise RuntimeError("Staging requires a writable MongoDB replica set with transactions")
        marker = await self.db.sync_metadata.find_one({"_id": "purpose"})
        if not marker or marker.get("value") != "desktop-sync-staging":
            raise RuntimeError("Staging purpose marker absent; refusing to initialize indexes")
        await self.db.sync_receipts.create_index(
            [("workspace_id", 1), ("operation_id", 1)], unique=True)
        await self.db.sync_receipts.create_index(
            [("workspace_id", 1), ("sale_id", 1)], unique=True)
        await self.db.sync_receipts.create_index(
            [("workspace_id", 1), ("device_id", 1), ("status", 1)])

    def _response(self, record: Mapping[str, Any], *, replay: bool = False) -> dict:
        return {
            "operation_id": record["operation_id"],
            "status": "STAGED",
            "business_applied": False,
            "replayed": replay,
        }

    def _compare(self, existing: Mapping[str, Any] | None, record: Mapping[str, Any]) -> dict | None:
        if existing is None:
            return None
        if (existing["device_id"] == record["device_id"] and
            existing["sale_id"] == record["entity_id"] and
            isinstance(existing.get("record_digest"), str) and
            secrets.compare_digest(existing["record_digest"], _immutable_record_digest(record))):
            return self._response(existing, replay=True)
        raise StagingConflict("OPERATION_ID_REUSED_WITH_DIFFERENT_CONTENT")

    async def stage(self, raw: Mapping[str, Any]) -> dict:
        record = validate_event(raw, self.device_id)
        coll = self.db.sync_receipts
        by_operation = {"workspace_id": self.workspace_id, "operation_id": record["operation_id"]}
        by_sale = {"workspace_id": self.workspace_id, "sale_id": record["entity_id"]}

        async def transaction(session):
            existing = await coll.find_one(by_operation, session=session)
            replay = self._compare(existing, record)
            if replay is not None:
                return replay
            if await coll.find_one(by_sale, session=session):
                raise StagingConflict("SALE_ID_ALREADY_USED_BY_ANOTHER_OPERATION")
            await coll.insert_one({
                "workspace_id": self.workspace_id,
                "device_id": record["device_id"],
                "operation_id": record["operation_id"],
                "sale_id": record["entity_id"],
                "kind": record["kind"],
                "schema_version": record["schema_version"],
                "payload_hash": record["payload_hash"],
                "record_digest": _immutable_record_digest(record),
                "event_payload": record["event_payload"],
                "status": "STAGED",
                "business_applied": False,
                "received_at": datetime.now(timezone.utc),
            }, session=session)
            return self._response(record)

        try:
            async with await self.client.start_session() as session:
                return await session.with_transaction(transaction)
        except DuplicateKeyError:
            # A concurrent transaction may have committed first.
            existing = await coll.find_one(by_operation)
            replay = self._compare(existing, record)
            if replay is not None:
                return replay
            if await coll.find_one(by_sale):
                raise StagingConflict("SALE_ID_ALREADY_USED_BY_ANOTHER_OPERATION")
            raise

    async def receipt(self, operation_id: str) -> dict | None:
        canonical_uuid(operation_id)
        row = await self.db.sync_receipts.find_one({
            "workspace_id": self.workspace_id,
            "device_id": self.device_id,
            "operation_id": operation_id,
        })
        return self._response(row, replay=True) if row else None

    async def status(self) -> dict:
        count = await self.db.sync_receipts.count_documents({
            "workspace_id": self.workspace_id,
            "device_id": self.device_id,
            "status": "STAGED",
        })
        return {"staged": count, "business_applied": 0, "mode": "RECEIPT_ONLY"}

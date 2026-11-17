"""Fail-closed synchronization contract for a future desktop/cloud transport.

This module is deliberately pure. A caller MUST apply accepted operations in a
single MongoDB transaction with a unique operation ID, expected entity revision,
and an atomic revision increment. Decisions alone do not reserve a revision.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Sequence
from uuid import UUID


class SyncDecision(str, Enum):
    ACCEPT = "accept"
    ALREADY_APPLIED = "already_applied"
    CONFLICT = "conflict"
    REJECT = "reject"


# High-risk mutations must never use timestamp-based last-write-wins.
PROTECTED_KINDS = frozenset({
    "sale.create", "sale.reverse", "stock.receive", "stock.adjust",
    "vending.restock", "vending.withdraw", "cash.movement",
    "supplier_order.confirm", "supplier_order.cancel",
    "supplier_order.receive", "accounting.adjust",
})


@dataclass(frozen=True)
class SyncEnvelope:
    operation_id: str
    device_id: str
    entity_kind: str
    entity_id: str
    kind: str
    base_revision: int
    payload: Mapping[str, Any]
    schema_version: int = 1

    def __post_init__(self) -> None:
        for name in ("operation_id", "device_id"):
            try:
                UUID(getattr(self, name))
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError(f"{name} must be a UUID") from exc
        if not self.entity_kind or not self.entity_id or not self.kind:
            raise ValueError("entity_kind, entity_id and kind are mandatory")
        if type(self.base_revision) is not int or self.base_revision < 0:
            raise ValueError("base_revision must be a nonnegative integer")
        if self.schema_version != 1:
            raise ValueError("unsupported sync schema version")
        if not isinstance(self.payload, Mapping):
            raise ValueError("payload must be a mapping")


def canonical_digest(operation: SyncEnvelope) -> str:
    """Immutable fingerprint; rejects floats/NaN and unsupported value types."""
    document = {
        "operation_id": operation.operation_id,
        "device_id": operation.device_id,
        "entity_kind": operation.entity_kind,
        "entity_id": operation.entity_id,
        "kind": operation.kind,
        "base_revision": operation.base_revision,
        "schema_version": operation.schema_version,
        "payload": operation.payload,
    }
    try:
        wire = json.dumps(document, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("payload is not canonical JSON") from exc
    # Money must be represented by integer cents or decimal strings, not floats.
    def reject_float(value: Any) -> None:
        if isinstance(value, float):
            raise ValueError("floating-point payload values are forbidden")
        if isinstance(value, dict):
            for item in value.values():
                reject_float(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                reject_float(item)
    reject_float(operation.payload)
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def decide_incoming(
    operation: SyncEnvelope,
    *,
    current_revision: int | None,
    existing_digest: str | None,
    writer_authorized: bool,
) -> tuple[SyncDecision, str]:
    """Decision is only a preflight; enforce again inside a DB transaction.

    Existing operation IDs cannot be reused with modified payloads. An offline
    operation built against a stale entity is quarantined, never auto-merged.
    """
    digest = canonical_digest(operation)
    if existing_digest is not None:
        if existing_digest == digest:
            return SyncDecision.ALREADY_APPLIED, "same operation already committed"
        return SyncDecision.REJECT, "operation ID reused with different content"
    if not writer_authorized:
        return SyncDecision.REJECT, "device lacks write authorization"
    if operation.kind not in PROTECTED_KINDS:
        return SyncDecision.REJECT, "operation kind not explicitly allowlisted"
    if current_revision is None:
        if operation.base_revision != 0:
            return SyncDecision.CONFLICT, "missing entity with nonzero base revision"
    elif operation.base_revision != current_revision:
        return SyncDecision.CONFLICT, "stale entity revision; manual reconciliation required"
    return SyncDecision.ACCEPT, "preflight only: commit transactionally"


def validate_batch(operations: Sequence[SyncEnvelope]) -> None:
    """Detect duplicate IDs within a transport batch before any writes."""
    seen: dict[str, str] = {}
    for operation in operations:
        digest = canonical_digest(operation)
        previous = seen.get(operation.operation_id)
        if previous is not None and previous != digest:
            raise ValueError("batch contains conflicting operation IDs")
        if previous is not None:
            raise ValueError("batch contains duplicate operation IDs")
        seen[operation.operation_id] = digest

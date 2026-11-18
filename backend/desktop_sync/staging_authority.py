"""Gate 3C laboratory-only escrow and transactional sales projection.

NEVER registers against backend.server. NEVER posts production business sales.
All mutations require a separately marked disposable rsDesktopCert database.
The ordinary staging receipt remains STAGED even after lab projection: it is
NOT a cloud or accounting ACK.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pymongo.errors import DuplicateKeyError

from .staging_receiver import StagingConflict, canonical_uuid


class LaboratoryConflict(StagingConflict):
    """A manual-reconciliation requirement; not a retriable transient error."""


def _positive_integer(value: Any, *, allow_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise ValueError("Quantity must be a positive integer")
    return value


def _code(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 80 or not value.strip():
        raise ValueError("Invalid product code")
    return value


class EscrowLaboratory:
    """Exclusive, separated staging projection. Explicit operator invocation only.

    Inventory invariant for every (workspace, product):
      initial_stock = unallocated + sum(device_grants.remaining + device_grants.consumed)

    'consumed' here records a *lab projection*; business inventory is untouched.
    """

    def __init__(self, db: Any, client: Any, *, workspace_id: str):
        self.db = db
        self.client = client
        self.workspace_id = canonical_uuid(workspace_id)
        if not db.name.startswith("desktop_sync_staging_"):
            raise RuntimeError("Laboratory requires a namespaced staging DB")

    async def prepare(self) -> None:
        hello = await self.client.admin.command("hello")
        if (hello.get("setName") != "rsDesktopCert"
            or not hello.get("isWritablePrimary")
            or hello.get("logicalSessionTimeoutMinutes") is None):
            raise RuntimeError("Escrow laboratory ONLY accepts disposable rsDesktopCert primary")
        purpose = await self.db.sync_metadata.find_one({"_id": "purpose"})
        lab = await self.db.sync_metadata.find_one({"_id": "escrow-lab-opt-in"})
        if (not purpose or purpose.get("value") != "desktop-sync-staging"
            or not lab or lab.get("value") != "escrow-lab-only"):
            raise RuntimeError("Manual staging escrow opt-in markers required")
        await self.db.lab_inventory.create_index(
            [("workspace_id", 1), ("codice", 1)], unique=True)
        await self.db.lab_grants.create_index(
            [("workspace_id", 1), ("device_id", 1), ("codice", 1)], unique=True)
        await self.db.lab_sales.create_index(
            [("workspace_id", 1), ("operation_id", 1)], unique=True)
        await self.db.lab_sales.create_index(
            [("workspace_id", 1), ("sale_id", 1)], unique=True)
        await self.db.lab_conflicts.create_index(
            [("workspace_id", 1), ("operation_id", 1)], unique=True)

    def _inventory_key(self, code: str) -> dict:
        return {"workspace_id": self.workspace_id, "codice": code}

    def _grant_key(self, device: str, code: str) -> dict:
        return {**self._inventory_key(code), "device_id": device}

    async def seed_inventory(self, code: str, quantity: int) -> dict:
        """One-time *lab* initialization, never migration from production."""
        code = _code(code)
        quantity = _positive_integer(quantity, allow_zero=True)
        key = self._inventory_key(code)
        async def tx(session):
            existing = await self.db.lab_inventory.find_one(key, session=session)
            if existing is not None:
                if existing["initial_stock"] == quantity:
                    return {"codice": code, "initial_stock": quantity, "replayed": True}
                raise LaboratoryConflict("LAB_INVENTORY_ALREADY_INITIALIZED")
            await self.db.lab_inventory.insert_one({
                **key, "initial_stock": quantity, "unallocated": quantity,
            }, session=session)
            return {"codice": code, "initial_stock": quantity, "replayed": False}
        async with await self.client.start_session() as session:
            return await session.with_transaction(tx)

    async def allocate(self, device_id: str, code: str, quantity: int) -> dict:
        """Allocate previously unassigned stock to a specific offline device."""
        device_id = canonical_uuid(device_id)
        code = _code(code)
        quantity = _positive_integer(quantity)
        key = self._inventory_key(code)
        async def tx(session):
            updated = await self.db.lab_inventory.update_one(
                {**key, "unallocated": {"$gte": quantity}},
                {"$inc": {"unallocated": -quantity}}, session=session)
            if updated.modified_count != 1:
                raise LaboratoryConflict("LAB_STOCK_ALLOCATION_EXCEEDS_POOL")
            await self.db.lab_grants.update_one(
                self._grant_key(device_id, code),
                {"$inc": {"granted": quantity, "remaining": quantity},
                 "$setOnInsert": {"consumed": 0}},
                upsert=True, session=session,
            )
            return {"device_id": device_id, "codice": code,
                    "allocated": quantity, "business_applied": False}
        async with await self.client.start_session() as session:
            return await session.with_transaction(tx)

    async def _record_conflict(self, row: dict, reason: str) -> None:
        """Persist a blocked result without changing STAGED or any stock."""
        key = {"workspace_id": self.workspace_id, "operation_id": row["operation_id"]}
        await self.db.lab_conflicts.update_one(
            key,
            {"$setOnInsert": {
                **key, "device_id": row["device_id"],
                "sale_id": row["sale_id"], "reason": reason,
                "status": "OPEN", "created_at": datetime.now(timezone.utc),
            }}, upsert=True,
        )

    async def apply_staged(self, operation_id: str) -> dict:
        """Manual lab-only projection; never changes real stock/cash/sales.

        Transactions atomically consume escrow + insert lab sale. An operation
        whose conflict is OPEN is not retried without future manual resolution.
        """
        operation_id = canonical_uuid(operation_id)
        key = {"workspace_id": self.workspace_id, "operation_id": operation_id}
        async def tx(session):
            row = await self.db.sync_receipts.find_one(key, session=session)
            if not row or row.get("status") != "STAGED" or row.get("business_applied") is not False:
                raise LaboratoryConflict("LAB_RECEIPT_NOT_STAGED")
            if row.get("kind") != "sale.create":
                raise LaboratoryConflict("LAB_UNSUPPORTED_OPERATION")
            existing = await self.db.lab_sales.find_one(key, session=session)
            if existing:
                if existing["record_digest"] != row["record_digest"]:
                    raise LaboratoryConflict("LAB_OPERATION_REUSED")
                return {"operation_id": operation_id, "lab_applied": True,
                        "business_applied": False, "replayed": True}
            conflict = await self.db.lab_conflicts.find_one(key, session=session)
            if conflict and conflict.get("status") == "OPEN":
                raise LaboratoryConflict("LAB_OPEN_CONFLICT_REQUIRES_RECONCILIATION")
            p = row["event_payload"]
            quantity = _positive_integer(p.get("quantita"))
            code = _code(p.get("codice"))
            device = canonical_uuid(row["device_id"])
            grant = await self.db.lab_grants.update_one(
                {**self._grant_key(device, code), "remaining": {"$gte": quantity}},
                {"$inc": {"remaining": -quantity, "consumed": quantity}},
                session=session,
            )
            if grant.modified_count != 1:
                raise LaboratoryConflict("LAB_DEVICE_ESCROW_EXHAUSTED")
            await self.db.lab_sales.insert_one({
                **key, "device_id": device, "sale_id": row["sale_id"],
                "record_digest": row["record_digest"], "codice": code,
                "quantity": quantity, "amount_cents": p["importo_cents"],
                "applied_at": datetime.now(timezone.utc),
            }, session=session)
            # Do NOT change the staging receipt to ACK; that would falsely
            # imply real booking in the business ledger.
            return {"operation_id": operation_id, "lab_applied": True,
                    "business_applied": False, "replayed": False}
        try:
            async with await self.client.start_session() as session:
                return await session.with_transaction(tx)
        except LaboratoryConflict as exc:
            if exc.reason == "LAB_DEVICE_ESCROW_EXHAUSTED":
                row = await self.db.sync_receipts.find_one(key)
                if row:
                    await self._record_conflict(row, exc.reason)
            raise

    async def stock_report(self, code: str) -> dict:
        """Read-only audit of the escrow conservation invariant."""
        code = _code(code)
        key = self._inventory_key(code)
        pool = await self.db.lab_inventory.find_one(key)
        if pool is None:
            raise LaboratoryConflict("LAB_UNKNOWN_PRODUCT")
        grants = await self.db.lab_grants.find(key).to_list(None)
        reserved = sum(x["remaining"] for x in grants)
        consumed = sum(x["consumed"] for x in grants)
        lhs = pool["initial_stock"]
        rhs = pool["unallocated"] + reserved + consumed
        if lhs != rhs:
            raise LaboratoryConflict("LAB_ESCROW_INVARIANT_BROKEN")
        return {"codice": code, "initial": lhs, "unallocated": pool["unallocated"],
                "reserved": reserved, "consumed": consumed, "balanced": True,
                "business_applied": False}

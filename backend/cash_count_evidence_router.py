"""Staging-only append-only physical cash count observations.

Observations do NOT change booked cash, stock, sales or opening balances.
They are evidence requiring subsequent independent reconciliation.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
import re
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictInt, field_validator
from pymongo.errors import DuplicateKeyError

EURO_CENT_DENOMINATIONS = {
    "0.01": 1, "0.02": 2, "0.05": 5, "0.10": 10,
    "0.20": 20, "0.50": 50, "1": 100, "2": 200,
    "5": 500, "10": 1000, "20": 2000, "50": 5000,
    "100": 10000, "200": 20000, "500": 50000,
}


class CashCountEvidenceIn(BaseModel):
    idempotency_key: str = Field(min_length=12, max_length=100)
    observed_at: datetime
    kind: Literal["opening", "closing", "spot"]
    denominations: dict[str, StrictInt]
    source_reference: str = Field(min_length=4, max_length=120)
    note: str = Field(default="", max_length=500)

    @field_validator("idempotency_key")
    @classmethod
    def valid_key(cls, value):
        if not re.fullmatch(r"[a-zA-Z0-9-]{12,100}", value):
            raise ValueError("Invalid idempotency key")
        return value

    @field_validator("denominations")
    @classmethod
    def valid_denominations(cls, value):
        if not value:
            raise ValueError("At least one denomination is required")
        if not set(value) <= set(EURO_CENT_DENOMINATIONS):
            raise ValueError("Unknown denomination")
        if any(type(count) is not int or count < 0 or count > 10000 for count in value.values()):
            raise ValueError("Counts must be bounded nonnegative integers")
        return value

    @field_validator("source_reference")
    @classmethod
    def valid_reference(cls, value):
        normalized = value.strip()
        if len(normalized) < 4:
            raise ValueError("Signed cash count reference is required")
        return normalized

    @field_validator("observed_at")
    @classmethod
    def valid_observation_time(cls, value):
        if not value.tzinfo or value.utcoffset() is None:
            raise ValueError("Timezone-aware observation time required")
        now = datetime.now(timezone.utc)
        instant = value.astimezone(timezone.utc)
        if instant > now + timedelta(minutes=5) or instant < now - timedelta(days=2):
            raise ValueError("Cash count time must be recent and not in the future")
        return instant


def _cash_total_cents(denominations):
    return sum(EURO_CENT_DENOMINATIONS[d] * count for d, count in denominations.items())


def _payload_digest(data, operator):
    document = {
        "observed_at": data.observed_at.astimezone(timezone.utc).isoformat(),
        "kind": data.kind,
        "denominations": data.denominations,
        "source_reference": data.source_reference.strip(),
        "note": data.note.strip(),
        "operator": operator,
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def create_cash_count_evidence_router(*, get_database, run_transactionally,
                                      request_operator, enabled=False):
    router = APIRouter()
    if not enabled:
        return router

    @router.get("/contabilita/conteggi-fisici")
    async def list_cash_counts(limit: int = 30):
        if not 1 <= limit <= 100:
            raise HTTPException(422, "Limit must be between 1 and 100")
        return await get_database().cash_count_evidence.find(
            {}, {"_id": 0},
        ).sort("created_at", -1).limit(limit).to_list(limit)

    @router.post("/contabilita/conteggi-fisici", status_code=201)
    async def append_cash_count(data: CashCountEvidenceIn, request: Request):
        database = get_database()
        operator = request_operator(request)
        digest = _payload_digest(data, operator)
        identifier = f"cash-count:{data.idempotency_key}"
        now = datetime.now(timezone.utc).isoformat()
        new_document = {
            "_id": identifier, "id": str(uuid.uuid4()),
            "idempotency_key": data.idempotency_key,
            "request_fingerprint": digest,
            "observed_at": data.observed_at.isoformat(),
            "kind": data.kind,
            "denominations": dict(sorted(data.denominations.items())),
            "total_cents": _cash_total_cents(data.denominations),
            "source_reference": data.source_reference.strip(),
            "note": data.note.strip(),
            "operator": operator,
            "created_at": now,
            "is_financial_baseline": False,
            "changes_booked_cash": False,
        }

        async def operation(session):
            kwargs = {"session": session} if session is not None else {}
            prior = await database.cash_count_evidence.find_one(
                {"_id": identifier}, **kwargs,
            )
            if prior:
                if prior.get("request_fingerprint") != digest:
                    raise HTTPException(409, "Count key already used with a different payload")
                prior.pop("_id", None)
                return {**prior, "replayed": True}
            await database.cash_count_evidence.insert_one(
                dict(new_document), **kwargs,
            )
            await database.audit_events.insert_one({
                "_id": f"cash-count-audit:{data.idempotency_key}",
                "id": str(uuid.uuid4()), "created_at": now,
                "azione": "REGISTRATO_CONTEGGIO_FISICO",
                "entita": "CONTEGGIO_CASSA_FISICO", "entita_id": new_document["id"],
                "operatore": operator, "prima": None,
                "dopo": {
                    "count_id": new_document["id"],
                    "total_cents": new_document["total_cents"],
                    "source_reference": new_document["source_reference"],
                },
                "idempotency_key": data.idempotency_key,
            }, **kwargs)
            result = {k: v for k, v in new_document.items() if k != "_id"}
            return {**result, "replayed": False}

        try:
            return await run_transactionally(operation)
        except DuplicateKeyError:
            prior = await database.cash_count_evidence.find_one({"_id": identifier}, {"_id": 0})
            if prior and prior.get("request_fingerprint") == digest:
                return {**prior, "replayed": True}
            raise HTTPException(409, "Count key conflicts with an existing observation") from None

    return router

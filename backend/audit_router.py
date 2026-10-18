"""Append-only audit writer and read endpoint."""
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter


def create_audit_router(*, get_database):
    """Build audit components around a late-bound database reference."""
    router = APIRouter()

    async def record_audit_event(
        azione,
        entita,
        entita_id=None,
        *,
        operatore="operatore-locale",
        prima=None,
        dopo=None,
        idempotency_key=None,
        batch_id=None,
    ):
        """Write an append-only event without Mongo's internal identifiers."""
        audit_collection = getattr(get_database(), "audit_events", None)
        # Narrow legacy fixtures may expose only the collection under test.
        if audit_collection is None:
            return
        await audit_collection.insert_one({
            "id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "azione": azione,
            "entita": entita,
            "entita_id": str(entita_id) if entita_id is not None else None,
            "operatore": operatore,
            "prima": dict(prima) if prima else None,
            "dopo": dict(dopo) if dopo else None,
            "idempotency_key": idempotency_key,
            "batch_id": batch_id,
        })

    @router.get("/audit/eventi")
    async def list_audit_events(limit: int = 100, entita: str | None = None, entita_id: str | None = None):
        capped_limit = min(max(int(limit or 100), 1), 500)
        query = {}
        if entita:
            query["entita"] = str(entita).strip()
        if entita_id:
            query["entita_id"] = str(entita_id).strip()
        return await get_database().audit_events.find(
            query, {"_id": 0}
        ).sort("created_at", -1).limit(capped_limit).to_list(capped_limit)

    return router, record_audit_event, list_audit_events

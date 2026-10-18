"""HTTP routes and workflow persistence for operational reconciliation."""
from datetime import datetime, timezone
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

if __package__:
    from .reconciliation import reconcile
else:
    from reconciliation import reconcile


class ReconciliationCaseIn(BaseModel):
    stato: str = Field(pattern=r"^(aperta|in_verifica|risolta|ignorata)$")
    nota: str = Field(default="", max_length=500)
    tipo: str = Field(default="", max_length=100)
    raccolta: str = Field(default="", max_length=100)
    riferimento: str = Field(default="", max_length=300)


def create_reconciliation_router(
    *,
    get_database,
    payment_bucket,
    request_operator,
    record_audit_event,
):
    """Build the router without importing the monolithic application module."""
    router = APIRouter()

    @router.get("/contabilita/riconciliazione")
    async def accounting_reconciliation(date_from: str | None = None, date_to: str | None = None):
        for value, label in ((date_from, "date_from"), (date_to, "date_to")):
            if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise HTTPException(422, f"{label} deve avere formato YYYY-MM-DD")
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from non può essere successiva a date_to")

        database = get_database()
        cases = await database.reconciliation_cases.find({}, {"_id": 0}).to_list(None)
        case_states = {item["fingerprint"]: item for item in cases if item.get("fingerprint")}
        return await reconcile(
            database,
            payment_bucket,
            date_from=date_from,
            date_to=date_to,
            case_states=case_states,
        )

    @router.put("/contabilita/riconciliazione/segnalazioni/{fingerprint}")
    async def update_reconciliation_case(fingerprint: str, body: ReconciliationCaseIn, request: Request):
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise HTTPException(422, "Identificativo segnalazione non valido")

        database = get_database()
        now = datetime.now(timezone.utc).isoformat()
        operator = request_operator(request)
        before = await database.reconciliation_cases.find_one(
            {"fingerprint": fingerprint}, {"_id": 0}
        )
        document = {
            "fingerprint": fingerprint,
            "stato": body.stato,
            "nota": body.nota.strip(),
            "tipo": body.tipo.strip(),
            "raccolta": body.raccolta.strip(),
            "riferimento": body.riferimento.strip(),
            "operatore": operator,
            "updated_at": now,
        }
        await database.reconciliation_cases.update_one(
            {"fingerprint": fingerprint},
            {"$set": document, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        saved = await database.reconciliation_cases.find_one(
            {"fingerprint": fingerprint}, {"_id": 0}
        )
        await record_audit_event(
            "AGGIORNATO",
            "SEGNALAZIONE_RICONCILIAZIONE",
            fingerprint,
            operatore=operator,
            prima=before,
            dopo=saved,
        )
        return saved

    return router, accounting_reconciliation, update_reconciliation_case

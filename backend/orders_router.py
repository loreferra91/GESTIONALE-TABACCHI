"""Manual merchandise receipt history and Smart Venue confirmation routes."""
from datetime import datetime, timezone
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

try:
    from .money import NonNegativeMoney
except ImportError:  # pragma: no cover - direct module execution compatibility
    from money import NonNegativeMoney


class OrdineStorico(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: str
    file_sorgente: str = ""
    codice: str
    descrizione: str = ""
    quantita: int
    prezzo: NonNegativeMoney = 0
    batch_id: Optional[str] = None
    smart_venue_applicabile: bool = False
    caricato_smart_venue: Optional[bool] = None
    caricato_smart_venue_il: Optional[str] = None
    caricato_smart_venue_da: Optional[str] = None
    operatore: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class OrdineIn(BaseModel):
    data: str
    codice: str
    descrizione: Optional[str] = ""
    quantita: int
    prezzo: NonNegativeMoney = 0
    file_sorgente: Optional[str] = "manuale"


def create_orders_router(
    *,
    get_database,
    cap_limit,
    request_operator,
    increment_smart_venue_for_receipt,
):
    router = APIRouter()

    @router.get("/ordini")
    async def list_ordini(limit: int = 1000):
        capped_limit = cap_limit(limit)
        return await get_database().storico_ordini.find(
            {}, {"_id": 0}
        ).sort("data", -1).limit(capped_limit).to_list(capped_limit)

    @router.post("/ordini")
    async def add_ordine(order: OrdineIn, request: Request = None):
        if order.quantita <= 0:
            raise HTTPException(422, "La quantità deve essere maggiore di zero")
        database = get_database()
        smart_venue_applicable = bool(
            await database.smart_venue.find_one({"codice": order.codice}, {"_id": 1})
        )
        document = OrdineStorico(
            **order.model_dump(),
            smart_venue_applicabile=smart_venue_applicable,
            caricato_smart_venue=False if smart_venue_applicable else None,
            operatore=request_operator(request),
        ).model_dump()
        await database.storico_ordini.insert_one(document)
        if order.codice:
            await database.prodotti.update_one(
                {"codice": order.codice},
                {"$inc": {
                    "giacenza_negozio": order.quantita,
                    "acquistati": order.quantita,
                }},
            )
            await increment_smart_venue_for_receipt(order.codice, order.quantita)
        return document

    @router.post("/ordini/{order_id}/conferma-smart-venue")
    async def conferma_carico_smart_venue(
        order_id: str,
        request: Request = None,
    ):
        """Confirm only the external operation without changing stock."""
        database = get_database()
        current = await database.storico_ordini.find_one(
            {"id": order_id}, {"_id": 0}
        )
        if not current:
            raise HTTPException(404, "Movimento di carico non trovato")
        if not current.get("smart_venue_applicabile"):
            raise HTTPException(
                422,
                "Il prodotto di questo carico non è gestito in Smart Venue",
            )
        if current.get("caricato_smart_venue") is True:
            return {**current, "ok": True, "gia_confermato": True}

        now = datetime.now(timezone.utc).isoformat()
        result = await database.storico_ordini.update_one(
            {"id": order_id, "caricato_smart_venue": {"$ne": True}},
            {"$set": {
                "caricato_smart_venue": True,
                "caricato_smart_venue_il": now,
                "caricato_smart_venue_da": request_operator(request),
            }},
        )
        if result.modified_count != 1:
            latest = await database.storico_ordini.find_one(
                {"id": order_id}, {"_id": 0}
            )
            if latest and latest.get("caricato_smart_venue") is True:
                return {**latest, "ok": True, "gia_confermato": True}
            raise HTTPException(409, "Il movimento è cambiato nel frattempo")
        updated = await database.storico_ordini.find_one(
            {"id": order_id}, {"_id": 0}
        )
        return {**updated, "ok": True, "gia_confermato": False}

    @router.delete("/ordini/{order_id}")
    async def del_ordine(order_id: str):
        await get_database().storico_ordini.delete_one({"id": order_id})
        return {"ok": True}

    return {
        "router": router,
        "list_ordini": list_ordini,
        "add_ordine": add_ordine,
        "conferma_carico_smart_venue": conferma_carico_smart_venue,
        "del_ordine": del_ordine,
    }

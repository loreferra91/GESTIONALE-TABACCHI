"""Vending column catalogue, availability proposals, and manual corrections."""
import asyncio
from datetime import datetime, timezone
import re
import uuid
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class VendingColonna(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    colonna: str
    codice: str = ""
    descrizione: str = ""
    giacenza: int = 0
    capacita_max: int = 5
    soglia_minima: int = 2


class VendingCreateIn(BaseModel):
    colonna: str
    codice: str
    capacita_max: int = 5
    soglia_minima: int = 2
    giacenza_iniziale: int = 0


def create_vending_router(
    *,
    get_database,
    get_params,
    param_value,
    product_code_text,
    session_kwargs,
):
    router = APIRouter()

    @router.post("/vending")
    async def create_vending_column(payload: VendingCreateIn):
        column = payload.colonna.strip().upper()
        code = product_code_text(payload.codice)
        if not re.fullmatch(r"[A-Z]\d+", column):
            raise HTTPException(422, "La colonna deve avere un formato come A01")
        if payload.capacita_max <= 0:
            raise HTTPException(422, "La capacità deve essere maggiore di zero")
        if payload.soglia_minima < 0 or payload.soglia_minima > payload.capacita_max:
            raise HTTPException(
                422,
                "La soglia deve essere compresa tra zero e la capacità",
            )
        if payload.giacenza_iniziale < 0 or payload.giacenza_iniziale > payload.capacita_max:
            raise HTTPException(
                422,
                "La giacenza iniziale deve essere compresa tra zero e la capacità",
            )

        database = get_database()
        existing, product, params = await asyncio.gather(
            database.vending.find_one({"colonna": column}),
            database.prodotti.find_one({"codice": code}),
            get_params(),
        )
        if existing:
            raise HTTPException(409, f"Colonna vending già presente: {column}")
        if not product:
            raise HTTPException(404, f"Prodotto non trovato: {code}")

        reserve = max(
            0,
            int(param_value(params, "SCORTA_MINIMA_NEGOZIO_VENDING", 2)),
        )
        available = max(0, int(product.get("giacenza_negozio", 0) or 0))
        loadable = max(0, available - reserve)
        if payload.giacenza_iniziale > loadable:
            raise HTTPException(
                409,
                f"Scorta negozio protetta: giacenza iniziale {payload.giacenza_iniziale}, "
                f"caricabili {loadable} (disponibili {available}, riserva {reserve})",
            )

        now = datetime.now(timezone.utc).isoformat()
        document = VendingColonna(
            colonna=column,
            codice=code,
            descrizione=str(product.get("descrizione") or code),
            giacenza=payload.giacenza_iniziale,
            capacita_max=payload.capacita_max,
            soglia_minima=payload.soglia_minima,
        ).model_dump()
        document.update({
            "created_at": now,
            "giacenza_aggiornata_il": now,
            "giacenza_sorgente": "INSERIMENTO_MANUALE",
        })

        product_updated = False
        try:
            if payload.giacenza_iniziale:
                result = await database.prodotti.update_one(
                    {
                        "codice": code,
                        "giacenza_negozio": {
                            "$gte": payload.giacenza_iniziale + reserve
                        },
                    },
                    {"$inc": {
                        "giacenza_negozio": -payload.giacenza_iniziale,
                        "giacenza_vending": payload.giacenza_iniziale,
                    }},
                )
                if result.matched_count == 0:
                    raise RuntimeError("la disponibilità negozio è cambiata")
                product_updated = True
            if await database.vending.find_one({"colonna": column}):
                raise RuntimeError(
                    f"la colonna {column} è stata creata nel frattempo"
                )
            await database.vending.insert_one(dict(document))
        except Exception as exc:
            if product_updated:
                await database.prodotti.update_one(
                    {"codice": code},
                    {"$inc": {
                        "giacenza_negozio": payload.giacenza_iniziale,
                        "giacenza_vending": -payload.giacenza_iniziale,
                    }},
                )
            raise HTTPException(409, f"Inserimento vending annullato: {exc}")

        return {
            **document,
            "giacenza_magazzino": available - payload.giacenza_iniziale,
            "scorta_minima_negozio": reserve,
            "giacenza_caricabile": loadable - payload.giacenza_iniziale,
        }

    @router.get("/vending")
    async def list_vending():
        database = get_database()
        documents, products, params = await asyncio.gather(
            database.vending.find({}, {"_id": 0}).sort("colonna", 1).to_list(500),
            database.prodotti.find(
                {}, {"_id": 0, "codice": 1, "giacenza_negozio": 1}
            ).to_list(5000),
            get_params(),
        )
        reserve = max(
            0,
            int(param_value(params, "SCORTA_MINIMA_NEGOZIO_VENDING", 2)),
        )
        available_by_code = {
            product.get("codice"): max(
                0, int(product.get("giacenza_negozio", 0) or 0)
            )
            for product in products
            if product.get("codice")
        }
        remaining_loadable = {
            code: max(0, available - reserve)
            for code, available in available_by_code.items()
        }
        output = []
        for document in documents:
            capacity = document.get("capacita_max")
            capacity = 5 if capacity is None else int(capacity)
            stock = document.get("giacenza", 0) or 0
            threshold = document.get("soglia_minima")
            threshold = 2 if threshold is None else int(threshold)
            needed = max(0, capacity - stock)
            below_threshold = stock <= threshold
            code = document.get("codice")
            available = available_by_code.get(code, 0)
            total_loadable = max(0, available - reserve)
            residual = remaining_loadable.get(code, 0)
            proposal = (
                min(needed, residual)
                if below_threshold and needed > 0
                else 0
            )
            if proposal:
                remaining_loadable[code] = residual - proposal
            if stock >= capacity:
                outcome = "PIENO" if stock == capacity else "OLTRE CAPACITA"
            elif below_threshold and needed > 0:
                if proposal <= 0:
                    outcome = "SCORTA NEGOZIO"
                elif proposal < needed:
                    outcome = "CARICO PARZIALE"
                else:
                    outcome = "DA CARICARE"
            else:
                outcome = "OK"
            document.update({
                "giacenza_magazzino": available,
                "scorta_minima_negozio": reserve,
                "giacenza_caricabile": total_loadable,
                "fabbisogno": needed,
                "proposta": proposal,
                "esito": outcome,
            })
            output.append(document)
        return output

    async def sync_product_vending_stock(code: str, session=None) -> None:
        if not code:
            return
        database = get_database()
        kwargs = session_kwargs(session)
        columns = await database.vending.find(
            {"codice": code}, {"_id": 0, "giacenza": 1}, **kwargs
        ).to_list(500)
        total = sum(
            max(0, int(row.get("giacenza", 0) or 0)) for row in columns
        )
        await database.prodotti.update_one(
            {"codice": code}, {"$set": {"giacenza_vending": total}}, **kwargs
        )

    @router.put("/vending/{column_id}/giacenza")
    async def update_vending_giacenza(column_id: str, body: Dict[str, Any]):
        raw_value = body.get("giacenza")
        try:
            stock = int(raw_value)
        except (TypeError, ValueError):
            raise HTTPException(422, "Giacenza non valida")
        if isinstance(raw_value, bool) or (
            isinstance(raw_value, float) and not raw_value.is_integer()
        ):
            raise HTTPException(422, "La giacenza deve essere un numero intero")
        if isinstance(raw_value, str) and not re.fullmatch(
            r"[+-]?\d+", raw_value.strip()
        ):
            raise HTTPException(422, "La giacenza deve essere un numero intero")
        if stock < 0:
            raise HTTPException(422, "La giacenza non può essere negativa")

        database = get_database()
        current = await database.vending.find_one({"id": column_id})
        if not current:
            raise HTTPException(404, "Colonna vending non trovata")
        capacity = int(current.get("capacita_max", 0) or 0)
        if capacity > 0 and stock > capacity:
            raise HTTPException(
                422,
                f"La giacenza supera la capacità della colonna ({capacity})",
            )
        old_value = int(current.get("giacenza", 0) or 0)
        result = await database.vending.update_one(
            {"id": column_id, "giacenza": current.get("giacenza", 0)},
            {"$set": {
                "giacenza": stock,
                "giacenza_aggiornata_il": datetime.now(timezone.utc).isoformat(),
                "giacenza_sorgente": "CORREZIONE_MANUALE",
            }},
        )
        if result.matched_count == 0:
            raise HTTPException(
                409,
                "La giacenza è cambiata nel frattempo: aggiorna la pagina e riprova",
            )
        await sync_product_vending_stock(str(current.get("codice") or ""))
        return {
            "ok": True,
            "id": column_id,
            "colonna": current.get("colonna"),
            "giacenza_precedente": old_value,
            "giacenza": stock,
        }

    @router.put("/vending/{column_id}")
    async def update_vending(column_id: str, body: Dict[str, Any]):
        if "giacenza" in body:
            raise HTTPException(
                422,
                "Per correggere la giacenza usa l'endpoint dedicato /giacenza",
            )
        if "codice" in body:
            raise HTTPException(
                422,
                "Il codice prodotto della colonna può essere modificato solo tramite import controllato",
            )
        allowed = {
            key: body[key]
            for key in ("descrizione", "capacita_max", "soglia_minima")
            if key in body
        }
        database = get_database()
        current = await database.vending.find_one({"id": column_id})
        if not current:
            raise HTTPException(404, "not found")
        raw_capacity = allowed.get(
            "capacita_max", current.get("capacita_max", 5)
        )
        raw_threshold = allowed.get(
            "soglia_minima", current.get("soglia_minima", 2)
        )
        try:
            capacity = int(raw_capacity)
            threshold = int(raw_threshold)
        except (TypeError, ValueError):
            raise HTTPException(
                422,
                "Capacità e soglia devono essere numeri interi",
            )
        for value in (raw_capacity, raw_threshold):
            if isinstance(value, bool) or (
                isinstance(value, float) and not value.is_integer()
            ):
                raise HTTPException(
                    422,
                    "Capacità e soglia devono essere numeri interi",
                )
            if isinstance(value, str) and not re.fullmatch(
                r"[+-]?\d+", value.strip()
            ):
                raise HTTPException(
                    422,
                    "Capacità e soglia devono essere numeri interi",
                )
        if capacity <= 0:
            raise HTTPException(422, "La capacità deve essere maggiore di zero")
        if threshold < 0 or threshold > capacity:
            raise HTTPException(
                422,
                "La soglia deve essere compresa tra zero e la capacità",
            )
        if int(current.get("giacenza", 0) or 0) > capacity:
            raise HTTPException(
                422,
                "La capacità non può essere inferiore alla giacenza attuale",
            )
        if "capacita_max" in allowed:
            allowed["capacita_max"] = capacity
        if "soglia_minima" in allowed:
            allowed["soglia_minima"] = threshold
        result = await database.vending.update_one(
            {"id": column_id}, {"$set": allowed}
        )
        if result.matched_count == 0:
            raise HTTPException(404, "not found")
        return await database.vending.find_one({"id": column_id}, {"_id": 0})

    return {
        "router": router,
        "create_vending_column": create_vending_column,
        "list_vending": list_vending,
        "sync_product_vending_stock": sync_product_vending_stock,
        "update_vending_giacenza": update_vending_giacenza,
        "update_vending": update_vending,
    }

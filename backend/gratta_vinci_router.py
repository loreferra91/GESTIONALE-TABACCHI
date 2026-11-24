"""Independent scratch-card catalogue, stock and idempotent movement ledger.

This module never reads or writes the tobacco products, vending or cash ledgers.
All multi-document mutations use the server's MongoDB transaction helper.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError


ROME = ZoneInfo("Europe/Rome")


def _now():
    return datetime.now(timezone.utc)


def _clean(value: str) -> str:
    return value.strip()


def _code(value: str) -> str:
    return _clean(value).upper()


class GrattaVinciProductIn(BaseModel):
    codice: str = Field(min_length=1, max_length=64)
    descrizione: str = Field(min_length=1, max_length=200)
    prezzo: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    quantita_magazzino: int = Field(ge=0, le=1000000)

    @field_validator("codice", "descrizione")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not _clean(value):
            raise ValueError("Il campo non può essere vuoto")
        return _clean(value)


class GrattaVinciProductEdit(BaseModel):
    descrizione: str = Field(min_length=1, max_length=200)
    prezzo: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    version: int = Field(ge=0)

    @field_validator("descrizione")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if not _clean(value):
            raise ValueError("La descrizione è obbligatoria")
        return _clean(value)


class GrattaVinciMovementIn(BaseModel):
    codice: str = Field(min_length=1, max_length=64)
    quantita: int = Field(ge=1, le=1000000)
    idempotency_key: str = Field(min_length=10, max_length=160)

    @field_validator("codice", "idempotency_key")
    @classmethod
    def validate_required(cls, value: str) -> str:
        if not _clean(value):
            raise ValueError("Valore obbligatorio")
        return _clean(value)


def _cents(value: Decimal) -> int:
    # Validated decimal_places=2 ensures no fractional-cent rounding.
    return int(value * 100)


def create_gratta_vinci_router(*, get_database, run_transactionally, session_kwargs, request_operator):
    router = APIRouter(prefix="/gratta-e-vinci", tags=["Gratta e Vinci"])

    async def ensure_movement_indexes(database):
        await database.gratta_vinci_prodotti.create_index("codice", unique=True)
        await database.gratta_vinci_prodotti.create_index("id", unique=True)
        await database.gratta_vinci_movimenti.create_index("idempotency_key", unique=True)
        await database.gratta_vinci_movimenti.create_index([("tipo", 1), ("giorno_locale", 1), ("codice", 1)])

    @router.get("/prodotti")
    async def list_products():
        database = get_database()
        return await database.gratta_vinci_prodotti.find(
            {}, {"_id": 0}
        ).sort("codice", 1).to_list(10000)

    @router.post("/prodotti", status_code=201)
    async def create_product(body: GrattaVinciProductIn, request: Request):
        database = get_database()
        await ensure_movement_indexes(database)
        now = _now().isoformat()
        document = {
            "id": str(uuid4()),
            "codice": _code(body.codice),
            "descrizione": body.descrizione,
            "prezzo_centesimi": _cents(body.prezzo),
            "quantita_magazzino": body.quantita_magazzino,
            "giacenza_iniziale": body.quantita_magazzino,
            "venduti_totale": 0,
            "incasso_totale_centesimi": 0,
            "version": 0,
            "operatore_creazione": request_operator(request),
            "created_at": now,
            "updated_at": now,
        }
        try:
            await database.gratta_vinci_prodotti.insert_one(document.copy())
        except DuplicateKeyError as exc:
            raise HTTPException(409, "Codice Gratta e Vinci già registrato") from exc
        return document

    @router.put("/prodotti/{product_id}")
    async def edit_product(product_id: str, body: GrattaVinciProductEdit):
        database = get_database()
        result = await database.gratta_vinci_prodotti.update_one(
            {"id": product_id, "version": body.version},
            {"$set": {
                "descrizione": body.descrizione,
                "prezzo_centesimi": _cents(body.prezzo),
                "updated_at": _now().isoformat(),
            }, "$inc": {"version": 1}},
        )
        if result.matched_count != 1:
            exists = await database.gratta_vinci_prodotti.find_one({"id": product_id}, {"_id": 1})
            raise HTTPException(409 if exists else 404, "Prodotto modificato da un altro operatore: ricarica" if exists else "Prodotto non trovato")
        return await database.gratta_vinci_prodotti.find_one({"id": product_id}, {"_id": 0})

    def _replay(existing, *, tipo: str, codice: str, quantita: int):
        if (existing.get("tipo"), existing.get("codice"), existing.get("quantita")) != (
            tipo, codice, quantita
        ):
            raise HTTPException(409, "Chiave idempotente già utilizzata per un'altra operazione")
        return {**existing, "gia_registrato": True}

    async def _movement(body: GrattaVinciMovementIn, request: Request, tipo: Literal["VENDITA", "CARICO"]):
        database = get_database()
        await ensure_movement_indexes(database)
        codice = _code(body.codice)
        key = body.idempotency_key.strip()
        existing = await database.gratta_vinci_movimenti.find_one(
            {"idempotency_key": key}, {"_id": 0}
        )
        if existing:
            return _replay(existing, tipo=tipo, codice=codice, quantita=body.quantita)

        async def operation(session):
            kwargs = session_kwargs(session)
            previous = await database.gratta_vinci_movimenti.find_one(
                {"idempotency_key": key}, {"_id": 0}, **kwargs
            )
            if previous:
                return _replay(previous, tipo=tipo, codice=codice, quantita=body.quantita)

            product = await database.gratta_vinci_prodotti.find_one(
                {"codice": codice}, {"_id": 0}, **kwargs
            )
            if not product:
                raise HTTPException(404, "Gratta e Vinci non trovato")
            available = int(product.get("quantita_magazzino", 0))
            if available < 0:
                raise HTTPException(409, "Giacenza anomala: operazione bloccata")
            if tipo == "VENDITA" and available < body.quantita:
                raise HTTPException(409, "Giacenza insufficiente per registrare la vendita")

            delta = -body.quantita if tipo == "VENDITA" else body.quantita
            cents = int(product["prezzo_centesimi"])
            now = _now()
            event = {
                "id": str(uuid4()),
                "idempotency_key": key,
                "tipo": tipo,
                "codice": codice,
                "prodotto_id": product["id"],
                "descrizione": product["descrizione"],
                "quantita": body.quantita,
                "prezzo_unitario_centesimi": cents if tipo == "VENDITA" else None,
                "totale_centesimi": cents * body.quantita if tipo == "VENDITA" else 0,
                "giacenza_precedente": available,
                "giacenza_successiva_prevista": available + delta,
                "giorno_locale": now.astimezone(ROME).date().isoformat(),
                "created_at": now.isoformat(),
                "operatore": request_operator(request),
            }
            stock_update = {"quantita_magazzino": delta, "version": 1}
            if tipo == "VENDITA":
                stock_update["venduti_totale"] = body.quantita
                stock_update["incasso_totale_centesimi"] = event["totale_centesimi"]
            query = {"id": product["id"], "quantita_magazzino": {"$gte": body.quantita if tipo == "VENDITA" else 0}}
            updated = False
            try:
                result = await database.gratta_vinci_prodotti.update_one(
                    query,
                    {"$inc": stock_update, "$set": {"updated_at": now.isoformat()}},
                    **kwargs,
                )
                if result.modified_count != 1:
                    raise HTTPException(409, "Stock cambiato: ricarica la tabella prima di riprovare")
                updated = True
                await database.gratta_vinci_movimenti.insert_one(event.copy(), **kwargs)
                return {**event, "gia_registrato": False}
            except Exception:
                # Only local/mocked Mongo uses nontransactional compensation.
                # Atlas commits the stock and the movement in one transaction.
                if session is None and updated:
                    await database.gratta_vinci_prodotti.update_one(
                        {"id": product["id"]},
                        {"$inc": {field: -amount for field, amount in stock_update.items()}},
                    )
                raise

        try:
            return await run_transactionally(operation)
        except DuplicateKeyError:
            existing = await database.gratta_vinci_movimenti.find_one(
                {"idempotency_key": key}, {"_id": 0}
            )
            if existing:
                return _replay(existing, tipo=tipo, codice=codice, quantita=body.quantita)
            raise

    @router.post("/vendite", status_code=201)
    async def sell(body: GrattaVinciMovementIn, request: Request):
        return await _movement(body, request, "VENDITA")

    @router.post("/carichi", status_code=201)
    async def receive(body: GrattaVinciMovementIn, request: Request):
        return await _movement(body, request, "CARICO")

    @router.get("/riepilogo")
    async def summary(giorno: Optional[str] = None):
        if giorno is not None:
            try:
                from datetime import date
                if date.fromisoformat(giorno).isoformat() != giorno:
                    raise ValueError
            except ValueError as exc:
                raise HTTPException(422, "Data non valida: usare YYYY-MM-DD") from exc
        database = get_database()
        match = {"tipo": "VENDITA"}
        if giorno:
            match["giorno_locale"] = giorno
        totals = await database.gratta_vinci_movimenti.aggregate([
            {"$match": match},
            {"$group": {
                "_id": "$codice",
                "venduti": {"$sum": "$quantita"},
                "totale_centesimi": {"$sum": "$totale_centesimi"},
            }},
        ]).to_list(10000)
        indexed = {row["_id"]: row for row in totals}
        products = await database.gratta_vinci_prodotti.find(
            {}, {"_id": 0}
        ).sort("codice", 1).to_list(10000)
        rows = [{
            **product,
            "venduti": indexed.get(product["codice"], {}).get("venduti", 0),
            "totale_centesimi": indexed.get(product["codice"], {}).get("totale_centesimi", 0),
        } for product in products]
        return {
            "giorno": giorno,
            "righe": rows,
            "totale_biglietti": sum(item["venduti"] for item in rows),
            "totale_centesimi": sum(item["totale_centesimi"] for item in rows),
        }

    @router.get("/movimenti")
    async def movement_history(codice: Optional[str] = None, limit: int = 100):
        database = get_database()
        query = {"codice": _code(codice)} if codice else {}
        cap = max(1, min(limit, 500))
        return await database.gratta_vinci_movimenti.find(
            query, {"_id": 0}
        ).sort("created_at", -1).limit(cap).to_list(cap)

    return router

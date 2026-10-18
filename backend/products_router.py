"""Product catalogue routes and audited shop-sales corrections."""
import asyncio
from datetime import datetime, timezone
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

try:
    from .money import NonNegativeMoney
except ImportError:  # pragma: no cover - direct module execution compatibility
    from money import NonNegativeMoney


class Prodotto(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codice: str
    descrizione: str
    categoria: str = "ACCESSORI"
    prezzo: NonNegativeMoney = 0
    acquistati: int = 0
    venduti_negozio: int = 0
    venduti_vending: int = 0
    giacenza_negozio: int = 0
    giacenza_vending: int = 0
    colonna_vending: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ProdottoIn(BaseModel):
    codice: str
    descrizione: str
    categoria: str = "ACCESSORI"
    prezzo: NonNegativeMoney = 0
    acquistati: int = 0
    venduti_negozio: int = 0
    venduti_vending: int = 0
    giacenza_negozio: int = 0
    giacenza_vending: int = 0
    colonna_vending: Optional[str] = None


class VendutiNegozioIn(BaseModel):
    venduti_negozio: int = Field(ge=0)
    valore_atteso: int = Field(ge=0)


PRODUCT_CATEGORIES = {
    "SIGARETTE",
    "SIGARI",
    "SIGARETTI",
    "FIUTO E MASTICO",
    "TRINCIATI PER SIGARETTA",
    "ALTRI TABACCHI DA FUMO",
    "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE",
    "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE ELETTRONICA",
    "ACCESSORI",
}


def create_products_router(
    *,
    get_database,
    query_regex,
    cap_limit,
    vending_stock_by_code,
    apply_vending_stock,
    product_code_text,
    adm_numeric_code,
    adm_local_code,
    adm_alias_index,
    canonical_product_code,
    request_operator,
    session_kwargs,
    run_transactionally,
    normalize_money,
    multiply_money,
):
    router = APIRouter()

    @router.get("/prodotti")
    async def list_prodotti(
        q: Optional[str] = None,
        categoria: Optional[str] = None,
        limit: int = 500,
    ):
        filt: Dict[str, Any] = {}
        if categoria:
            filt["categoria"] = categoria
        if q:
            rx = query_regex(q)
            filt["$or"] = [{"codice": rx}, {"descrizione": rx}]
        capped_limit = cap_limit(limit)
        database = get_database()
        docs, vending_totals = await asyncio.gather(
            database.prodotti.find(filt, {"_id": 0}).sort("codice", 1).limit(
                capped_limit
            ).to_list(capped_limit),
            vending_stock_by_code(),
        )
        return apply_vending_stock(docs, vending_totals)

    async def canonical_manual_product_payload(product: ProdottoIn) -> Dict[str, Any]:
        payload = product.model_dump()
        raw_code = product_code_text(payload["codice"])
        numeric = adm_numeric_code(raw_code)
        listino = await get_database().listino_adm.find(
            {"$or": [
                {"codice": adm_local_code(numeric)},
                {"adm_codice": numeric},
            ]},
            {"_id": 0},
        ).to_list(20) if numeric else []
        payload["codice"] = canonical_product_code(
            raw_code,
            payload["descrizione"],
            adm_alias_index(listino),
        )
        return payload

    @router.post("/prodotti")
    async def create_prodotto(product: ProdottoIn):
        payload = await canonical_manual_product_payload(product)
        # Vending stock changes exclusively through vending-column workflows.
        payload["giacenza_vending"] = 0
        database = get_database()
        if await database.prodotti.find_one({"codice": payload["codice"]}):
            raise HTTPException(409, f"Codice prodotto già presente: {payload['codice']}")
        document = Prodotto(**payload).model_dump()
        await database.prodotti.insert_one(document)
        return document

    @router.put("/prodotti/{prod_id}")
    async def update_prodotto(prod_id: str, product: ProdottoIn):
        payload = await canonical_manual_product_payload(product)
        # Dedicated workflows own vending stock and sold counters.
        payload.pop("giacenza_vending", None)
        payload.pop("venduti_negozio", None)
        payload.pop("venduti_vending", None)
        database = get_database()
        collision = await database.prodotti.find_one({
            "codice": payload["codice"],
            "id": {"$ne": prod_id},
        })
        if collision:
            raise HTTPException(409, f"Codice prodotto già presente: {payload['codice']}")
        result = await database.prodotti.update_one({"id": prod_id}, {"$set": payload})
        if result.matched_count == 0:
            raise HTTPException(404, "not found")
        document = await database.prodotti.find_one({"id": prod_id}, {"_id": 0})
        vending_totals = await vending_stock_by_code() or {}
        document["giacenza_vending"] = vending_totals.get(
            str(document.get("codice") or ""), 0
        )
        return document

    @router.patch("/prodotti/{prod_id}/venduti-negozio")
    async def update_venduti_negozio(
        prod_id: str,
        body: VendutiNegozioIn,
        request: Request,
    ):
        operator = request_operator(request)

        async def persist_correction(session):
            kwargs = session_kwargs(session)
            database = get_database()
            product = await database.prodotti.find_one(
                {"id": prod_id}, {"_id": 0}, **kwargs
            )
            if not product:
                raise HTTPException(404, "Prodotto non trovato")

            current_value = int(product.get("venduti_negozio", 0) or 0)
            if current_value != body.valore_atteso:
                raise HTTPException(
                    409,
                    "Il valore dei venduti negozio è cambiato nel frattempo. Ricarica e riprova.",
                )

            if current_value != body.venduti_negozio:
                quantity_delta = body.venduti_negozio - current_value
                unit_price = normalize_money(product.get("prezzo", 0) or 0)
                accounting_amount = multiply_money(unit_price, quantity_delta)
                previous_stock = int(product.get("giacenza_negozio", 0) or 0)
                new_stock = previous_stock - quantity_delta
                result = await database.prodotti.update_one(
                    {"id": prod_id, "venduti_negozio": current_value},
                    {"$set": {
                        "venduti_negozio": body.venduti_negozio,
                        "giacenza_negozio": new_stock,
                    }},
                    **kwargs,
                )
                if result.matched_count != 1:
                    raise HTTPException(
                        409,
                        "Il valore dei venduti negozio è cambiato nel frattempo. Ricarica e riprova.",
                    )
                try:
                    await database.rettifiche_venduti.insert_one({
                        "id": str(uuid.uuid4()),
                        "prodotto_id": prod_id,
                        "codice": product.get("codice", ""),
                        "campo": "venduti_negozio",
                        "valore_precedente": current_value,
                        "valore_nuovo": body.venduti_negozio,
                        "delta_quantita": quantity_delta,
                        "prezzo_unitario": unit_price,
                        "importo_contabile": accounting_amount,
                        "delta_giacenza_negozio": -quantity_delta,
                        "giacenza_precedente": previous_stock,
                        "giacenza_nuova": new_stock,
                        "operatore": operator,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }, **kwargs)
                except Exception:
                    if session is None:
                        await database.prodotti.update_one(
                            {"id": prod_id, "venduti_negozio": body.venduti_negozio},
                            {"$set": {
                                "venduti_negozio": current_value,
                                "giacenza_negozio": previous_stock,
                            }},
                        )
                    raise

            product["venduti_negozio"] = body.venduti_negozio
            product["giacenza_negozio"] = int(
                product.get("giacenza_negozio", 0) or 0
            ) - (body.venduti_negozio - current_value)
            product["venduti_totale"] = body.venduti_negozio + int(
                product.get("venduti_vending", 0) or 0
            )
            return product

        return await run_transactionally(persist_correction)

    @router.delete("/prodotti/{prod_id}")
    async def delete_prodotto(prod_id: str):
        await get_database().prodotti.delete_one({"id": prod_id})
        return {"ok": True}

    return {
        "router": router,
        "list_prodotti": list_prodotti,
        "canonical_manual_product_payload": canonical_manual_product_payload,
        "create_prodotto": create_prodotto,
        "update_prodotto": update_prodotto,
        "update_venduti_negozio": update_venduti_negozio,
        "delete_prodotto": delete_prodotto,
    }

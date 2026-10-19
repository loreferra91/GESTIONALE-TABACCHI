import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

if __package__:
    from .money import NonNegativeMoney
else:
    from money import NonNegativeMoney


class VenditaGiornaliera(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: str
    codice: str
    descrizione: str = ""
    quantita: int = 1
    importo: NonNegativeMoney = 0
    canale: str = "NEGOZIO"
    pagamento: str = "CONTANTI"
    colonna: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sorgente: str = "MANUALE"
    batch_id: Optional[str] = None
    undo_meta: Dict[str, Any] = Field(default_factory=dict)


class VenditaIn(BaseModel):
    data: str
    codice: str
    descrizione: Optional[str] = ""
    quantita: int = Field(default=1, gt=0)
    importo: NonNegativeMoney = Field(default=0, ge=0)
    canale: str = "NEGOZIO"
    pagamento: str = "CONTANTI"
    colonna: Optional[str] = None


def create_sales_router(
    *,
    get_database: Callable[[], Any],
    cap_limit: Callable[[int], int],
    product_code_text: Callable[[Any], str],
    normalize_vending_column: Callable[[Any], str],
    session_kwargs: Callable[[Any], Dict[str, Any]],
    run_transactionally: Callable[[Any], Any],
    sync_product_vending_stock: Callable[..., Any],
) -> Dict[str, Any]:
    router = APIRouter()

    @router.get("/vendite")
    async def list_vendite(giorno: Optional[str] = None, limit: int = 500):
        db = get_database()
        query: Dict[str, Any] = {}
        if giorno:
            safe = re.sub(r"[^0-9\-T:.]", "", giorno)[:32]
            query["data"] = {"$regex": f"^{re.escape(safe)}"}
        capped_limit = cap_limit(limit)
        return await (
            db.vendite.find(query, {"_id": 0})
            .sort("data", -1)
            .limit(capped_limit)
            .to_list(capped_limit)
        )

    @router.post("/vendite")
    async def add_vendita(
        vendita: VenditaIn,
        idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        db = get_database()
        channel = str(vendita.canale or "").strip().upper()
        if channel not in {"NEGOZIO", "VENDING"}:
            raise HTTPException(422, "Canale vendita non valido")
        codice = product_code_text(vendita.codice)
        if not codice:
            raise HTTPException(422, "Codice prodotto obbligatorio")
        key = str(idempotency_key or "").strip() or None
        if key and not 10 <= len(key) <= 160:
            raise HTTPException(422, "X-Idempotency-Key deve contenere da 10 a 160 caratteri")
        if key:
            existing = await db.vendite.find_one({"idempotency_key": key}, {"_id": 0})
            if existing:
                return existing

        product = await db.prodotti.find_one({"codice": codice}, {"_id": 0})
        if not product:
            raise HTTPException(404, f"Prodotto non trovato: {codice}")

        column_code = None
        if channel == "VENDING":
            column_code = normalize_vending_column(vendita.colonna)
            if not column_code:
                raise HTTPException(422, "Per una vendita Vending devi indicare la colonna")

        payload = vendita.model_dump()
        payload.update(
            {
                "codice": codice,
                "canale": channel,
                "pagamento": str(vendita.pagamento or "CONTANTI").strip().upper(),
                "descrizione": vendita.descrizione or product.get("descrizione", ""),
                "colonna": column_code,
            }
        )
        sale_doc = VenditaGiornaliera(**payload).model_dump()
        if key:
            sale_doc["idempotency_key"] = key

        async def operation(session):
            kwargs = session_kwargs(session)
            if key:
                existing = await db.vendite.find_one(
                    {"idempotency_key": key}, {"_id": 0}, **kwargs
                )
                if existing:
                    return existing

            column_changed = False
            product_changed = False
            try:
                if channel == "VENDING":
                    column = await db.vending.find_one(
                        {"colonna": column_code, "codice": codice}, {"_id": 0}, **kwargs
                    )
                    if not column:
                        raise HTTPException(422, "La colonna Vending non contiene il prodotto indicato")
                    result = await db.vending.update_one(
                        {"id": column["id"], "codice": codice, "giacenza": {"$gte": vendita.quantita}},
                        {"$inc": {"giacenza": -vendita.quantita}},
                        **kwargs,
                    )
                    if result.modified_count != 1:
                        raise HTTPException(409, f"Giacenza insufficiente nella colonna {column_code}")
                    column_changed = True
                    result = await db.prodotti.update_one(
                        {"codice": codice},
                        {"$inc": {"venduti_vending": vendita.quantita}},
                        **kwargs,
                    )
                else:
                    result = await db.prodotti.update_one(
                        {"codice": codice, "giacenza_negozio": {"$gte": vendita.quantita}},
                        {
                            "$inc": {
                                "giacenza_negozio": -vendita.quantita,
                                "venduti_negozio": vendita.quantita,
                            }
                        },
                        **kwargs,
                    )
                if result.modified_count != 1:
                    raise HTTPException(409, f"Giacenza insufficiente per il prodotto {codice}")
                product_changed = True
                await db.vendite.insert_one(sale_doc.copy(), **kwargs)
                return sale_doc
            except Exception:
                if session is None:
                    if product_changed:
                        increments = (
                            {"venduti_vending": -vendita.quantita}
                            if channel == "VENDING"
                            else {
                                "giacenza_negozio": vendita.quantita,
                                "venduti_negozio": -vendita.quantita,
                            }
                        )
                        await db.prodotti.update_one({"codice": codice}, {"$inc": increments})
                    if column_changed:
                        await db.vending.update_one(
                            {"colonna": column_code, "codice": codice},
                            {"$inc": {"giacenza": vendita.quantita}},
                        )
                raise

        try:
            result = await run_transactionally(operation)
        except DuplicateKeyError:
            if not key:
                raise
            existing = await db.vendite.find_one({"idempotency_key": key}, {"_id": 0})
            if not existing:
                raise
            result = existing
        if channel == "VENDING":
            await sync_product_vending_stock(codice)
        return result

    @router.delete("/vendite/{v_id}")
    async def del_vendita(v_id: str):
        db = get_database()
        vendita = await db.vendite.find_one({"id": v_id})
        if not vendita:
            raise HTTPException(404, "Vendita non trovata")
        if vendita.get("sorgente", "MANUALE") != "MANUALE":
            raise HTTPException(409, "Questa vendita va annullata dal relativo caricamento")
        return await annulla_vendita_manuale(v_id)

    @router.get("/vendite/manuale/ultima")
    async def ultima_vendita_manuale():
        db = get_database()
        return await db.vendite.find_one(
            {"sorgente": "MANUALE"},
            {"_id": 0},
            sort=[("created_at", -1)],
        )

    @router.post("/vendite/manuale/{sale_id}/annulla")
    async def annulla_vendita_manuale(sale_id: str):
        db = get_database()
        sale = await db.vendite.find_one(
            {"id": sale_id, "sorgente": "MANUALE"}, {"_id": 0}
        )
        if not sale:
            raise HTTPException(404, "Vendita manuale non trovata o già annullata")
        vend_field = "venduti_vending" if sale.get("canale") == "VENDING" else "venduti_negozio"
        quantity = int(sale.get("quantita") or 0)
        increments = {vend_field: -quantity}
        is_vending = sale.get("canale") == "VENDING"
        column_code = normalize_vending_column(sale.get("colonna")) if is_vending else None
        if is_vending and not column_code:
            raise HTTPException(409, "Vendita Vending senza colonna: annullamento manuale richiesto")
        if not is_vending:
            increments["giacenza_negozio"] = quantity

        async def operation(session):
            kwargs = session_kwargs(session)
            column_changed = False
            stock_changed = False
            try:
                if is_vending:
                    column_update = await db.vending.update_one(
                        {"colonna": column_code, "codice": sale.get("codice")},
                        {"$inc": {"giacenza": quantity}},
                        **kwargs,
                    )
                    if column_update.modified_count != 1:
                        raise RuntimeError("La colonna Vending della vendita non esiste più")
                    column_changed = True
                stock_update = await db.prodotti.update_one(
                    {"codice": sale.get("codice")}, {"$inc": increments}, **kwargs
                )
                if stock_update.modified_count != 1:
                    raise RuntimeError("Il prodotto della vendita non esiste più")
                stock_changed = True
                deletion = await db.vendite.delete_one(
                    {"id": sale_id, "sorgente": "MANUALE"}, **kwargs
                )
                if deletion.deleted_count != 1:
                    raise RuntimeError("La vendita è cambiata durante l'annullamento")
                if is_vending:
                    await sync_product_vending_stock(str(sale.get("codice") or ""), session)
            except Exception:
                if session is None:
                    if stock_changed:
                        rollback = {vend_field: quantity}
                        if not is_vending:
                            rollback["giacenza_negozio"] = -quantity
                        await db.prodotti.update_one(
                            {"codice": sale.get("codice")}, {"$inc": rollback}
                        )
                    if column_changed:
                        await db.vending.update_one(
                            {"colonna": column_code, "codice": sale.get("codice")},
                            {"$inc": {"giacenza": -quantity}},
                        )
                        await sync_product_vending_stock(str(sale.get("codice") or ""))
                raise

        try:
            await run_transactionally(operation)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "rimossi": 1, "id": sale_id}

    return {
        "router": router,
        "list_vendite": list_vendite,
        "add_vendita": add_vendita,
        "del_vendita": del_vendita,
        "ultima_vendita_manuale": ultima_vendita_manuale,
        "annulla_vendita_manuale": annulla_vendita_manuale,
    }

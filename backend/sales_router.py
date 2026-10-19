import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Request
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


class BulkVenditaIn(BaseModel):
    canale: str = "NEGOZIO"
    pagamento: str = "CONTANTI"
    righe: List[Dict[str, Any]]


class CassaRapidaIn(BaseModel):
    data: str
    pagamento: str = "CONTANTI"
    righe: List[Dict[str, Any]]
    idempotency_key: Optional[str] = None


def create_sales_router(
    *,
    get_database: Callable[[], Any],
    cap_limit: Callable[[int], int],
    product_code_text: Callable[[Any], str],
    normalize_vending_column: Callable[[Any], str],
    session_kwargs: Callable[[Any], Dict[str, Any]],
    run_transactionally: Callable[[Any], Any],
    sync_product_vending_stock: Callable[..., Any],
    normalize_nonnegative_money: Callable[[Any], float],
    decimal_money: Callable[..., Decimal],
    normalize_money: Callable[[Any], float],
    request_operator: Callable[[Optional[Request]], str],
    cash_movement_model: Any,
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

    @router.post("/vendite/bulk")
    async def bulk_vendite(body: BulkVenditaIn):
        db = get_database()
        if str(body.canale or "").upper() == "VENDING":
            raise HTTPException(
                422,
                "Per le vendite Vending usa il CSV distributore, che identifica la colonna reale",
            )
        batch_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        skipped = 0
        errors = []
        prepared = []
        required_by_code: Dict[str, int] = {}
        for index, row in enumerate(body.righe):
            try:
                codice = product_code_text(row.get("codice"))
                if not codice:
                    skipped += 1
                    continue
                quantity = int(float(row.get("quantita") or 0))
                amount = normalize_nonnegative_money(row.get("importo", 0))
                if quantity <= 0:
                    raise ValueError("quantità non valida")
                if amount < 0:
                    raise ValueError("importo non valido")
                sale_date = str(row.get("data") or datetime.now(timezone.utc).isoformat())
                description = str(row.get("descrizione") or "")
                product = await db.prodotti.find_one({"codice": codice}, {"_id": 0})
                if not product:
                    raise ValueError(f"prodotto non trovato: {codice}")
                required_by_code[codice] = required_by_code.get(codice, 0) + quantity
                prepared.append(
                    VenditaGiornaliera(
                        data=sale_date,
                        codice=codice,
                        descrizione=description or product.get("descrizione", ""),
                        quantita=quantity,
                        importo=amount,
                        canale="NEGOZIO",
                        pagamento=str(body.pagamento or "CONTANTI").strip().upper(),
                        sorgente="BULK",
                        batch_id=batch_id,
                    ).model_dump()
                )
            except Exception as exc:
                errors.append({"riga": index + 1, "errore": str(exc)})
        for codice, required in required_by_code.items():
            product = await db.prodotti.find_one(
                {"codice": codice}, {"_id": 0, "giacenza_negozio": 1}
            )
            available = int((product or {}).get("giacenza_negozio") or 0)
            if required > available:
                errors.append(
                    {"codice": codice, "errore": f"giacenza {available}, richiesti {required}"}
                )
        if errors:
            raise HTTPException(
                422,
                detail={
                    "message": "Caricamento rifiutato: nessuna vendita registrata",
                    "errori": errors,
                },
            )

        async def operation(session):
            kwargs = session_kwargs(session)
            changed: List[Dict[str, Any]] = []
            inserted_ids: List[str] = []
            try:
                for sale in prepared:
                    quantity = int(sale["quantita"])
                    update = await db.prodotti.update_one(
                        {"codice": sale["codice"], "giacenza_negozio": {"$gte": quantity}},
                        {"$inc": {"giacenza_negozio": -quantity, "venduti_negozio": quantity}},
                        **kwargs,
                    )
                    if update.modified_count != 1:
                        raise RuntimeError(f"Giacenza cambiata per {sale['codice']}")
                    changed.append(sale)
                    await db.vendite.insert_one(sale.copy(), **kwargs)
                    inserted_ids.append(sale["id"])
                if prepared:
                    await db.vendite_bulk_imports.insert_one(
                        {
                            "id": batch_id,
                            "created_at": created_at,
                            "sorgente": "BULK",
                            "canale": "NEGOZIO",
                            "pagamento": str(body.pagamento or "CONTANTI").strip().upper(),
                            "inseriti": len(prepared),
                            "saltati": skipped,
                            "status": "active",
                        },
                        **kwargs,
                    )
            except Exception:
                if session is None:
                    if inserted_ids:
                        await db.vendite.delete_many(
                            {"id": {"$in": inserted_ids}, "batch_id": batch_id}
                        )
                    for sale in changed:
                        quantity = int(sale["quantita"])
                        await db.prodotti.update_one(
                            {"codice": sale["codice"]},
                            {
                                "$inc": {
                                    "giacenza_negozio": quantity,
                                    "venduti_negozio": -quantity,
                                }
                            },
                        )
                    await db.vendite_bulk_imports.delete_one({"id": batch_id})
                raise

        await run_transactionally(operation)
        inserted = len(prepared)
        return {
            "inseriti": inserted,
            "saltati": skipped,
            "errori": errors,
            "batch_id": batch_id if inserted else None,
            "created_at": created_at if inserted else None,
            "canale": body.canale,
            "pagamento": body.pagamento,
        }

    @router.post("/vendite/cassa-rapida")
    async def vendita_cassa_rapida(body: CassaRapidaIn, request: Request = None):
        db = get_database()
        key = str(body.idempotency_key or "").strip() or None
        if key and not 10 <= len(key) <= 160:
            raise HTTPException(422, "idempotency_key deve contenere da 10 a 160 caratteri")
        pagamento = str(body.pagamento or "CONTANTI").strip().upper()
        if not body.righe:
            raise HTTPException(422, "Il carrello è vuoto")
        if key:
            existing = await db.cassa_rapida_batches.find_one(
                {"idempotency_key": key}, {"_id": 0}
            )
            if existing:
                return existing

        batch_id = str(uuid.uuid4())
        prepared = []
        total = Decimal(0)
        for index, row in enumerate(body.righe):
            try:
                codice = product_code_text(row.get("codice"))
                quantity = int(row.get("quantita") or 0)
                if not codice or quantity <= 0:
                    raise ValueError("prodotto o quantità non validi")
                product = await db.prodotti.find_one({"codice": codice}, {"_id": 0})
                if not product:
                    raise ValueError(f"prodotto non trovato: {codice}")
                unit_price = decimal_money(
                    row.get("prezzo", product.get("prezzo", 0)), nonnegative=True
                )
                line_total = unit_price * quantity
                prepared.append(
                    {
                        "id": str(uuid.uuid4()),
                        "data": body.data,
                        "codice": codice,
                        "descrizione": str(
                            row.get("descrizione") or product.get("descrizione") or ""
                        ),
                        "quantita": quantity,
                        "importo": normalize_money(line_total),
                        "canale": "NEGOZIO",
                        "pagamento": pagamento,
                        "sorgente": "POS",
                        "batch_id": batch_id,
                        "operatore": request_operator(request),
                    }
                )
                total += line_total
            except (TypeError, ValueError, ArithmeticError) as exc:
                raise HTTPException(422, f"Riga {index + 1}: {exc}") from exc

        total_float = normalize_money(total)
        created_at = datetime.now(timezone.utc).isoformat()
        batch = {
            "id": batch_id,
            "created_at": created_at,
            "sorgente": "POS",
            "pagamento": pagamento,
            "totale": total_float,
            "pezzi": sum(int(row["quantita"]) for row in prepared),
            "idempotency_key": key,
            "status": "active",
        }
        cash_entry = cash_movement_model(
            data=body.data,
            tipo="ENTRATA",
            importo=total_float,
            descrizione=f"Vendita banco ({batch['pezzi']} pz · {pagamento})",
            operatore=request_operator(request),
        ).model_dump()

        async def operation(session):
            kwargs = session_kwargs(session)
            if key:
                existing = await db.cassa_rapida_batches.find_one(
                    {"idempotency_key": key}, {"_id": 0}, **kwargs
                )
                if existing:
                    return existing
            changed = []
            try:
                for sale in prepared:
                    quantity = int(sale["quantita"])
                    result = await db.prodotti.update_one(
                        {"codice": sale["codice"], "giacenza_negozio": {"$gte": quantity}},
                        {"$inc": {"giacenza_negozio": -quantity, "venduti_negozio": quantity}},
                        **kwargs,
                    )
                    if result.modified_count != 1:
                        raise RuntimeError(
                            f"Giacenza insufficiente per il prodotto {sale['codice']}"
                        )
                    changed.append(sale)
                    await db.vendite.insert_one(sale.copy(), **kwargs)
                await db.cassa.insert_one(cash_entry.copy(), **kwargs)
                await db.cassa_rapida_batches.insert_one(batch.copy(), **kwargs)
                return batch
            except Exception:
                if session is None:
                    await db.cassa.delete_one({"id": cash_entry["id"]})
                    await db.vendite.delete_many({"batch_id": batch_id, "sorgente": "POS"})
                    for sale in changed:
                        await db.prodotti.update_one(
                            {"codice": sale["codice"]},
                            {
                                "$inc": {
                                    "giacenza_negozio": int(sale["quantita"]),
                                    "venduti_negozio": -int(sale["quantita"]),
                                }
                            },
                        )
                    await db.cassa_rapida_batches.delete_one({"id": batch_id})
                raise

        try:
            return await run_transactionally(operation)
        except DuplicateKeyError:
            if key:
                existing = await db.cassa_rapida_batches.find_one(
                    {"idempotency_key": key}, {"_id": 0}
                )
                if existing:
                    return existing
            raise

    @router.get("/vendite/bulk/ultimo")
    async def ultimo_bulk_vendite():
        db = get_database()
        return await db.vendite_bulk_imports.find_one(
            {
                "status": "active",
                "$or": [{"sorgente": "BULK"}, {"sorgente": {"$exists": False}}],
            },
            {"_id": 0},
            sort=[("created_at", -1)],
        )

    @router.post("/vendite/bulk/{batch_id}/annulla")
    async def annulla_bulk_vendite(batch_id: str):
        db = get_database()
        claim = await db.vendite_bulk_imports.update_one(
            {"id": batch_id, "status": "active"},
            {"$set": {"status": "annulling"}},
        )
        if claim.modified_count != 1:
            raise HTTPException(404, "Caricamento non trovato o già annullato")

        async def operation(session):
            kwargs = session_kwargs(session)
            sales = await db.vendite.find(
                {"batch_id": batch_id, "sorgente": "BULK"}, {"_id": 0}, **kwargs
            ).to_list(None)
            completed = []
            current_stock_change = None
            try:
                for sale in sales:
                    field = (
                        "giacenza_vending"
                        if sale.get("canale") == "VENDING"
                        else "giacenza_negozio"
                    )
                    vend_field = (
                        "venduti_vending"
                        if sale.get("canale") == "VENDING"
                        else "venduti_negozio"
                    )
                    quantity = int(sale.get("quantita") or 0)
                    stock_update = await db.prodotti.update_one(
                        {"codice": sale.get("codice")},
                        {"$inc": {field: quantity, vend_field: -quantity}},
                        **kwargs,
                    )
                    if stock_update.modified_count != 1:
                        raise RuntimeError(f"Prodotto non trovato: {sale.get('codice')}")
                    current_stock_change = (sale, field, vend_field, quantity)
                    deletion = await db.vendite.delete_one(
                        {"id": sale.get("id"), "batch_id": batch_id}, **kwargs
                    )
                    if deletion.deleted_count != 1:
                        raise RuntimeError("Una vendita del caricamento non è stata eliminata")
                    completed.append(current_stock_change)
                    current_stock_change = None

                await db.vendite_bulk_imports.update_one(
                    {"id": batch_id, "status": "annulling"},
                    {
                        "$set": {
                            "status": "annulled",
                            "annulled_at": datetime.now(timezone.utc).isoformat(),
                            "rimossi": len(completed),
                        }
                    },
                    **kwargs,
                )
                return len(completed)
            except Exception:
                if session is None:
                    if current_stock_change:
                        sale, field, vend_field, quantity = current_stock_change
                        await db.prodotti.update_one(
                            {"codice": sale.get("codice")},
                            {"$inc": {field: -quantity, vend_field: quantity}},
                        )
                    for sale, field, vend_field, quantity in reversed(completed):
                        await db.vendite.insert_one(sale.copy())
                        await db.prodotti.update_one(
                            {"codice": sale.get("codice")},
                            {"$inc": {field: -quantity, vend_field: quantity}},
                        )
                raise

        try:
            removed = await run_transactionally(operation)
        except Exception as exc:
            await db.vendite_bulk_imports.update_one(
                {"id": batch_id, "status": "annulling"},
                {"$set": {"status": "active"}},
            )
            raise HTTPException(500, f"Impossibile annullare il caricamento: {exc}") from exc
        return {"ok": True, "batch_id": batch_id, "rimossi": removed}

    return {
        "router": router,
        "list_vendite": list_vendite,
        "add_vendita": add_vendita,
        "del_vendita": del_vendita,
        "ultima_vendita_manuale": ultima_vendita_manuale,
        "annulla_vendita_manuale": annulla_vendita_manuale,
        "bulk_vendite": bulk_vendite,
        "vendita_cassa_rapida": vendita_cassa_rapida,
        "ultimo_bulk_vendite": ultimo_bulk_vendite,
        "annulla_bulk_vendite": annulla_bulk_vendite,
    }

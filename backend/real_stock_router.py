"""Physical inventory views, adjustments, and adjustment history routes."""
import asyncio
from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


class GiacenzaRealeIn(BaseModel):
    giacenza_negozio: int
    giacenza_vending: int
    attesa_giacenza_negozio: int
    attesa_giacenza_vending: int
    motivazione: str = ""


def plan_vending_inventory(
    columns: List[Dict[str, Any]], target: int
) -> List[Dict[str, Any]]:
    """Distribute an aggregate adjustment across existing physical columns."""
    ordered = sorted(columns, key=lambda row: str(row.get("colonna") or ""))
    current = sum(max(0, int(row.get("giacenza", 0) or 0)) for row in ordered)
    capacity = sum(max(0, int(row.get("capacita_max", 0) or 0)) for row in ordered)
    if target < 0:
        raise HTTPException(422, "La giacenza vending non può essere negativa")
    if not ordered and target:
        raise HTTPException(422, "Il prodotto non è assegnato ad alcuna colonna vending")
    if target > capacity and target > current:
        raise HTTPException(
            422,
            f"La giacenza vending supera la capacità complessiva ({capacity})",
        )

    values = {
        str(row["id"]): max(0, int(row.get("giacenza", 0) or 0))
        for row in ordered
    }
    delta = target - current
    if delta > 0:
        for row in ordered:
            row_id = str(row["id"])
            room = max(0, int(row.get("capacita_max", 0) or 0) - values[row_id])
            added = min(room, delta)
            values[row_id] += added
            delta -= added
            if delta == 0:
                break
    elif delta < 0:
        remaining = -delta
        for row in reversed(ordered):
            row_id = str(row["id"])
            removed = min(values[row_id], remaining)
            values[row_id] -= removed
            remaining -= removed
            if remaining == 0:
                break

    return [
        {
            **row,
            "giacenza_precedente": int(row.get("giacenza", 0) or 0),
            "giacenza_nuova": values[str(row["id"])],
        }
        for row in ordered
        if values[str(row["id"])] != int(row.get("giacenza", 0) or 0)
    ]


def create_real_stock_router(
    *,
    get_database,
    product_code_text,
    request_operator,
    session_kwargs,
    run_transactionally,
    record_audit_event,
    cap_limit,
):
    router = APIRouter()

    @router.get("/categorie-prodotti")
    async def list_categorie_prodotti():
        """Return the distinct categories present in the product catalogue."""
        raw = await get_database().prodotti.distinct("categoria")
        return sorted({
            str(category or "").strip()
            for category in raw
            if str(category or "").strip()
        })

    @router.get("/giacenze-reali")
    async def list_giacenze_reali():
        database = get_database()
        products, columns = await asyncio.gather(
            database.prodotti.find({}, {"_id": 0}).to_list(5000),
            database.vending.find({}, {"_id": 0}).to_list(5000),
        )
        vending_by_code: Dict[str, List[Dict[str, Any]]] = {}
        for column in columns:
            code = str(column.get("codice") or "")
            if code:
                vending_by_code.setdefault(code, []).append(column)

        rows = []
        for product in products:
            code = str(product.get("codice") or "")
            product_columns = vending_by_code.get(code, [])
            shop = max(0, int(product.get("giacenza_negozio", 0) or 0))
            vending = sum(
                max(0, int(row.get("giacenza", 0) or 0))
                for row in product_columns
            )
            rows.append({
                "id": product.get("id"),
                "codice": code,
                "descrizione": str(product.get("descrizione") or code),
                "categoria": str(product.get("categoria") or ""),
                "giacenza_negozio": shop,
                "giacenza_vending": vending,
                "giacenza_totale": shop + vending,
                "colonne_vending": len(product_columns),
                "capacita_vending": sum(
                    max(0, int(row.get("capacita_max", 0) or 0))
                    for row in product_columns
                ),
            })
        return sorted(rows, key=lambda row: (row["descrizione"].casefold(), row["codice"]))

    @router.put("/giacenze-reali/{codice}")
    async def update_giacenza_reale(
        codice: str,
        body: GiacenzaRealeIn,
        request: Request = None,
    ):
        code = product_code_text(codice)
        for value in (
            body.giacenza_negozio,
            body.giacenza_vending,
            body.attesa_giacenza_negozio,
            body.attesa_giacenza_vending,
        ):
            if value < 0:
                raise HTTPException(
                    422,
                    "Le giacenze devono essere numeri interi non negativi",
                )
        reason = str(body.motivazione or "").strip()
        if len(reason) > 500:
            raise HTTPException(422, "La motivazione non può superare 500 caratteri")
        operator = request_operator(request)

        async def persist_adjustment(session):
            kwargs = session_kwargs(session)
            database = get_database()
            product = await database.prodotti.find_one({"codice": code}, **kwargs)
            if not product:
                raise HTTPException(404, "Prodotto non trovato")
            columns = await database.vending.find(
                {"codice": code}, {"_id": 0}, **kwargs
            ).to_list(500)
            old_shop = int(product.get("giacenza_negozio", 0) or 0)
            old_vending = sum(
                max(0, int(row.get("giacenza", 0) or 0)) for row in columns
            )
            if (
                old_shop != body.attesa_giacenza_negozio
                or old_vending != body.attesa_giacenza_vending
            ):
                raise HTTPException(
                    409,
                    "Le giacenze sono cambiate: aggiorna la pagina prima di salvare",
                )

            vending_plan = plan_vending_inventory(columns, body.giacenza_vending)
            now = datetime.now(timezone.utc).isoformat()
            product_updated = False
            applied_columns = []
            adjustment_ids = []
            try:
                product_result = await database.prodotti.update_one(
                    {
                        "codice": code,
                        "giacenza_negozio": product.get("giacenza_negozio", 0),
                    },
                    {"$set": {
                        "giacenza_negozio": body.giacenza_negozio,
                        "giacenza_vending": body.giacenza_vending,
                        "giacenza_reale_aggiornata_il": now,
                    }},
                    **kwargs,
                )
                if product_result.matched_count != 1:
                    raise HTTPException(
                        409,
                        "La giacenza negozio è cambiata durante il salvataggio",
                    )
                product_updated = True

                for planned in vending_plan:
                    column_result = await database.vending.update_one(
                        {
                            "id": planned["id"],
                            "giacenza": planned["giacenza_precedente"],
                        },
                        {"$set": {
                            "giacenza": planned["giacenza_nuova"],
                            "giacenza_aggiornata_il": now,
                            "giacenza_sorgente": "RETTIFICA_INVENTARIO",
                        }},
                        **kwargs,
                    )
                    if column_result.matched_count != 1:
                        raise HTTPException(
                            409,
                            f"La colonna {planned.get('colonna')} è cambiata durante il salvataggio",
                        )
                    applied_columns.append(planned)

                adjustments = []
                if old_shop != body.giacenza_negozio:
                    adjustments.append({
                        "id": str(uuid.uuid4()),
                        "codice": code,
                        "descrizione": str(product.get("descrizione") or code),
                        "ubicazione": "NEGOZIO",
                        "quantita_precedente": old_shop,
                        "quantita_nuova": body.giacenza_negozio,
                        "differenza": body.giacenza_negozio - old_shop,
                        "motivazione": reason,
                        "operatore": operator,
                        "created_at": now,
                    })
                if old_vending != body.giacenza_vending:
                    adjustments.append({
                        "id": str(uuid.uuid4()),
                        "codice": code,
                        "descrizione": str(product.get("descrizione") or code),
                        "ubicazione": "VENDING",
                        "quantita_precedente": old_vending,
                        "quantita_nuova": body.giacenza_vending,
                        "differenza": body.giacenza_vending - old_vending,
                        "motivazione": reason,
                        "operatore": operator,
                        "dettaglio_colonne": [
                            {
                                "id": row["id"],
                                "colonna": row.get("colonna"),
                                "quantita_precedente": row["giacenza_precedente"],
                                "quantita_nuova": row["giacenza_nuova"],
                            }
                            for row in vending_plan
                        ],
                        "created_at": now,
                    })
                if adjustments:
                    adjustment_ids = [adjustment["id"] for adjustment in adjustments]
                    await database.rettifiche_inventario.insert_many(
                        [dict(adjustment) for adjustment in adjustments],
                        **kwargs,
                    )

                return {
                    "ok": True,
                    "codice": code,
                    "descrizione": str(product.get("descrizione") or code),
                    "giacenza_negozio": body.giacenza_negozio,
                    "giacenza_vending": body.giacenza_vending,
                    "giacenza_totale": body.giacenza_negozio + body.giacenza_vending,
                    "rettifiche": adjustments,
                    "updated_at": now,
                }
            except Exception:
                if session is None:
                    if adjustment_ids:
                        await database.rettifiche_inventario.delete_many(
                            {"id": {"$in": adjustment_ids}}
                        )
                    for planned in reversed(applied_columns):
                        await database.vending.update_one(
                            {
                                "id": planned["id"],
                                "giacenza": planned["giacenza_nuova"],
                            },
                            {"$set": {
                                "giacenza": planned["giacenza_precedente"],
                                "giacenza_sorgente": planned.get("giacenza_sorgente"),
                                "giacenza_aggiornata_il": planned.get(
                                    "giacenza_aggiornata_il"
                                ),
                            }},
                        )
                    if product_updated:
                        await database.prodotti.update_one(
                            {
                                "codice": code,
                                "giacenza_negozio": body.giacenza_negozio,
                            },
                            {"$set": {
                                "giacenza_negozio": old_shop,
                                "giacenza_vending": old_vending,
                                "giacenza_reale_aggiornata_il": product.get(
                                    "giacenza_reale_aggiornata_il"
                                ),
                            }},
                        )
                raise

        result = await run_transactionally(persist_adjustment)
        if result.get("rettifiche"):
            await record_audit_event(
                "RETTIFICATO",
                "GIACENZA_REALE",
                code,
                dopo={
                    "rettifiche": result["rettifiche"],
                    "giacenza_negozio": result["giacenza_negozio"],
                    "giacenza_vending": result["giacenza_vending"],
                },
                operatore=operator,
            )
        return result

    @router.get("/giacenze-reali/rettifiche/storico")
    async def storico_rettifiche_inventario(limit: int = 200):
        capped_limit = cap_limit(limit)
        return await get_database().rettifiche_inventario.find(
            {}, {"_id": 0}
        ).sort("created_at", -1).limit(capped_limit).to_list(capped_limit)

    return {
        "router": router,
        "list_categorie_prodotti": list_categorie_prodotti,
        "list_giacenze_reali": list_giacenze_reali,
        "update_giacenza_reale": update_giacenza_reale,
        "storico_rettifiche_inventario": storico_rettifiche_inventario,
    }

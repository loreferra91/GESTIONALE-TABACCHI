import asyncio
import hashlib
import io
import json
import logging
import math
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

try:
    from .auto_order_planning import ROME, commercial_day, is_orderable_product, pending_by_code, projected_shortage, reconcile_sales, rounded_order_quantity
except ImportError:  # pragma: no cover
    from auto_order_planning import ROME, commercial_day, is_orderable_product, pending_by_code, projected_shortage, reconcile_sales, rounded_order_quantity


class AutoOrderRigaIn(BaseModel):
    codice: str
    quantita: int = Field(gt=0, le=100000)


class AutoOrderConfermaIn(BaseModel):
    snapshot_key: Optional[str] = None
    idempotency_key: Optional[str] = None
    batch_key: Optional[str] = None
    fornitore: Optional[str] = Field(default=None, max_length=120)
    data_consegna_prevista: Optional[date] = None
    righe: Optional[List[AutoOrderRigaIn]] = None


class FornituraUpdateIn(BaseModel):
    version: int = Field(ge=0)
    stato: Optional[str] = None
    data_consegna_prevista: Optional[date] = None


class RiconciliaCaricoIn(BaseModel):
    version: int = Field(ge=0)
    conferma_differenze: bool = False


def relevant_auto_order_params(params: Dict[str, Any]) -> Dict[str, Any]:
    names = {
        "GIORNI_COPERTURA_MIN", "GIORNI_COPERTURA_TARGET",
        "AUTO_ORDER_FINESTRA_BREVE_GG", "AUTO_ORDER_FINESTRA_LUNGA_GG",
        "AUTO_ORDER_PESO_BREVE", "AUTO_ORDER_FATTORE_SICUREZZA",
        "AUTO_ORDER_LEAD_TIME_GG", "AUTO_ORDER_REVIEW_PERIOD_GG",
        "FAST_VENDUTO30_MIN", "SLOW_VENDUTO30_MAX",
        "SCORTA_MINIMA_NEGOZIO_VENDING",
    }
    return {key: value for key, value in params.items() if key in names or key.startswith("LOTTO_")}


def auto_order_snapshot_key(ao: Dict[str, Any]) -> str:
    payload = {
        "data_riferimento_domanda": ao.get("data_riferimento_domanda"),
        "parametri": relevant_auto_order_params(ao.get("parametri", {})),
        "source_watermarks": {key: value for key, value in (ao.get("source_watermarks") or {}).items() if key != "ultima_riconciliazione"},
        "planning_day": ao.get("planning_day"),
        "orders_version": ao.get("orders_version"),
        "data_quality": ao.get("data_quality"),
        "righe": [
            {
                "codice": row.get("codice"),
                "qta_da_ordinare": row.get("qta_da_ordinare"),
                "giacenza_negozio": row.get("giacenza_negozio"),
                "giacenza_vending": row.get("giacenza_vending"),
                "auto_order_version": row.get("auto_order_version"),
                "venduto_breve": row.get("venduto_breve"),
                "venduto_lungo": row.get("venduto_lungo"),
                "quantita_in_arrivo": row.get("quantita_in_arrivo"),
                "quantita_senza_data": row.get("quantita_senza_data"),
                "ricezioni_previste": row.get("ricezioni_previste"),
                "qualita_dati": row.get("qualita_dati"),
                "classe_rotazione": row.get("classe_rotazione"),
                "anomalia_domanda": row.get("anomalia_domanda"),
                "anomalie_riconciliazione": row.get("anomalie_riconciliazione"),
                "prezzo": row.get("prezzo"),
                "totale": row.get("totale"),
            }
            for row in [*ao.get("righe", []), *ao.get("esclusi", [])]
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "auto-order:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_auto_order_router(
    *,
    get_database: Callable[[], Any],
    get_params: Callable[[], Any],
    param_value: Callable[[Dict[str, float], str, float], float],
    lot_for: Callable[[str, Dict[str, float]], int],
    vending_stock_by_code: Callable[[], Any],
    apply_vending_stock: Callable[[List[Dict[str, Any]], Dict[str, Any]], None],
    normalize_money: Callable[[Any], float],
    sum_money: Callable[[Any], float],
    multiply_money: Callable[[Any, Any], float],
    get_auto_order: Callable[[], Any],
    run_transactionally: Callable[[Any], Any],
    record_audit_event: Callable[..., Any],
    create_backup_snapshot: Callable[..., Any],
    session_kwargs: Callable[[Any], Dict[str, Any]],
) -> Dict[str, Any]:
    router = APIRouter()

    @router.get("/auto-order/forniture")
    async def list_forniture():
        db = get_database()
        batches = await db.ordini_fornitore.find(
            {"stato": {"$in": ["CREATO", "CONFERMATO", "INVIATO", "PARZIALMENTE_RICEVUTO"]}},
            {"_id": 0},
        ).sort("data", -1).to_list(None)
        keys = [batch["batch_key"] for batch in batches if batch.get("batch_key")]
        lines = await db.ordini_fornitore_righe.find({"batch_key": {"$in": keys}}, {"_id": 0}).to_list(None) if keys else []
        by_key = {}
        for line in lines:
            by_key.setdefault(line.get("batch_key"), []).append(line)
        return [{**batch, "righe": by_key.get(batch.get("batch_key"), [])} for batch in batches]

    async def compatible_receipts(batch_key: str) -> List[Dict[str, Any]]:
        """Preview completed, unlinked stock receipts without changing balances."""
        db = get_database()
        batch = await db.ordini_fornitore.find_one({"batch_key": batch_key}, {"_id": 0})
        if not batch:
            raise HTTPException(404, "Ordine fornitore non trovato")
        if batch.get("stato") not in {"CREATO", "CONFERMATO", "INVIATO", "PARZIALMENTE_RICEVUTO"}:
            raise HTTPException(409, "Ordine fornitore già chiuso")
        if batch.get("status", "COMPLETED") != "COMPLETED" or batch.get("last_receipt_batch_id"):
            raise HTTPException(409, "Ordine già collegato a un carico o non completato")
        lines = await db.ordini_fornitore_righe.find({"batch_key": batch_key}, {"_id": 0}).to_list(None)
        if not lines or any(int(row.get("quantita_ricevuta", 0) or 0) for row in lines):
            raise HTTPException(409, "Riconciliazione legacy disponibile solo per ordini non ancora collegati a carichi")
        ordered: Dict[str, int] = {}
        for row in lines:
            code = str(row.get("codice") or "")
            ordered[code] = ordered.get(code, 0) + int(row.get("quantita_ordinata", row.get("quantita", 0)) or 0)
        receipts = await db.ricezioni_merce.find(
            {"status": "completed", "supplier_order_batch_key": {"$in": [None, ""]},
             "legacy_reconciled_order_batch_key": {"$exists": False}}, {"_id": 0}
        ).sort("completed_at", -1).to_list(100)
        candidates = []
        for receipt in receipts:
            if commercial_day(receipt.get("data")) is None or commercial_day(batch.get("data")) is None or commercial_day(receipt["data"]) < commercial_day(batch["data"]):
                continue
            movements = await db.storico_ordini.find(
                {"batch_id": receipt.get("id"), "annullato": {"$ne": True}},
                {"_id": 0, "codice": 1, "quantita": 1},
            ).to_list(None)
            received: Dict[str, int] = {}
            for movement in movements:
                code = str(movement.get("codice") or "")
                received[code] = received.get(code, 0) + int(movement.get("quantita") or 0)
            if not received:
                continue
            matched = set(ordered) & set(received)
            if not ordered or len(matched) / len(ordered) < 0.8:
                continue
            missing = {code: quantity - received.get(code, 0) for code, quantity in ordered.items() if quantity > received.get(code, 0)}
            extra = {code: quantity - ordered.get(code, 0) for code, quantity in received.items() if quantity > ordered.get(code, 0)}
            candidates.append({
                "ricezione_id": receipt["id"], "data": receipt.get("data"),
                "file_sorgente": receipt.get("file_sorgente"),
                "righe_ordine": len(ordered), "righe_coincidenti": len(matched),
                "pezzi_ordinati": sum(ordered.values()), "pezzi_caricati": sum(received.values()),
                "mancanti": missing, "extra": extra,
                "ricevuti_per_codice": received,
            })
        return candidates

    @router.get("/auto-order/forniture/{batch_key}/carichi-esistenti")
    async def carichi_esistenti(batch_key: str):
        return {"carichi": await compatible_receipts(batch_key)}

    @router.post("/auto-order/forniture/{batch_key}/chiudi-con-carico/{receipt_id}")
    async def chiudi_con_carico(batch_key: str, receipt_id: str, body: RiconciliaCaricoIn):
        db = get_database()
        candidates = await compatible_receipts(batch_key)
        candidate = next((item for item in candidates if item["ricezione_id"] == receipt_id), None)
        if not candidate:
            raise HTTPException(409, "Carico non disponibile o non compatibile con l'ordine")
        if (candidate["mancanti"] or candidate["extra"]) and not body.conferma_differenze:
            raise HTTPException(422, "Conferma le differenze fra ordine e carico prima di chiudere")
        batch = await db.ordini_fornitore.find_one({"batch_key": batch_key}, {"_id": 0})
        lines = await db.ordini_fornitore_righe.find({"batch_key": batch_key}, {"_id": 0}).to_list(None)
        current_version = int(batch.get("version", 0) or 0)
        if current_version != body.version:
            raise HTTPException(409, "Ordine fornitore modificato nel frattempo")
        backup = await create_backup_snapshot(
            "Prima riconciliazione ordine con carico esistente", "pre-riconciliazione-fornitura",
            ["ordini_fornitore", "ordini_fornitore_righe", "ricezioni_merce", "storico_ordini"],
        )
        now = datetime.now(timezone.utc).isoformat()

        async def persist(session):
            kwargs = session_kwargs(session)
            receipt_changed = await db.ricezioni_merce.update_one(
                {"id": receipt_id, "status": "completed", "supplier_order_batch_key": {"$in": [None, ""]},
                 "legacy_reconciled_order_batch_key": {"$exists": False}},
                {"$set": {"legacy_reconciled_order_batch_key": batch_key, "legacy_reconciled_at": now}}, **kwargs,
            )
            if receipt_changed.matched_count != 1:
                raise HTTPException(409, "Carico già riconciliato nel frattempo")
            for line in lines:
                code = str(line.get("codice") or "")
                ordered = int(line.get("quantita_ordinata", line.get("quantita", 0)) or 0)
                received = min(ordered, candidate["ricevuti_per_codice"].get(code, 0))
                changed = await db.ordini_fornitore_righe.update_one(
                    {"id": line["id"], "batch_key": batch_key,
                     "quantita_ricevuta": {"$in": [None, 0]}},
                    {"$set": {"quantita_ricevuta": received, "quantita_non_consegnata": ordered - received,
                              "quantita_residua": 0, "stato": "RICEVUTO", "data_ultima_ricezione": candidate["data"],
                              "legacy_receipt_id": receipt_id}}, **kwargs,
                )
                if changed.matched_count != 1:
                    raise HTTPException(409, f"Riga fornitura {code} modificata nel frattempo")
            version_filter = {"version": body.version} if body.version else {"$or": [{"version": 0}, {"version": {"$exists": False}}]}
            changed = await db.ordini_fornitore.update_one(
                {"id": batch["id"], "stato": batch["stato"], **version_filter},
                {"$set": {"stato": "RICEVUTO", "last_receipt_batch_id": receipt_id,
                          "riconciliazione_legacy": {"at": now, "backup_id": backup["id"],
                                                    "mancanti": candidate["mancanti"], "extra": candidate["extra"]}},
                 "$inc": {"version": 1}}, **kwargs,
            )
            if changed.matched_count != 1:
                raise HTTPException(409, "Ordine fornitore modificato nel frattempo")
            await db.storico_ordini.update_many(
                {"batch_id": receipt_id, "annullato": {"$ne": True}},
                {"$set": {"legacy_reconciled_order_batch_key": batch_key}}, **kwargs,
            )

        await run_transactionally(persist)
        await record_audit_event("RICONCILIATO", "ORDINE_FORNITORE", batch["id"], prima=batch,
                                 dopo={"stato": "RICEVUTO", "ricezione_id": receipt_id,
                                       "mancanti": candidate["mancanti"], "extra": candidate["extra"]})
        return {"ok": True, "backup_id": backup["id"], "ricezione_id": receipt_id,
                "ordine": batch_key, "mancanti": candidate["mancanti"], "extra": candidate["extra"]}

    @router.patch("/auto-order/forniture/{batch_key}")
    async def update_fornitura(batch_key: str, body: FornituraUpdateIn):
        db = get_database()
        batch = await db.ordini_fornitore.find_one({"batch_key": batch_key}, {"_id": 0})
        if not batch:
            raise HTTPException(404, "Ordine fornitore non trovato")
        if batch.get("status", "COMPLETED") != "COMPLETED":
            raise HTTPException(409, "Ordine fornitore incompleto")
        current = str(batch.get("stato") or "").upper()
        if current in {"RICEVUTO", "ANNULLATO"}:
            raise HTTPException(409, "Ordine fornitore già chiuso")
        target = (body.stato or current).upper()
        allowed = {"CREATO": {"CONFERMATO", "INVIATO", "ANNULLATO"},
                   "CONFERMATO": {"INVIATO", "ANNULLATO"},
                   "INVIATO": {"ANNULLATO"},
                   "PARZIALMENTE_RICEVUTO": set()}
        if target != current and target not in allowed.get(current, set()):
            raise HTTPException(422, f"Transizione {current} → {target} non consentita")
        if target == "ANNULLATO":
            lines = await db.ordini_fornitore_righe.find({"batch_key": batch_key}, {"_id": 0, "quantita_ricevuta": 1}).to_list(None)
            if any(int(line.get("quantita_ricevuta", 0) or 0) for line in lines):
                raise HTTPException(409, "Ordine già parzialmente ricevuto: annullamento automatico non consentito")
        if body.data_consegna_prevista and body.data_consegna_prevista <= datetime.now(ROME).date():
            raise HTTPException(422, "Questa è una previsione futura. Se la merce è già arrivata, collega il carico registrato con la sua data effettiva")
        changes = {"stato": target}
        if body.data_consegna_prevista:
            changes["data_consegna_prevista"] = body.data_consegna_prevista.isoformat()
        version_filter = {"version": body.version} if body.version else {"$or": [{"version": 0}, {"version": {"$exists": False}}]}
        changed = await db.ordini_fornitore.update_one(
            {"id": batch["id"], "stato": current, **version_filter},
            {"$set": changes, "$inc": {"version": 1}},
        )
        if changed.matched_count != 1:
            raise HTTPException(409, "Ordine fornitore modificato nel frattempo")
        updated = await db.ordini_fornitore.find_one({"id": batch["id"]}, {"_id": 0})
        await record_audit_event("MODIFICATO", "ORDINE_FORNITORE", batch["id"], prima=batch, dopo=updated)
        return updated

    @router.get("/auto-order")
    async def auto_order():
        db = get_database()
        params = await get_params()
        finestra_breve = max(3, min(30, int(param_value(params, "AUTO_ORDER_FINESTRA_BREVE_GG", 10))))
        finestra_lunga = max(
            finestra_breve,
            min(90, int(param_value(params, "AUTO_ORDER_FINESTRA_LUNGA_GG", 30))),
        )
        peso_breve = max(0.0, min(1.0, float(param_value(params, "AUTO_ORDER_PESO_BREVE", 0.70))))
        peso_lungo = 1.0 - peso_breve
        latest_app, latest_csv = await asyncio.gather(
            db.vendite.find_one({"sorgente": {"$ne": "CSV_VENDING"}, "data": {"$regex": r"^\d{4}-\d{2}-\d{2}"}}, {"_id": 0, "data": 1}, sort=[("data", -1)]),
            db.vendite.find_one({"sorgente": "CSV_VENDING", "data": {"$regex": r"^\d{4}-\d{2}-\d{2}"}}, {"_id": 0, "data": 1}, sort=[("data", -1)]),
        )

        def parsed_date(document: Optional[Dict[str, Any]]) -> Optional[datetime]:
            raw = (document or {}).get("data")
            if not raw:
                return None
            if isinstance(raw, datetime):
                return raw
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                return None

        available_dates = [value for value in (parsed_date(latest_app), parsed_date(latest_csv)) if value]
        latest_sale_day = max((commercial_day(value) for value in available_dates), default=None)
        reference_day = datetime.now(ROME).date()

        def sales_pipeline(days: int) -> List[Dict[str, Any]]:
            first_day = reference_day - timedelta(days=days - 1)
            since = datetime.combine(first_day, datetime.min.time()).isoformat()
            before = datetime.combine(reference_day + timedelta(days=1), datetime.min.time()).isoformat()
            return [
                {"$match": {"data": {"$gte": since, "$lt": before}}},
                {"$group": {"_id": "$codice", "tot": {"$sum": "$quantita"}}},
            ]

        pipeline_breve = sales_pipeline(finestra_breve)
        pipeline_lunga = sales_pipeline(finestra_lunga)
        first_day = reference_day - timedelta(days=finestra_lunga - 1)
        daily_pipeline = [
            # One-day margins retain aware timestamps that cross a Rome day boundary.
            {"$match": {"$or": [
                {"data": {"$gte": (first_day - timedelta(days=1)).isoformat(), "$lt": (reference_day + timedelta(days=2)).isoformat()}},
                # Older native records can still use the Italian date format.
                {"data": {"$regex": r"^\d{2}[/\-]\d{2}[/\-]\d{4}"}},
            ]}},
            {"$group": {"_id": {"codice": "$codice", "day": "$data",
                                 "canale": "$canale", "sorgente": "$sorgente"},
                         "tot": {"$sum": "$quantita"}}},
        ]
        storico_pipeline = [
            {
                "$group": {
                    "_id": "$codice",
                    "n_ord": {"$sum": 1},
                    "tot_quantita": {"$sum": "$quantita"},
                }
            }
        ]

        (
            prodotti,
            vendite_app_breve,
            vendite_app_lunga,
            storico_ordini,
            vending_totals,
            app_daily,
        ) = await asyncio.gather(
            db.prodotti.find({}, {"_id": 0}).to_list(None),
            db.vendite.aggregate(pipeline_breve).to_list(None),
            db.vendite.aggregate(pipeline_lunga).to_list(None),
            db.storico_ordini.aggregate(storico_pipeline).to_list(None),
            vending_stock_by_code(),
            db.vendite.aggregate(daily_pipeline).to_list(None),
        )
        apply_vending_stock(prodotti, vending_totals)

        def totals(rows: List[Dict[str, Any]]) -> Dict[str, int]:
            return {row["_id"]: int(row.get("tot", 0) or 0) for row in rows if row.get("_id") is not None}

        vendite_app_breve_per_codice = totals(vendite_app_breve)
        vendite_app_lunga_per_codice = totals(vendite_app_lunga)
        storico_per_codice = {row["_id"]: row for row in storico_ordini if row.get("_id") is not None}
        reconciled = reconcile_sales(app_daily, reference_day, finestra_breve, finestra_lunga)
        latest_receipt = await db.ricezioni_merce.find_one({"status": "completed"}, {"_id": 0, "completed_at": 1}, sort=[("completed_at", -1)]) if hasattr(db, "ricezioni_merce") else None

        fatt = max(1.0, float(param_value(params, "AUTO_ORDER_FATTORE_SICUREZZA", 1.15)))
        gg_min = max(1.0, float(param_value(params, "GIORNI_COPERTURA_MIN", 7)))
        gg_target = max(gg_min, float(param_value(params, "GIORNI_COPERTURA_TARGET", 14)))
        lead_time = int(params.get("AUTO_ORDER_LEAD_TIME_GG") or 0)
        review_period = int(params.get("AUTO_ORDER_REVIEW_PERIOD_GG") or 0)
        planning_days = lead_time + review_period if lead_time > 0 else int(gg_target)
        planning_today = datetime.now(ROME).date()
        pending = {}
        orders_version = hashlib.sha256(b"[]").hexdigest()
        if hasattr(db, "ordini_fornitore") and hasattr(db, "ordini_fornitore_righe"):
            batch_rows = await db.ordini_fornitore.find(
                {"stato": {"$in": ["CREATO", "CONFERMATO", "INVIATO", "PARZIALMENTE_RICEVUTO"]}},
                {"_id": 0},
            ).to_list(None)
            keys = [row["batch_key"] for row in batch_rows if row.get("batch_key") and row.get("status", "COMPLETED") == "COMPLETED"]
            order_rows = await db.ordini_fornitore_righe.find(
                {"batch_key": {"$in": keys}}, {"_id": 0}
            ).to_list(None) if keys else []
            pending = pending_by_code(batch_rows, order_rows, planning_today, planning_days)
            orders_version = hashlib.sha256(json.dumps({
                "batches": sorted(((row.get("batch_key"), row.get("stato"), row.get("version"), row.get("data_consegna_prevista")) for row in batch_rows), key=lambda item: json.dumps(item, default=str)),
                "lines": sorted(((row.get("batch_key"), row.get("codice"), row.get("quantita_ordinata", row.get("quantita")), row.get("quantita_ricevuta"), row.get("stato"), row.get("data_consegna_prevista")) for row in order_rows), key=lambda item: json.dumps(item, default=str)),
            }, sort_keys=True, default=str).encode("utf-8")).hexdigest()

        proposte = []
        esclusi = []
        for prodotto in prodotti:
            if not is_orderable_product(prodotto):
                continue
            codice = prodotto["codice"]
            giac_negozio = int(prodotto.get("giacenza_negozio", 0) or 0)
            giac_vending = int(prodotto.get("giacenza_vending", 0) or 0)
            venduti_vending = int(prodotto.get("venduti_vending", 0) or 0)
            giac_tot = giac_negozio + giac_vending
            magazzino_reale_lordo = giac_negozio
            magazzino_reale = max(0, magazzino_reale_lordo)
            anomalia_stock = giac_negozio < 0

            sales = reconciled.get(codice)
            if sales:
                venduto_breve = sales["venduto_breve"]
                venduto_lungo = sales["venduto_lungo"]
                fonte_domanda = sales["fonte_prevalente"]
            else:
                venduto_breve = vendite_app_breve_per_codice.get(codice, 0)
                venduto_lungo = vendite_app_lunga_per_codice.get(codice, 0)
                fonte_domanda = "VENDITE" if venduto_lungo else "NESSUNA"

            storico = storico_per_codice.get(codice, {})
            n_ord = int(storico.get("n_ord", 0) or 0)
            tot_quantita = storico.get("tot_quantita", 0) or 0
            media_ord = round(tot_quantita / n_ord, 2) if n_ord else 0

            domanda_breve_gg = venduto_breve / float(finestra_breve)
            domanda_lunga_gg = venduto_lungo / float(finestra_lunga)
            domanda_gg = (domanda_breve_gg * peso_breve) + (domanda_lunga_gg * peso_lungo)
            domanda_precedente_gg = max(0, venduto_lungo - venduto_breve) / (finestra_lunga - finestra_breve) if finestra_lunga > finestra_breve else None
            venduto_normalizzato_30 = venduto_lungo * 30 / finestra_lunga
            fast_threshold = float(params.get("FAST_VENDUTO30_MIN", 8))
            slow_threshold = float(params.get("SLOW_VENDUTO30_MAX", 2))
            classe_rotazione = "FAST" if venduto_normalizzato_30 >= fast_threshold else "SLOW" if venduto_normalizzato_30 <= slow_threshold else "NORMAL"
            anomalia_domanda = domanda_precedente_gg is not None and domanda_precedente_gg > 0 and domanda_breve_gg >= 2 * domanda_precedente_gg and domanda_breve_gg - domanda_precedente_gg >= 2
            domanda_fallback_lunga = False
            if domanda_gg <= 0 and domanda_lunga_gg > 0:
                # Un peso breve del 100% non deve azzerare il fabbisogno di un
                # articolo che ha avuto almeno una vendita nella finestra lunga.
                domanda_gg = domanda_lunga_gg
                domanda_fallback_lunga = True
            if sales and sales["venduto_totale"] > 0 and sales["venduto_negozio"] is not None:
                if domanda_fallback_lunga:
                    domanda_negozio_gg = sales["venduto_negozio"] / float(finestra_lunga)
                    domanda_vending_gg = sales["venduto_vending"] / float(finestra_lunga)
                else:
                    domanda_negozio_gg = (sales["venduto_negozio_breve"] / float(finestra_breve) * peso_breve
                                          + sales["venduto_negozio"] / float(finestra_lunga) * peso_lungo)
                    domanda_vending_gg = (sales["venduto_vending_breve"] / float(finestra_breve) * peso_breve
                                          + sales["venduto_vending"] / float(finestra_lunga) * peso_lungo)
            else:
                domanda_negozio_gg = domanda_gg
                domanda_vending_gg = 0.0
            lotto = lot_for(prodotto.get("categoria", "ACCESSORI"), params)
            reserve = max(0, int(params.get("SCORTA_MINIMA_NEGOZIO_VENDING", 2) or 0)) if giac_vending > 0 or prodotto.get("colonna_vending") else 0
            supply = pending.get(codice, {})
            supply_anomalies = supply.get("anomalie", [])
            anomalia = anomalia_stock or bool(supply_anomalies)
            incoming = int(supply.get("quantita_in_arrivo", 0))
            undated = int(supply.get("quantita_senza_data", 0))
            receipts = supply.get("ricezioni", [])
            copertura_negozio_gg = magazzino_reale / domanda_negozio_gg if domanda_negozio_gg > 0 else None
            copertura_vending_gg = giac_vending / domanda_vending_gg if domanda_vending_gg > 0 else None
            coperture = [value for value in (copertura_negozio_gg, copertura_vending_gg) if value is not None]
            copertura_gg = min(coperture) if coperture else None
            target_days = planning_days if lead_time > 0 else gg_target
            target_scorta = math.ceil(domanda_gg * target_days * fatt) + reserve if domanda_gg > 0 else 0
            scorta_sicurezza = max(0, target_scorta - reserve - math.ceil(domanda_gg * target_days))
            punto_riordino = target_scorta if lead_time > 0 else math.ceil(domanda_gg * gg_min)
            # An order is usable for target coverage only if due within the planning horizon.
            inventory_position = giac_tot + incoming
            fabbisogno_grezzo = max(0, target_scorta - inventory_position)
            shortage_day = projected_shortage(giac_tot, domanda_gg, receipts, planning_today, int(target_days)) if domanda_gg > 0 else None
            shop_shortage_day = projected_shortage(giac_negozio, domanda_negozio_gg, receipts, planning_today, int(target_days)) if domanda_negozio_gg > 0 else None
            vending_shortage_day = projected_shortage(giac_vending, domanda_vending_gg, [], planning_today, int(target_days)) if domanda_vending_gg > 0 else None

            stato = "NESSUN ORDINE"
            motivo = "NESSUNA VENDITA RECENTE"
            qta = 0
            if anomalia_stock:
                stato = "ANOMALIA"
                motivo = f"STOCK NEGOZIO FISICO NEGATIVO ({giac_negozio}) · VERIFICARE IMPORT/MOVIMENTI"
            elif supply_anomalies:
                stato = "ANOMALIA"
                motivo = "ORDINE FORNITORE CON QUANTITÀ INCOERENTI · VERIFICARE RICEZIONI"
            elif venduto_lungo <= 0:
                if magazzino_reale < lotto:
                    stato = "CONTROLLO MANUALE"
                    motivo = (
                        f"SCORTA {magazzino_reale} SOTTO LOTTO {lotto} · "
                        f"NESSUNA VENDITA IN {finestra_lunga}GG"
                    )
                else:
                    motivo = f"NESSUNA VENDITA NEGLI ULTIMI {finestra_lunga}GG"
            elif sales and sales["anomalie_riconciliazione"]:
                stato = "CONTROLLO MANUALE"
                motivo = "VENDITE CON FONTI SOVRAPPOSTE O RETTIFICHE · VERIFICARE RICONCILIAZIONE"
            elif undated and giac_tot < target_scorta:
                stato = "CONTROLLO MANUALE"
                motivo = f"{undated} PEZZI ORDINATI SENZA DATA FUTURA AFFIDABILE · VERIFICARE FORNITORE"
            elif incoming and shortage_day and fabbisogno_grezzo == 0:
                stato = "CONTROLLO MANUALE"
                motivo = f"FORNITURA GIÀ ORDINATA MA STOCK IN ESAURIMENTO IL {shortage_day} · SOLLECITARE CONSEGNA"
            elif copertura_negozio_gg is not None and copertura_negozio_gg < gg_min and fabbisogno_grezzo == 0 and giac_vending > 0 and not incoming:
                stato = "CONTROLLO MANUALE"
                motivo = "NEGOZIO SOTTO SOGLIA; SCORTA VENDING NON DISPONIBILE DIRETTAMENTE IN NEGOZIO"
            elif copertura_vending_gg is not None and copertura_vending_gg < gg_min and fabbisogno_grezzo == 0 and giac_negozio > reserve:
                stato = "CONTROLLO MANUALE"
                motivo = "VENDING SOTTO SOGLIA; VERIFICARE RICARICA DAL NEGOZIO"
            elif fabbisogno_grezzo > 0 and ((lead_time > 0 and inventory_position < punto_riordino) or (lead_time == 0 and copertura_gg is not None and copertura_gg < gg_min)):
                stato = "ORDINA ORA"
                motivo = (f"POSIZIONE {inventory_position} < PUNTO RIORDINO {punto_riordino}" if lead_time > 0 else f"COPERTURA {copertura_gg:.2f}gg < {int(gg_min)}gg") + f" · ARRIVO {incoming} · FABBISOGNO {fabbisogno_grezzo}"
                qta = rounded_order_quantity(fabbisogno_grezzo, lotto)
            elif incoming and fabbisogno_grezzo == 0:
                stato = "ORDINE IN ARRIVO"
                motivo = f"{incoming} PEZZI IN ARRIVO ENTRO {int(target_days)}GG"
            elif copertura_gg is not None and copertura_gg < gg_target:
                stato = "MONITORA"
                motivo = f"COPERTURA {copertura_gg:.2f}gg TRA {int(gg_min)} E {int(gg_target)}gg"
            elif copertura_gg is not None:
                motivo = f"COPERTURA SUFFICIENTE ({copertura_gg:.2f}gg)"
            if domanda_fallback_lunga and not anomalia:
                motivo += f" · DOMANDA DA {finestra_lunga}GG"
            if shortage_day and receipts:
                motivo += f" · RISCHIO ESAURIMENTO IL {shortage_day} PRIMA DELLA CONSEGNA"
            if shop_shortage_day and receipts and shop_shortage_day != shortage_day:
                motivo += f" · RISCHIO NEGOZIO IL {shop_shortage_day}"
            if vending_shortage_day and vending_shortage_day != shortage_day:
                motivo += f" · RISCHIO VENDING IL {vending_shortage_day}"
            if anomalia_domanda:
                motivo += " · DOMANDA IN FORTE AUMENTO: VERIFICARE"

            row = {
                "codice": codice,
                "descrizione": prodotto.get("descrizione", ""),
                "categoria": prodotto.get("categoria", ""),
                "stato": stato,
                "anomalia": anomalia,
                "anomalie_forniture": supply_anomalies,
                "qta_da_ordinare": qta,
                "giacenza": giac_tot,
                "giacenza_negozio": giac_negozio,
                "venduti_vending": venduti_vending,
                "giacenza_vending": giac_vending,
                "auto_order_version": int(prodotto.get("auto_order_version", 0) or 0),
                "magazzino_reale_lordo": magazzino_reale_lordo,
                "magazzino_reale": magazzino_reale,
                "venduto_periodo": venduto_breve,
                "finestra_domanda_gg": finestra_breve,
                "venduto_breve": venduto_breve,
                "venduto_lungo": venduto_lungo,
                "venduto_10gg": venduto_breve,
                "venduto_30gg": venduto_lungo,
                "fonte_domanda": fonte_domanda,
                "venduto_negozio": (sales or {}).get("venduto_negozio"),
                "venduto_vending": (sales or {}).get("venduto_vending"),
                "venduto_totale": (sales or {}).get("venduto_totale", venduto_lungo),
                "fonte_prevalente": fonte_domanda,
                "fonti_utilizzate": (sales or {}).get("fonti_utilizzate", [fonte_domanda]),
                "periodo_coperto": (sales or {}).get("periodo_coperto"),
                "qualita_dati": (sales or {}).get("qualita_dati", "DATI_AFFIDABILI"),
                "anomalie_riconciliazione": (sales or {}).get("anomalie_riconciliazione", []),
                "domanda_gg_10": round(domanda_breve_gg, 3),
                "domanda_gg_30": round(domanda_lunga_gg, 3),
                "domanda_precedente_gg": round(domanda_precedente_gg, 3) if domanda_precedente_gg is not None else None,
                "domanda_gg": round(domanda_gg, 3),
                "domanda_negozio_gg": round(domanda_negozio_gg, 3),
                "domanda_vending_gg": round(domanda_vending_gg, 3),
                "domanda_fallback_lunga": domanda_fallback_lunga,
                "classe_rotazione": classe_rotazione,
                "anomalia_domanda": anomalia_domanda,
                "copertura_gg": round(copertura_gg, 2) if copertura_gg is not None else None,
                "copertura_negozio_gg": round(copertura_negozio_gg, 2) if copertura_negozio_gg is not None else None,
                "copertura_vending_gg": round(copertura_vending_gg, 2) if copertura_vending_gg is not None else None,
                "target_scorta": target_scorta,
                "punto_riordino": punto_riordino,
                "scorta_sicurezza": scorta_sicurezza,
                "scorta_riservata": reserve,
                "fabbisogno_grezzo": fabbisogno_grezzo,
                "quantita_in_arrivo": incoming,
                "quantita_senza_data": undated,
                "ricezioni_previste": receipts,
                "posizione_inventariale": inventory_position,
                "data_rischio_esaurimento": shortage_day,
                "data_rischio_esaurimento_negozio": shop_shortage_day,
                "data_rischio_esaurimento_vending": vending_shortage_day,
                "modalita_pianificazione": "LEAD_TIME" if lead_time > 0 else "LEGACY",
                "media_ordini_storico": media_ord,
                "n_ordini_storici": n_ord,
                "lotto_ordine": lotto,
                "motivo": motivo,
                "prezzo": prodotto.get("prezzo", 0),
                "totale": multiply_money(prodotto.get("prezzo") or 0, qta),
                "costo_unitario_acquisto": prodotto.get("costo_acquisto"),
                "totale_costo_stimato": multiply_money(prodotto["costo_acquisto"], qta) if prodotto.get("costo_acquisto") is not None else None,
            }
            if stato == "ORDINA ORA" and not anomalia and qta > 0:
                proposte.append(row)
            else:
                esclusi.append(row)

        proposte.sort(key=lambda row: (-row["totale"], row["codice"]))
        ordine_stati = {"ANOMALIA": 0, "CONTROLLO MANUALE": 1, "ORDINE IN ARRIVO": 2, "MONITORA": 3, "NESSUN ORDINE": 4}
        esclusi.sort(key=lambda row: (ordine_stati.get(row["stato"], 9), row["descrizione"], row["codice"]))
        totale = sum_money(row["totale"] for row in proposte)
        riepilogo_stati = {"ORDINA ORA": len(proposte)}
        for row in esclusi:
            riepilogo_stati[row["stato"]] = riepilogo_stati.get(row["stato"], 0) + 1
        n_anomalie_stock = sum(1 for row in [*proposte, *esclusi] if row["giacenza_negozio"] < 0)
        n_anomalie_forniture = sum(1 for row in [*proposte, *esclusi] if row["anomalie_forniture"])
        watermarks = {
            "ultima_vendita_nativa": (latest_app or {}).get("data"),
            "ultima_vendita_csv_vending": (latest_csv or {}).get("data"),
            "ultima_ricezione_merce": (latest_receipt or {}).get("completed_at"),
            "ultima_riconciliazione": datetime.now(timezone.utc).isoformat(),
        }
        if n_anomalie_forniture:
            data_quality = "DATI_INCOERENTI"
        elif not latest_sale_day or (reference_day - latest_sale_day).days >= finestra_lunga or any(row.get("qualita_dati") == "DATI_PARZIALI" for row in [*proposte, *esclusi]):
            data_quality = "DATI_PARZIALI"
        else:
            data_quality = "DATI_AFFIDABILI"
        inventory_version = hashlib.sha256(json.dumps(
            sorted((row["codice"], row["giacenza_negozio"], row["giacenza_vending"], row["auto_order_version"]) for row in [*proposte, *esclusi]),
            separators=(",", ":"), default=str,
        ).encode("utf-8")).hexdigest()
        parameters_version = hashlib.sha256(json.dumps(relevant_auto_order_params(params), sort_keys=True, default=str).encode("utf-8")).hexdigest()
        response = {
            "righe": proposte,
            "esclusi": esclusi,
            "totale": totale,
            "totale_costo_stimato": sum_money(row["totale_costo_stimato"] for row in proposte if row["totale_costo_stimato"] is not None),
            "costi_non_disponibili": sum(1 for row in proposte if row["totale_costo_stimato"] is None),
            "n_righe": len(proposte),
            "n_anomalie_stock": n_anomalie_stock,
            "n_anomalie_forniture": n_anomalie_forniture,
            "data_quality": data_quality,
            "source_watermarks": watermarks,
            "inventory_version": inventory_version,
            "parameters_version": parameters_version,
            "orders_version": orders_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "planning_day": planning_today.isoformat(),
            "riepilogo_stati": riepilogo_stati,
            "parametri": params,
            "finestra_domanda_gg": finestra_breve,
            "finestra_breve_gg": finestra_breve,
            "finestra_lunga_gg": finestra_lunga,
            "peso_breve": peso_breve,
            "peso_lungo": peso_lungo,
            "fattore_sicurezza": fatt,
            "data_riferimento_domanda": reference_day.isoformat(),
            "giorni_ritardo_dati": max(0, (reference_day - latest_sale_day).days) if latest_sale_day else None,
        }
        response["snapshot_key"] = auto_order_snapshot_key(response)
        return response

    def auto_order_selected_rows(
        ao: Dict[str, Any], requested: Optional[List[AutoOrderRigaIn]] = None
    ) -> List[Dict[str, Any]]:
        if requested is None:
            return [
                dict(row)
                for row in ao["righe"]
                if not row.get("anomalia") and int(row.get("qta_da_ordinare") or 0) > 0
            ]
        if not requested:
            raise HTTPException(422, "Aggiungi almeno un articolo all'ordine")

        available = {
            row["codice"]: row
            for row in [*ao.get("righe", []), *ao.get("esclusi", [])]
            if row.get("codice") and not row.get("anomalia") and not row.get("anomalie_riconciliazione")
            and row.get("stato") not in {"CONTROLLO MANUALE", "ORDINE IN ARRIVO"}
        }
        seen = set()
        selected = []
        for item in requested:
            codice = item.codice.strip()
            if codice in seen:
                raise HTTPException(422, f"Articolo duplicato nella selezione: {codice}")
            row = available.get(codice)
            if not row:
                raise HTTPException(422, f"Articolo non ordinabile: {codice}")
            seen.add(codice)
            quantity = int(item.quantita)
            lot = int(row.get("lotto_ordine") or 1)
            if lot <= 0 or quantity % lot:
                lower = (quantity // max(1, lot)) * lot
                upper = lower + max(1, lot)
                raise HTTPException(422, f"Quantità {quantity} non multipla del lotto {lot} per {codice}. Valori validi: {lower} o {upper}.")
            selected.append(
                {
                    **row,
                    "stato": "ORDINA ORA",
                    "qta_da_ordinare": quantity,
                    "totale": multiply_money(row.get("prezzo") or 0, quantity),
                    "totale_costo_stimato": multiply_money(row["costo_unitario_acquisto"], quantity) if row.get("costo_unitario_acquisto") is not None else None,
                }
            )
        return selected

    def auto_order_rows_from_query(ao: Dict[str, Any], selection: Optional[str]) -> List[Dict[str, Any]]:
        if not selection:
            return auto_order_selected_rows(ao)
        try:
            raw = json.loads(selection)
            requested = [AutoOrderRigaIn.model_validate(item) for item in raw]
        except Exception as exc:
            raise HTTPException(422, "Selezione Auto-Order non valida") from exc
        return auto_order_selected_rows(ao, requested)

    @router.post("/auto-order/conferma")
    async def conferma_auto_order(
        body: Optional[AutoOrderConfermaIn] = None,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ):
        db = get_database()
        requested_rows = body.righe if body else None
        supplied_snapshot = body.snapshot_key if body else None
        if requested_rows is not None and not supplied_snapshot:
            raise HTTPException(422, "Snapshot Auto-Order obbligatorio per confermare una selezione")
        if body and body.data_consegna_prevista and body.data_consegna_prevista <= datetime.now(ROME).date():
            raise HTTPException(422, "La consegna prevista deve essere futura")
        request_fingerprint = hashlib.sha256(json.dumps({
            "snapshot_key": supplied_snapshot,
            "righe": [row.model_dump() for row in requested_rows] if requested_rows is not None else None,
            "fornitore": (body.fornitore or "").strip() if body else "",
            "data_consegna_prevista": body.data_consegna_prevista.isoformat() if body and body.data_consegna_prevista else None,
        }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        header_key = idempotency_key if isinstance(idempotency_key, str) else None
        explicit_key = header_key or (body.idempotency_key if body else None) or (body.batch_key if body else None)
        if not explicit_key and supplied_snapshot and requested_rows is not None:
            early_selection = hashlib.sha256(json.dumps(
                [{"codice": item.codice.strip(), "quantita": item.quantita} for item in requested_rows],
                ensure_ascii=False, sort_keys=True,
            ).encode("utf-8")).hexdigest()[:24]
            explicit_key = f"{supplied_snapshot}:selezione:{early_selection}"

        def replay(existing):
            if existing.get("request_fingerprint") != request_fingerprint:
                raise HTTPException(409, "Chiave idempotente già usata con contenuto diverso")
            if existing.get("status") != "COMPLETED":
                raise HTTPException(409, "Ordine ancora in elaborazione o incompleto")
            return {"ok": True, **existing, "duplicate": True}

        if explicit_key:
            explicit_key = explicit_key.strip()
            if len(explicit_key) > 200:
                raise HTTPException(422, "Chiave idempotente troppo lunga")
            existing = await db.ordini_fornitore.find_one({"batch_key": explicit_key}, {"_id": 0})
            if existing:
                return replay(existing)
        ao = await get_auto_order()
        if ao.get("data_quality") == "DATI_INCOERENTI":
            raise HTTPException(409, "Dati Auto-Order incoerenti: correggere le anomalie prima di confermare")
        if supplied_snapshot and supplied_snapshot != ao["snapshot_key"]:
            raise HTTPException(409, "I dati di magazzino o vendita sono cambiati. Ricalcola Auto-Order prima di confermare.")
        righe = auto_order_selected_rows(ao, requested_rows)
        if not righe:
            raise HTTPException(422, "Nessun articolo da confermare")
        selection_key = hashlib.sha256(
            json.dumps(
                [{"codice": row["codice"], "quantita": row["qta_da_ordinare"]} for row in righe],
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:24]
        default_key = (
            f"{ao['snapshot_key']}:selezione:{selection_key}"
            if requested_rows is not None
            else ao["snapshot_key"]
        )
        batch_key = (
            explicit_key
            or default_key
        ).strip()
        if not batch_key:
            batch_key = ao["snapshot_key"]
        existing = await db.ordini_fornitore.find_one({"batch_key": batch_key}, {"_id": 0})
        if existing:
            return replay(existing)

        batch = {
            "id": str(uuid.uuid4()),
            "batch_key": batch_key,
            "snapshot_key": ao["snapshot_key"],
            "data": datetime.now(timezone.utc).isoformat(),
            "stato": "CONFERMATO",
            "version": 0,
            "status": "PROCESSING",
            "request_fingerprint": request_fingerprint,
            "origine": "AUTO-ORDER",
            "ordinati": len(righe),
            "totale": sum_money(row.get("totale", 0) or 0 for row in righe),
            "data_riferimento_domanda": ao.get("data_riferimento_domanda"),
            "fornitore": (body.fornitore or "").strip() if body else "",
            "data_consegna_prevista": body.data_consegna_prevista.isoformat() if body and body.data_consegna_prevista else None,
            "totale_costo_stimato": sum_money(row["totale_costo_stimato"] for row in righe if row.get("totale_costo_stimato") is not None) if all(row.get("totale_costo_stimato") is not None for row in righe) else None,
            "costi_non_disponibili": sum(1 for row in righe if row.get("totale_costo_stimato") is None),
        }
        async def persist(session):
            kwargs = {"session": session} if session is not None else {}
            locked_products = []
            await db.ordini_fornitore.insert_one(batch.copy(), **kwargs)
            try:
                for row in righe:
                    if "auto_order_version" not in row:
                        continue  # compatibility for legacy in-process callers
                    expected = int(row["auto_order_version"])
                    version_filter = {"auto_order_version": expected} if expected else {"$or": [{"auto_order_version": 0}, {"auto_order_version": {"$exists": False}}]}
                    locked = await db.prodotti.update_one(
                        {"codice": row["codice"], "giacenza_negozio": row["giacenza_negozio"],
                         "prezzo": row.get("prezzo", 0), **version_filter},
                        {"$inc": {"auto_order_version": 1}},
                        **kwargs,
                    )
                    if locked.matched_count != 1:
                        raise HTTPException(409, "Giacenza o prodotto cambiato durante la conferma. Ricalcola Auto-Order.")
                    locked_products.append((row["codice"], expected + 1))
                for index, row in enumerate(righe, start=1):
                    document = {
                    "id": str(uuid.uuid4()),
                    "batch_id": batch["id"],
                    "batch_key": batch_key,
                    "riga": index,
                    "codice": row["codice"],
                    "descrizione": row.get("descrizione", ""),
                    "categoria": row.get("categoria", ""),
                    "quantita": int(row["qta_da_ordinare"]),
                    "quantita_ordinata": int(row["qta_da_ordinare"]),
                    "quantita_ricevuta": 0,
                    "quantita_residua": int(row["qta_da_ordinare"]),
                    "stato": "CONFERMATO",
                    "data_ordine": batch["data"],
                    "data_consegna_prevista": batch["data_consegna_prevista"],
                    "fornitore": batch["fornitore"],
                    "prezzo": normalize_money(row.get("prezzo") or 0),
                    "totale": normalize_money(row.get("totale") or 0),
                    "costo_unitario_acquisto": row.get("costo_unitario_acquisto"),
                    "totale_costo_stimato": row.get("totale_costo_stimato"),
                    "motivo": row.get("motivo", ""),
                    }
                    await db.ordini_fornitore_righe.insert_one(document, **kwargs)
                completed = await db.ordini_fornitore.update_one(
                    {"id": batch["id"], "status": "PROCESSING"},
                    {"$set": {"status": "COMPLETED", "completed_at": datetime.now(timezone.utc).isoformat()}},
                    **kwargs,
                )
                if completed.matched_count != 1:
                    raise RuntimeError("Batch ordine cambiato durante il salvataggio")
            except Exception:
                if session is None:
                    try:
                        await db.ordini_fornitore_righe.delete_many({"batch_id": batch["id"]})
                        deleted = await db.ordini_fornitore.delete_one({"id": batch["id"], "status": "PROCESSING"})
                        if deleted is None or deleted.deleted_count == 1:
                            for code, version in reversed(locked_products):
                                await db.prodotti.update_one(
                                    {"codice": code, "auto_order_version": version},
                                    {"$inc": {"auto_order_version": -1}},
                                )
                    except Exception:
                        logging.exception("auto-order rollback failed for batch %s", batch_key)
                raise
            return {"ok": True, **batch, "status": "COMPLETED", "duplicate": False}

        try:
            return await run_transactionally(persist)
        except DuplicateKeyError as exc:
            existing = await db.ordini_fornitore.find_one({"batch_key": batch_key}, {"_id": 0})
            if existing:
                return replay(existing)
            raise HTTPException(409, "Ordine già in elaborazione") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, "Creazione batch Auto-Order non completata") from exc

    async def export_rows(ao, selezione, snapshot_key, batch_key):
        if batch_key:
            db = get_database()
            batch = await db.ordini_fornitore.find_one({"batch_key": batch_key}, {"_id": 0})
            if not batch or batch.get("status", "COMPLETED") != "COMPLETED":
                raise HTTPException(409, "Ordine confermato non trovato o incompleto")
            lines = await db.ordini_fornitore_righe.find({"batch_key": batch_key}, {"_id": 0}).sort("riga", 1).to_list(None)
            if len(lines) != int(batch.get("ordinati", -1)):
                raise HTTPException(409, "Righe ordine fornitore incomplete")
            return [{**line, "qta_da_ordinare": int(line["quantita"])} for line in lines], batch
        if snapshot_key and snapshot_key != ao.get("snapshot_key"):
            raise HTTPException(409, "Proposta cambiata: ricalcola Auto-Order prima di esportare")
        return auto_order_rows_from_query(ao, selezione), None

    @router.get("/auto-order/pdf")
    async def auto_order_pdf(
        fornitore: Optional[str] = "Fornitore",
        categoria: Optional[str] = None,
        selezione: Optional[str] = None,
        snapshot_key: Optional[str] = None,
        batch_key: Optional[str] = None,
    ):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle
        from xml.sax.saxutils import escape as xml_escape

        fornitore_safe = xml_escape((fornitore or "Fornitore")[:120])
        categoria_filtro = (categoria or "").strip()[:120]
        categoria_safe = xml_escape(categoria_filtro)

        ao = await get_auto_order()
        righe, exported_batch = await export_rows(ao, selezione, snapshot_key, batch_key)
        if categoria_filtro:
            righe = [row for row in righe if row.get("categoria") == categoria_filtro]
        totale = sum_money(row.get("totale", 0) or 0 for row in righe)

        buffer = io.BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=15 * mm,
            rightMargin=15 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "t",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=18,
            textColor=colors.HexColor("#0F172A"),
            spaceAfter=2,
        )
        subtitle_style = ParagraphStyle(
            "s",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=colors.HexColor("#64748B"),
            spaceAfter=12,
        )
        cell_style = ParagraphStyle(
            "cell",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7,
            leading=8,
            textColor=colors.HexColor("#0F172A"),
        )

        story = [Paragraph("ORDINE FORNITORE - GOD SERVICES", title_style)]
        today = datetime.now(ROME).strftime("%d/%m/%Y")
        filter_label = f" &nbsp;·&nbsp; Selezione: <b>{categoria_safe}</b>" if categoria_filtro else ""
        story.append(
            Paragraph(
                f"Destinatario: <b>{fornitore_safe}</b> &nbsp;·&nbsp; Data: <b>{today}</b>"
                f"{filter_label} &nbsp;·&nbsp; Righe: <b>{len(righe)}</b> &nbsp;·&nbsp; "
                f"Totale: <b>€ {totale:.2f}</b>",
                subtitle_style,
            )
        )

        data = [["CODICE", "ARTICOLO", "QTA", "TOTALE"]]
        for row in righe:
            data.append(
                [
                    row["codice"],
                    Paragraph(xml_escape(row["descrizione"] or ""), cell_style),
                    str(row["qta_da_ordinare"]),
                    f"€ {row['totale']:.2f}",
                ]
            )
        data.append(["", "", "TOTALE", f"€ {totale:.2f}"])

        table = Table(data, colWidths=[25 * mm, 105 * mm, 15 * mm, 35 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 8),
                    ("ALIGN", (0, 0), (-1, 0), "LEFT"),
                    ("ALIGN", (2, 1), (3, -1), "RIGHT"),
                    ("FONTNAME", (0, 1), (-1, -2), "Helvetica"),
                    ("FONTSIZE", (0, 1), (-1, -1), 8),
                    ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#0F172A")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#0F172A")),
                    ("LINEBELOW", (0, -2), (-1, -2), 0.5, colors.HexColor("#E2E8F0")),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F1F5F9")),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, -1), (-1, -1), 9),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)
        footer = Paragraph(
            "Calcolo Auto-Order: domanda ponderata {peso_breve:.0f}% ultimi {breve}gg + "
            "{peso_lungo:.0f}% ultimi {lungo}gg fino al {riferimento}; giacenza negozio = "
            "stock fisico libero gia normalizzato dall'import; ordine sotto {copertura}gg "
            "verso target {target}gg con sicurezza x{fattore:.2f}. "
            "Riferimento: {riferimento_ordine}.".format(
                peso_breve=float(ao.get("peso_breve", 0.70)) * 100,
                peso_lungo=float(ao.get("peso_lungo", 0.30)) * 100,
                breve=int(ao.get("finestra_breve_gg", 10)),
                lungo=int(ao.get("finestra_lunga_gg", 30)),
                riferimento=datetime.fromisoformat(ao["data_riferimento_domanda"]).strftime("%d/%m/%Y"),
                copertura=int(ao["parametri"].get("GIORNI_COPERTURA_MIN", 7)),
                target=int(ao["parametri"].get("GIORNI_COPERTURA_TARGET", 14)),
                fattore=float(ao.get("fattore_sicurezza", 1.15)),
                riferimento_ordine=xml_escape((exported_batch or {}).get("batch_key") or "bozza"),
            ),
            subtitle_style,
        )

        def draw_footer(canvas, current_document):
            footer.wrap(current_document.width, 14 * mm)
            footer.drawOn(canvas, current_document.leftMargin, 4 * mm)

        document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
        buffer.seek(0)
        categoria_slug = re.sub(r"[^a-z0-9]+", "_", categoria_filtro.lower()).strip("_")
        suffix = f"_{categoria_slug}" if categoria_slug else ""
        filename = f"ordine{suffix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.pdf"
        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "X-Auto-Order-Category": categoria_filtro or "TUTTE",
                "X-Auto-Order-Rows": str(len(righe)),
            },
        )

    @router.get("/auto-order/excel")
    async def auto_order_excel(
        fornitore: Optional[str] = "Fornitore",
        categoria: Optional[str] = None,
        selezione: Optional[str] = None,
        snapshot_key: Optional[str] = None,
        batch_key: Optional[str] = None,
    ):
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        fornitore_testo = (fornitore or "Fornitore").strip()[:120] or "Fornitore"
        categoria_filtro = (categoria or "").strip()[:120]

        ao = await get_auto_order()
        righe, exported_batch = await export_rows(ao, selezione, snapshot_key, batch_key)
        if categoria_filtro:
            righe = [row for row in righe if row.get("categoria") == categoria_filtro]
        totale = sum_money(row.get("totale", 0) or 0 for row in righe)

        def excel_text(value: Any) -> str:
            text = str(value or "")
            return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Ordine fornitore"
        worksheet.sheet_view.showGridLines = False
        worksheet.freeze_panes = "A5"

        dark_fill = PatternFill("solid", fgColor="0F172A")
        light_fill = PatternFill("solid", fgColor="F1F5F9")
        white_bold = Font(color="FFFFFF", bold=True)

        worksheet.merge_cells("A1:D1")
        worksheet["A1"] = "ORDINE FORNITORE - GOD SERVICES"
        worksheet["A1"].font = Font(size=16, bold=True, color="0F172A")
        worksheet["A1"].alignment = Alignment(vertical="center")
        worksheet.row_dimensions[1].height = 26

        worksheet["A2"] = "Destinatario"
        worksheet["B2"] = excel_text(fornitore_testo)
        worksheet["C2"] = "Data"
        worksheet["D2"] = datetime.now(ROME).date()
        worksheet["D2"].number_format = "dd/mm/yyyy"
        worksheet["A3"] = "Selezione"
        worksheet["B3"] = excel_text(categoria_filtro or "Tutte le categorie")
        worksheet["C3"] = "Righe"
        worksheet["D3"] = len(righe)
        for cell in (worksheet["A2"], worksheet["C2"], worksheet["A3"], worksheet["C3"]):
            cell.font = Font(bold=True, color="475569")

        header_row = 4
        for column, label in enumerate(["CODICE", "ARTICOLO", "QTA", "TOTALE"], start=1):
            cell = worksheet.cell(row=header_row, column=column, value=label)
            cell.fill = dark_fill
            cell.font = white_bold
            cell.alignment = Alignment(horizontal="right" if column >= 3 else "left")

        first_data_row = header_row + 1
        for row_index, row in enumerate(righe, start=first_data_row):
            worksheet.cell(row=row_index, column=1, value=excel_text(row.get("codice")))
            worksheet.cell(row=row_index, column=2, value=excel_text(row.get("descrizione")))
            worksheet.cell(row=row_index, column=3, value=int(row.get("qta_da_ordinare") or 0))
            total_cell = worksheet.cell(row=row_index, column=4, value=float(row.get("totale") or 0))
            total_cell.number_format = "€ #,##0.00"
            worksheet.cell(row=row_index, column=3).alignment = Alignment(horizontal="right")
            total_cell.alignment = Alignment(horizontal="right")

        total_row = first_data_row + len(righe)
        worksheet.cell(row=total_row, column=3, value="TOTALE")
        worksheet.cell(row=total_row, column=4, value=totale)
        for column in range(1, 5):
            cell = worksheet.cell(row=total_row, column=column)
            cell.fill = light_fill
            cell.font = Font(bold=True, color="0F172A")
        worksheet.cell(row=total_row, column=4).number_format = "€ #,##0.00"
        worksheet.cell(row=total_row, column=3).alignment = Alignment(horizontal="right")
        worksheet.cell(row=total_row, column=4).alignment = Alignment(horizontal="right")

        last_filter_row = max(header_row, total_row - 1)
        worksheet.auto_filter.ref = f"A{header_row}:D{last_filter_row}"
        for column, width in {1: 20, 2: 55, 3: 14, 4: 18}.items():
            worksheet.column_dimensions[get_column_letter(column)].width = width

        workbook.properties.title = "Ordine fornitore"
        workbook.properties.subject = categoria_filtro or "Tutte le categorie"
        workbook.properties.creator = "God Services Gestionale"
        metadata = workbook.create_sheet("Metadati Auto-Order")
        metadata.append(["Campo", "Valore"])
        metadata.append(["origine", "AUTO-ORDER"])
        metadata.append(["stato", "CONFERMATO" if exported_batch else "BOZZA"])
        metadata.append(["snapshot_key", excel_text((exported_batch or {}).get("snapshot_key") or ao.get("snapshot_key"))])
        metadata.append(["batch_key", excel_text((exported_batch or {}).get("batch_key") or "")])
        metadata.append(["data_generazione_utc", datetime.now(timezone.utc).isoformat()])

        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        categoria_slug = re.sub(r"[^a-z0-9]+", "_", categoria_filtro.lower()).strip("_")
        suffix = f"_{categoria_slug}" if categoria_slug else ""
        filename = f"ordine{suffix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "X-Auto-Order-Category": categoria_filtro or "TUTTE",
                "X-Auto-Order-Rows": str(len(righe)),
            },
        )

    return {
        "router": router,
        "auto_order": auto_order,
        "list_forniture": list_forniture,
        "carichi_esistenti": carichi_esistenti,
        "chiudi_con_carico": chiudi_con_carico,
        "update_fornitura": update_fornitura,
        "auto_order_selected_rows": auto_order_selected_rows,
        "auto_order_rows_from_query": auto_order_rows_from_query,
        "conferma_auto_order": conferma_auto_order,
        "auto_order_pdf": auto_order_pdf,
        "auto_order_excel": auto_order_excel,
    }

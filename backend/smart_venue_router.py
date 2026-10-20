"""Smart Venue import, reconciliation and CRUD routes.

Dependencies are callbacks on purpose: the application and tests replace the
database and import services at runtime.
"""
import asyncio
import io
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from pymongo import UpdateOne

try:
    from .money import NonNegativeMoney
except ImportError:  # pragma: no cover - direct module execution
    from money import NonNegativeMoney


class SmartVenueProductIn(BaseModel):
    codice: str
    descrizione: str
    barcode: str = ""
    categoria: str = "ACCESSORI"
    prezzo: NonNegativeMoney = 0
    acquistati: int = 0
    giacenza_negozio: int = 0
    giacenza_vending: int = 0
    smart_venue: int = 0


class SmartVenueBarcodeIn(BaseModel):
    barcode: str = ""


class SmartVenueBulkIn(BaseModel):
    testo: str

class SmartVenueEntryIn(BaseModel):
    quantita: int
    motivazione: str
    idempotency_key: str
    saldo_atteso: int
    differenza_attesa: int
    rettifica_id: Optional[str] = None


def create_smart_venue_router(*, get_database, read_capped, create_backup_snapshot,
                              record_import_history, bulk_upsert, product_model,
                              product_categories, product_code_text,
                              product_identity_tokens, adm_numeric_code,
                              adm_local_code, nonnegative_money, money_product,
                              vending_stock_by_code, run_transactionally, session_kwargs,
                              request_operator, record_audit_event):
    router = APIRouter()

    async def import_smart_venue(worksheet) -> Dict[str, int]:
        db = get_database()
        rows_by_code: Dict[str, Dict[str, Any]] = {}
        errors = 0
        for row_number, row in enumerate(worksheet.iter_rows(min_row=3, values_only=True), start=3):
            try:
                if not row or not row[0]:
                    continue
                code = product_code_text(row[0])
                if not code:
                    continue
                rows_by_code[code] = {"id": f"smart-venue:{code}", "codice": code,
                    "descrizione": str(row[1] or "").strip(),
                    "smart_venue": int(row[5] or 0) if len(row) > 5 else 0,
                    "origine": "EXCEL", "barcode": str(row[4] or "").strip() if len(row) > 4 else "",
                    "riga_excel": row_number, "updated_at": datetime.now(timezone.utc).isoformat()}
            except (TypeError, ValueError):
                errors += 1
        manual_rows = await db.smart_venue.find({"origine": "MANUALE"}, {"_id": 0}).to_list(5000)
        for row in manual_rows:
            code = str(row.get("codice") or "")
            if code and code not in rows_by_code:
                rows_by_code[code] = row
        await db.smart_venue.delete_many({})
        if rows_by_code:
            await db.smart_venue.insert_many(list(rows_by_code.values()))
        return {"inseriti": len(rows_by_code), "aggiornati": 0, "errori": errors}

    def parse_bulk_text(text: str) -> Dict[str, Any]:
        lines = [line.strip() for line in str(text or "").replace("\r", "").split("\n") if line.strip()]
        errors, grouped = [], {}
        if not lines:
            return {"righe": [], "errori": [{"riga": 1, "errore": "Nessun dato incollato"}], "record": 0}
        if len(lines) % 4:
            errors.append({"riga": len(lines) - len(lines) % 4 + 1, "errore": "Il testo deve contenere blocchi completi di 4 righe"})
        for start in range(0, len(lines) - len(lines) % 4, 4):
            header, price_raw, quantity_raw, value_raw = lines[start:start + 4]
            try:
                left, adm_code, category = header.rsplit(" - ", 2)
                barcode, description = left.split(" - ", 1)
                numeric_code = adm_numeric_code(adm_code)
                if not numeric_code:
                    raise ValueError("codice ADM mancante")
                code = adm_local_code(numeric_code)
                price = nonnegative_money(price_raw)
                quantity_float = float(quantity_raw.replace(",", "."))
                if not quantity_float.is_integer() or quantity_float < 0:
                    raise ValueError("quantità non valida")
                quantity = int(quantity_float)
                total_value = nonnegative_money(value_raw)
                expected_value = money_product(price, quantity)
                current = grouped.setdefault(code, {"codice": code, "descrizione": description.strip(), "categoria": category.strip(), "smart_venue": 0, "barcodes": [], "righe_sorgente": [], "avvisi": []})
                current["smart_venue"] += quantity
                if barcode.strip() and barcode.strip() not in current["barcodes"]:
                    current["barcodes"].append(barcode.strip())
                current["righe_sorgente"].append(start // 4 + 1)
                if abs(expected_value - total_value) > 0.02:
                    current["avvisi"].append(f"valore {total_value:.2f} diverso da prezzo × quantità {expected_value:.2f}")
            except (ValueError, TypeError) as exc:
                errors.append({"riga": start + 1, "errore": str(exc)})
        return {"righe": sorted(grouped.values(), key=lambda row: row["codice"]), "errori": errors, "record": len(lines) // 4}

    def resolve_rows(rows, products):
        by_code = {str(p.get("codice") or ""): p for p in products}
        by_identity = {}
        for product in products:
            identity = product_identity_tokens(product.get("descrizione"))
            if identity: by_identity.setdefault(identity, []).append(product)
        resolved = {}
        for source in rows:
            row = dict(source); requested = str(row.get("codice") or ""); product = by_code.get(requested)
            if product is None:
                candidates = by_identity.get(product_identity_tokens(row.get("descrizione")), [])
                if len(candidates) == 1: product = candidates[0]
            code = str((product or {}).get("codice") or requested); row["codice"] = code
            if code not in resolved:
                row["codici_sorgente"] = [requested] if requested else []; resolved[code] = row; continue
            current = resolved[code]; current["smart_venue"] = max(int(current.get("smart_venue", 0) or 0), int(row.get("smart_venue", 0) or 0))
            for field in ("barcodes", "righe_sorgente", "avvisi", "codici_sorgente"):
                incoming = [requested] if field == "codici_sorgente" and requested else row.get(field, [])
                current[field] = list(dict.fromkeys([*(current.get(field) or []), *incoming]))
        return sorted(resolved.values(), key=lambda row: row["codice"])

    async def pending_by_code():
        rows = await get_database().smart_venue_movimenti.find(
            {"tipo": "CARICO_IN_ENTRATA", "stato": "PENDENTE"}, {"_id": 0, "codice": 1, "quantita": 1}
        ).to_list(5000)
        totals = {}
        for row in rows: totals[row["codice"]] = totals.get(row["codice"], 0) + int(row.get("quantita") or 0)
        return totals

    def reconciliation_status(*, has_product, difference, pending, has_adjustment=False, smart_venue=0, vending=0):
        if not has_product: return "CODICE MANCANTE"
        if pending: return "CARICO DA INSERIRE"
        if difference > 0: return "USCITA DA CONFERMARE"
        if difference < 0 and has_adjustment: return "RETTIFICA DA VERIFICARE"
        if difference < 0 and smart_venue < vending: return "ANOMALIA VENDING"
        if difference < 0: return "ENTRATA DA VERIFICARE"
        return "ALLINEATO"

    async def comparison(rows):
        db = get_database()
        products, vending, pending = await asyncio.gather(db.prodotti.find({}, {"_id": 0}).to_list(5000), vending_stock_by_code(), pending_by_code())
        rows = resolve_rows(rows, products); by_code = {str(p.get("codice") or ""): p for p in products}; vending = vending or {}; result = []
        for row in rows:
            code = str(row.get("codice") or ""); product = by_code.get(code) or next((p for p in products if adm_numeric_code(p.get("codice")) == adm_numeric_code(code)), None)
            code = str((product or {}).get("codice") or code); smart = int(row.get("smart_venue", 0) or 0); vend = int(vending.get(code, 0) or 0); shop = int((product or {}).get("giacenza_negozio", 0) or 0); diff = smart - shop - vend; inferred = smart - vend
            status = reconciliation_status(has_product=bool(product), difference=diff, pending=pending.get(code, 0), smart_venue=smart, vending=vend)
            result.append({**row, "codice": code, "prodotto_trovato": bool(product), "giacenza_vending": vend, "giacenza_negozio": shop, "gestionale_totale": shop + vend, "negozio_calcolato": inferred, "carichi_pendenti": pending.get(code, 0), "differenza": diff, "differenza_residua": diff + pending.get(code, 0), "stato": status})
        return result

    @router.post("/smart-venue/bulk/preview")
    async def preview_smart_venue_bulk(body: SmartVenueBulkIn):
        parsed = parse_bulk_text(body.testo); rows = await comparison(parsed["righe"])
        return {**parsed, "righe": rows, "prodotti": len(rows), "totale_smart_venue": sum(r["smart_venue"] for r in rows), "differenze": sum(r["stato"] != "OK" for r in rows)}

    @router.post("/smart-venue/bulk/import")
    async def import_smart_venue_bulk(body: SmartVenueBulkIn):
        db = get_database(); parsed = parse_bulk_text(body.testo)
        if parsed["errori"]: raise HTTPException(422, {"message": "Correggi gli errori prima di salvare", "errori": parsed["errori"]})
        if not parsed["righe"]: raise HTTPException(422, "Nessun prodotto valido")
        resolved = resolve_rows(parsed["righe"], await db.prodotti.find({}, {"_id": 0}).to_list(5000)); backup = await create_backup_snapshot("Prima import testo SmartVenue", "pre-import-smart-venue-bulk", ["smart_venue", "smart_venue_hidden", "smart_venue_values"]); batch_id = str(uuid.uuid4()); now = datetime.now(timezone.utc).isoformat(); codes = [r["codice"] for r in resolved]
        operations = [UpdateOne({"codice": r["codice"]}, {"$set": {"id": f"smart-venue:{r['codice']}", "codice": r["codice"], "descrizione": r["descrizione"], "smart_venue": r["smart_venue"], "barcode": r["barcodes"][0] if r["barcodes"] else "", "barcodes": r["barcodes"], "origine": "BULK_TESTO", "batch_id": batch_id, "updated_at": now}}, upsert=True) for r in resolved]
        if (await bulk_upsert(db.smart_venue, operations))["errori"]: raise HTTPException(500, "Salvataggio SmartVenue incompleto: riprova")
        await db.smart_venue.delete_many({"origine": "BULK_TESTO", "codice": {"$nin": codes}}); rows = await comparison(resolved)
        response = {"ok": True, "batch_id": batch_id, "backup_id": backup["id"], "created_at": now, "righe": rows, "prodotti": len(rows), "totale_smart_venue": sum(r["smart_venue"] for r in rows), "differenze": sum(r["stato"] != "OK" for r in rows), "errori": []}; await record_import_history("Testo SmartVenue", response, backup["id"]); return response

    @router.post("/smart-venue/import-excel")
    async def import_smart_venue_excel(file: UploadFile = File(...)):
        try: import openpyxl
        except Exception: raise HTTPException(500, "openpyxl non installato")
        try: workbook = openpyxl.load_workbook(io.BytesIO(await read_capped(file)), data_only=True, keep_vba=False)
        except Exception as exc: raise HTTPException(422, f"File non leggibile: {exc}")
        if "(SMART VENUE)" not in workbook.sheetnames: raise HTTPException(422, "Foglio (SMART VENUE) non trovato")
        backup = await create_backup_snapshot(f"Prima import SMART VENUE {file.filename or 'Excel'}", "pre-import-smart-venue"); result = await import_smart_venue(workbook["(SMART VENUE)"]); response = {"ok": True, "file": file.filename, "backup_id": backup["id"], "tipo_import": "smart_venue", "contabilita_inclusa": False, "fogli_trovati": ["(SMART VENUE)"], "fogli_mancanti": [], "dettaglio": {"(SMART VENUE)": result}, "totali": {"smart_venue_righe": result["inseriti"]}}; await record_import_history(file.filename or "Excel SMART VENUE", response, backup["id"]); return response

    @router.get("/smart-venue")
    async def list_smart_venue():
        db = get_database(); hidden, smart, products, vending, pending, adjustments = await asyncio.gather(db.smart_venue_hidden.find({}, {"_id": 0, "product_id": 1}).to_list(5000), db.smart_venue.find({}, {"_id": 0}).sort("codice", 1).to_list(5000), db.prodotti.find({}, {"_id": 0}).sort("codice", 1).to_list(5000), vending_stock_by_code(), pending_by_code(), db.rettifiche_inventario.find({}, {"_id": 0}).sort("created_at", -1).to_list(5000)); hidden_ids = {str(r.get("product_id")) for r in hidden}; adjustment_by_code = {}; [adjustment_by_code.setdefault(str(r.get("codice") or ""), r) for r in adjustments]; products = [{**p, "giacenza_vending": (vending or {}).get(str(p.get("codice") or ""), 0)} for p in products]; by_code = {str(p.get("codice") or ""): p for p in products}; rows = []
        for s in resolve_rows(smart, products):
            rid = str(s.get("id") or f"smart-venue:{s.get('codice', '')}");
            if rid in hidden_ids: continue
            p = by_code.get(str(s.get("codice") or ""), {}); code = str(s.get("codice") or ""); shop = int(p.get("giacenza_negozio", 0) or 0); vend = int(p.get("giacenza_vending", 0) or 0); value = int(s.get("smart_venue", s.get("rimanenze_smart", 0)) or 0); diff = value - shop - vend; inferred = value - vend; pending_quantity = pending.get(code, 0); status = reconciliation_status(has_product=bool(p), difference=diff, pending=pending_quantity, has_adjustment=code in adjustment_by_code, smart_venue=value, vending=vend)
            rows.append({"id": rid, "codice": code, "descrizione": str(s.get("descrizione") or p.get("descrizione") or ""), "acquistati": int(p.get("acquistati", 0) or 0), "rimanenze": shop + vend, "giacenza_negozio": shop, "giacenza_vending": vend, "gestionale_totale": shop + vend, "negozio_calcolato": inferred, "smart_venue": value, "barcode": str(s.get("barcode", s.get("codice_smart", "")) or ""), "barcodes": s.get("barcodes") or [], "carichi_pendenti": pending_quantity, "differenza": diff, "differenza_residua": diff + pending_quantity, "stato": status, "rettifica_id": (adjustment_by_code.get(code) or {}).get("id"), "rettifica_motivazione": (adjustment_by_code.get(code) or {}).get("motivazione")})
        return rows

    @router.post("/smart-venue")
    async def create_smart_venue_product(body: SmartVenueProductIn):
        db = get_database(); code = product_code_text(body.codice); description = str(body.descrizione or "").strip(); category = str(body.categoria or "").strip().upper()
        if not code or not description: raise HTTPException(422, "Codice e descrizione sono obbligatori")
        if category not in product_categories: raise HTTPException(422, "Categoria prodotto non valida")
        for field in ("acquistati", "giacenza_negozio", "smart_venue"):
            if getattr(body, field) < 0: raise HTTPException(422, f"{field.replace('_', ' ').capitalize()} non può essere negativo")
        if await db.smart_venue.find_one({"codice": code}): raise HTTPException(409, f"Codice già presente in SMART VENUE: {code}")
        product = await db.prodotti.find_one({"codice": code}, {"_id": 0})
        if product is None:
            product = product_model(codice=code, descrizione=description, categoria=category, prezzo=body.prezzo, acquistati=body.acquistati, giacenza_negozio=body.giacenza_negozio, giacenza_vending=0).model_dump(); await db.prodotti.insert_one(product)
        rid = f"smart-venue:{code}"; smart = {"id": rid, "codice": code, "descrizione": description, "barcode": str(body.barcode or "").strip(), "smart_venue": body.smart_venue, "origine": "MANUALE", "updated_at": datetime.now(timezone.utc).isoformat()}; await db.smart_venue.insert_one(dict(smart)); await db.smart_venue_hidden.delete_many({"product_id": rid}); stock = int(product.get("giacenza_negozio", 0) or 0) + int(product.get("giacenza_vending", 0) or 0); return {**smart, "descrizione": str(product.get("descrizione") or description), "acquistati": int(product.get("acquistati", 0) or 0), "rimanenze": stock, "differenza": body.smart_venue - stock}

    @router.put("/smart-venue/{row_id}/barcode")
    async def update_smart_venue_barcode(row_id: str, body: SmartVenueBarcodeIn):
        db = get_database(); barcode = str(body.barcode or "").strip()
        if len(barcode) > 100: raise HTTPException(422, "Barcode troppo lungo")
        result = await db.smart_venue.update_one({"id": row_id}, {"$set": {"barcode": barcode, "updated_at": datetime.now(timezone.utc).isoformat()}})
        if result.matched_count == 0: raise HTTPException(404, "Riga SMARTV VENUE non trovata")
        return {"ok": True, "id": row_id, "barcode": barcode}

    def parse_quantity(value):
        if isinstance(value, bool): raise HTTPException(422, "Inserimento Smart Venue non valido")
        try: quantity = int(value)
        except (TypeError, ValueError): raise HTTPException(422, "Inserimento Smart Venue non valido")
        if isinstance(value, float) and not value.is_integer(): raise HTTPException(422, "Inserimento Smart Venue deve essere un numero intero")
        if quantity < 0: raise HTTPException(422, "Inserimento Smart Venue non può essere negativo")
        return quantity

    @router.post("/smart-venue/{row_id}/conferma")
    async def confirm_smart_venue_difference(row_id: str, body: Optional[Dict[str, Any]] = None, request: Request = None):
        """Record an explicitly confirmed external outgoing movement."""
        db = get_database(); row = await db.smart_venue.find_one({"id": row_id}, {"_id": 0});
        if not row: raise HTTPException(404, "Riga SMARTV VENUE non trovata")
        product = await db.prodotti.find_one({"codice": row.get("codice")}, {"_id": 0}) or {}; vending = await vending_stock_by_code() or {}; stock = int(product.get("giacenza_negozio", 0) or 0) + int(vending.get(str(product.get("codice") or ""), 0) or 0); current = int(row.get("smart_venue", row.get("rimanenze_smart", 0)) or 0); difference = current - stock; requested = (body or {}).get("inserimento_smart_venue"); quantity = difference if requested in (None, "") else parse_quantity(requested)
        if difference <= 0: raise HTTPException(422, "L'uscita è disponibile solo per una differenza positiva")
        if quantity <= 0: raise HTTPException(422, "La quantità di uscita deve essere positiva")
        if quantity > difference: raise HTTPException(422, "L'uscita supera la differenza positiva")
        if quantity > current: raise HTTPException(422, "Inserimento Smart Venue superiore alla quantità disponibile")
        key = str((body or {}).get("idempotency_key") or uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat(); updated = current - quantity
        async def confirm(session):
            kwargs = session_kwargs(session)
            existing = await db.smart_venue_movimenti.find_one({"idempotency_key": key}, {"_id": 0}, **kwargs)
            if existing: return existing, True
            result = await db.smart_venue.update_one({"id": row_id, "smart_venue": current}, {"$set": {"smart_venue": updated, "ultimo_inserimento_smart_venue": quantity, "updated_at": now}}, **kwargs)
            if result.matched_count != 1: raise HTTPException(409, "Saldo Smart Venue cambiato: ricarica e riprova")
            movement = {"id": str(uuid.uuid4()), "idempotency_key": key, "codice": row["codice"], "tipo": "USCITA", "quantita": quantity, "stato": "CONFERMATO", "saldo_precedente": current, "saldo_successivo": updated, "origine": "SMART_VENUE_RICONCILIAZIONE", "motivazione": str((body or {}).get("motivazione") or "Uscita registrata in Smart Venue"), "operatore": request_operator(request), "created_at": now, "confirmed_at": now}
            await db.smart_venue_movimenti.insert_one(movement, **kwargs)
            return movement, False
        movement, already = await run_transactionally(confirm)
        if not already: await record_audit_event("CONFERMATO", "SMART_VENUE_USCITA", movement["id"], prima={"saldo": current}, dopo={"saldo": updated, "quantita": quantity}, operatore=movement["operatore"])
        return {"ok": True, "gia_confermato": already, "id": row_id, "rimanenze": stock, "smart_venue": movement.get("saldo_successivo", updated), "inserimento_smart_venue": quantity, "saldo_precedente": current, "saldo_successivo": updated, "differenza": updated - stock}

    @router.post("/smart-venue/{row_id}/conferma-entrata")
    async def confirm_smart_venue_entry(row_id: str, body: SmartVenueEntryIn, request: Request = None):
        db = get_database()
        if body.quantita <= 0 or not body.motivazione.strip() or not body.idempotency_key.strip():
            raise HTTPException(422, "Quantità, motivazione e idempotency_key sono obbligatori")
        row = await db.smart_venue.find_one({"id": row_id}, {"_id": 0})
        if not row: raise HTTPException(404, "Riga SMARTV VENUE non trovata")
        product = await db.prodotti.find_one({"codice": row.get("codice")}, {"_id": 0}) or {}
        vending = await vending_stock_by_code() or {}; shop = int(product.get("giacenza_negozio", 0) or 0); vend = int(vending.get(str(row.get("codice") or ""), 0) or 0)
        current = int(row.get("smart_venue", 0) or 0); difference = current - shop - vend
        if difference >= 0: raise HTTPException(422, "L'entrata è disponibile solo per una differenza negativa")
        if body.quantita > abs(difference): raise HTTPException(422, "L'entrata supera la differenza negativa")
        if body.saldo_atteso != current or body.differenza_attesa != difference: raise HTTPException(409, "Saldo o differenza cambiati: ricarica e riprova")
        if body.rettifica_id:
            adjustment = await db.rettifiche_inventario.find_one({"id": body.rettifica_id, "codice": row.get("codice")}, {"_id": 0})
            if not adjustment: raise HTTPException(422, "Rettifica non valida per questa riga Smart Venue")
        now = datetime.now(timezone.utc).isoformat(); updated = current + body.quantita
        async def confirm(session):
            kwargs = session_kwargs(session); existing = await db.smart_venue_movimenti.find_one({"idempotency_key": body.idempotency_key}, {"_id": 0}, **kwargs)
            if existing: return existing, True
            result = await db.smart_venue.update_one({"id": row_id, "smart_venue": current}, {"$set": {"smart_venue": updated, "updated_at": now}}, **kwargs)
            if result.matched_count != 1: raise HTTPException(409, "Saldo Smart Venue cambiato: ricarica e riprova")
            movement = {"id": str(uuid.uuid4()), "row_id": row_id, "idempotency_key": body.idempotency_key, "codice": row["codice"], "tipo": "ENTRATA_RETTIFICA", "quantita": body.quantita, "stato": "CONFERMATO", "saldo_precedente": current, "saldo_successivo": updated, "rettifica_id": body.rettifica_id, "motivazione": body.motivazione.strip(), "operatore": request_operator(request), "created_at": now, "confirmed_at": now}
            await db.smart_venue_movimenti.insert_one(movement, **kwargs); return movement, False
        movement, already = await run_transactionally(confirm)
        if already and (movement.get("row_id") not in (None, row_id) or movement.get("tipo") != "ENTRATA_RETTIFICA"):
            raise HTTPException(409, "idempotency_key già utilizzata per un'altra operazione")
        if not already: await record_audit_event("CONFERMATO", "SMART_VENUE_ENTRATA", movement["id"], prima={"saldo": current, "differenza": difference}, dopo={"saldo": updated, "differenza": updated - shop - vend, "quantita": body.quantita, "motivazione": body.motivazione.strip()}, operatore=movement["operatore"], idempotency_key=body.idempotency_key)
        return {"ok": True, "gia_confermato": already, "id": row_id, "smart_venue": movement["saldo_successivo"], "quantita": movement["quantita"], "differenza": movement["saldo_successivo"] - shop - vend}

    @router.get("/smart-venue/diagnostica/riconciliazioni")
    async def smart_venue_reconciliation_diagnostic():
        rows = await list_smart_venue()
        stati = sorted({row.get("stato") for row in rows})
        return {"totale": len(rows), "per_stato": {stato: sum(row.get("stato") == stato for row in rows) for stato in stati}, "righe": rows}

    @router.delete("/smart-venue/{row_id}")
    async def delete_smart_venue_row(row_id: str):
        db = get_database(); row = await db.smart_venue.find_one({"id": row_id}, {"_id": 0, "id": 1, "codice": 1})
        if not row: raise HTTPException(404, "Riga SMARTV VENUE non trovata")
        if not await db.smart_venue_hidden.find_one({"product_id": row_id}): await db.smart_venue_hidden.insert_one({"id": str(uuid.uuid4()), "product_id": row_id, "codice": row.get("codice"), "hidden_at": datetime.now(timezone.utc).isoformat()})
        return {"ok": True, "id": row_id}

    return {"router": router, "import_smart_venue": import_smart_venue, "parse_smart_venue_bulk_text": parse_bulk_text, "resolve_smart_venue_product_rows": resolve_rows, "smart_venue_bulk_comparison": comparison, "preview_smart_venue_bulk": preview_smart_venue_bulk, "import_smart_venue_bulk": import_smart_venue_bulk, "import_smart_venue_excel": import_smart_venue_excel, "list_smart_venue": list_smart_venue, "create_smart_venue_product": create_smart_venue_product, "update_smart_venue_barcode": update_smart_venue_barcode, "confirm_smart_venue_difference": confirm_smart_venue_difference, "delete_smart_venue_row": delete_smart_venue_row}

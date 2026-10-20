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
    codice: str
    quantita: int
    motivazione: str
    idempotency_key: str
    saldo_atteso: int
    differenza_attesa: int
    rettifica_id: Optional[str] = None

class SmartVenueExitIn(BaseModel):
    codice: str
    quantita: int
    motivazione: str
    idempotency_key: str
    saldo_atteso: int
    differenza_attesa: int


def _normalized_reason(value: Any) -> str:
    return " ".join(str(value or "").split())

def create_smart_venue_router(*, get_database, read_capped, create_backup_snapshot,
                              record_import_history, bulk_upsert, product_model,
                              product_categories, product_code_text,
                              product_identity_tokens, adm_numeric_code,
                              adm_local_code, nonnegative_money, money_product,
                              vending_stock_by_code, run_transactionally, session_kwargs,
                              request_operator, record_audit_event):
    router = APIRouter()

    @router.post("/smart-venue/{row_id}/conferma-uscita")
    async def confirm_smart_venue_exit(row_id: str, body: SmartVenueExitIn, request: Request = None):
        db = get_database()
        reason = _normalized_reason(body.motivazione)
        key = body.idempotency_key.strip()
        if body.quantita <= 0 or not reason or not key:
            raise HTTPException(422, "Quantità, motivazione e idempotency_key sono obbligatori")
        # This lookup deliberately precedes all current-state checks. A retry
        # must return its original outcome after its first request changed the
        # balance (and hence the current difference).
        existing = await db.smart_venue_movimenti.find_one({"idempotency_key": key}, {"_id": 0})
        if existing:
            expected = {
                "tipo": "USCITA", "row_id": row_id,
                "codice": body.codice, "quantita": body.quantita,
                "saldo_precedente": body.saldo_atteso,
                "differenza_precedente": body.differenza_attesa,
                "motivazione": reason,
            }
            if any(existing.get(field) != value for field, value in expected.items()):
                raise HTTPException(409, "idempotency_key già utilizzata con un payload diverso")
            return {"ok": True, "gia_confermato": True, "id": row_id,
                    "smart_venue": existing["saldo_successivo"], "quantita": existing["quantita"],
                    "saldo_precedente": existing["saldo_precedente"],
                    "saldo_successivo": existing["saldo_successivo"],
                    "differenza": existing["differenza_successiva"]}
        row = await db.smart_venue.find_one({"id": row_id}, {"_id": 0})
        if not row: raise HTTPException(404, "Riga SMARTV VENUE non trovata")
        if body.codice != row.get("codice"):
            raise HTTPException(422, "Codice non valido per questa riga Smart Venue")
        if await db.smart_venue_movimenti.find_one({"tipo": "CARICO_IN_ENTRATA", "stato": "PENDENTE", "codice": row.get("codice")}, {"_id": 0, "id": 1}):
            raise HTTPException(409, "Esiste un carico pendente: confermalo prima dallo Storico ordini")
        product = await db.prodotti.find_one({"codice": row.get("codice")}, {"_id": 0}) or {}
        vending = await vending_stock_by_code() or {}
        shop = int(product.get("giacenza_negozio", 0) or 0)
        vend = int(vending.get(str(row.get("codice") or ""), 0) or 0)
        stock = shop + vend
        current = int(row.get("smart_venue", row.get("rimanenze_smart", 0)) or 0)
        difference = current - stock
        if body.saldo_atteso != current:
            raise HTTPException(409, "Saldo Smart Venue cambiato: ricarica e riprova")
        if body.differenza_attesa != difference:
            raise HTTPException(409, "Differenza Smart Venue cambiata: ricarica e riprova")
        if difference <= 0:
            raise HTTPException(422, "Differenza non positiva")
        if body.quantita > difference:
            raise HTTPException(422, "Quantità supera la differenza")
        if body.quantita > current:
            raise HTTPException(422, "Quantità supera il saldo")
        updated = current - body.quantita
        now = datetime.now(timezone.utc).isoformat()

        async def confirm(session):
            kwargs = session_kwargs(session)
            raced = await db.smart_venue_movimenti.find_one(
                {"idempotency_key": key}, {"_id": 0}, **kwargs
            )
            if raced:
                return raced, True
            result = await db.smart_venue.update_one(
                {"id": row_id, "smart_venue": current},
                {"$set": {"smart_venue": updated, "updated_at": now}},
                **kwargs,
            )
            if result.matched_count != 1:
                raise HTTPException(409, "Saldo Smart Venue cambiato: ricarica e riprova")
            movement = {
                "id": str(uuid.uuid4()), "row_id": row_id,
                "idempotency_key": key, "codice": body.codice,
                "tipo": "USCITA", "quantita": body.quantita,
                "stato": "CONFERMATO", "saldo_precedente": current,
                "saldo_successivo": updated,
                "differenza_precedente": difference,
                "differenza_successiva": updated - stock,
                "motivazione": reason, "operatore": request_operator(request),
                "created_at": now, "confirmed_at": now,
            }
            try:
                await db.smart_venue_movimenti.insert_one(movement, **kwargs)
            except Exception:
                if session is None:
                    await db.smart_venue.update_one(
                        {"id": row_id, "smart_venue": updated},
                        {"$set": {"smart_venue": current}},
                    )
                raise
            return movement, False

        movement, already = await run_transactionally(confirm)
        if already:
            expected = {
                "tipo": "USCITA", "row_id": row_id,
                "codice": body.codice, "quantita": body.quantita,
                "saldo_precedente": body.saldo_atteso,
                "differenza_precedente": body.differenza_attesa,
                "motivazione": reason,
            }
            if any(movement.get(field) != value for field, value in expected.items()):
                raise HTTPException(409, "idempotency_key già utilizzata con un payload diverso")
        else:
            await record_audit_event(
                "CONFERMATO", "SMART_VENUE_USCITA", movement["id"],
                prima={"saldo": current, "differenza": difference},
                dopo={"saldo": updated, "differenza": movement["differenza_successiva"],
                       "quantita": body.quantita, "motivazione": reason},
                operatore=movement["operatore"], idempotency_key=key,
            )
        return {
            "ok": True, "gia_confermato": already, "id": row_id,
            "smart_venue": movement["saldo_successivo"],
            "quantita": movement["quantita"],
            "saldo_precedente": movement["saldo_precedente"],
            "saldo_successivo": movement["saldo_successivo"],
            "differenza": movement["differenza_successiva"],
        }

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

    def applicable_adjustments_by_code(adjustments, movements, smart_rows):
        """Return the newest unreconciled inventory adjustment after each SV baseline.

        A Smart Venue import (or confirmed reconciliation) establishes a new
        baseline.  Older physical-inventory adjustments cannot explain a later
        Smart Venue snapshot.  An adjustment linked to a confirmed entry is
        consumed and therefore cannot keep the row in a pending state.
        """
        baseline_by_code = {
            str(row.get("codice") or ""): str(row.get("updated_at") or "")
            for row in smart_rows
        }
        reconciled = {
            str(movement.get("rettifica_id"))
            for movement in movements
            if movement.get("stato") == "CONFERMATO" and movement.get("rettifica_id")
        }
        result = {}
        for adjustment in sorted(
            adjustments,
            key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")),
            reverse=True,
        ):
            code = str(adjustment.get("codice") or "")
            adjustment_id = str(adjustment.get("id") or "")
            if (not code or not adjustment_id or adjustment_id in reconciled
                    or code in result
                    or str(adjustment.get("created_at") or "") <= baseline_by_code.get(code, "")):
                continue
            result[code] = adjustment
        return result

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
        db = get_database(); hidden, smart, products, vending, pending, adjustments, movements = await asyncio.gather(db.smart_venue_hidden.find({}, {"_id": 0, "product_id": 1}).to_list(5000), db.smart_venue.find({}, {"_id": 0}).sort("codice", 1).to_list(5000), db.prodotti.find({}, {"_id": 0}).sort("codice", 1).to_list(5000), vending_stock_by_code(), pending_by_code(), db.rettifiche_inventario.find({}, {"_id": 0}).to_list(5000), db.smart_venue_movimenti.find({"stato": "CONFERMATO", "rettifica_id": {"$exists": True}}, {"_id": 0, "rettifica_id": 1, "stato": 1}).to_list(5000)); hidden_ids = {str(r.get("product_id")) for r in hidden}; adjustment_by_code = applicable_adjustments_by_code(adjustments, movements, smart); products = [{**p, "giacenza_vending": (vending or {}).get(str(p.get("codice") or ""), 0)} for p in products]; by_code = {str(p.get("codice") or ""): p for p in products}; rows = []
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
        """Reject the retired non-idempotent contract.

        Kept as an explicit response so stale clients cannot silently perform a
        balance change without the expected balance, reason and stable key.
        """
        raise HTTPException(
            410,
            "Operazione sostituita: usa /conferma-uscita con chiave idempotente",
        )

    @router.post("/smart-venue/{row_id}/conferma-entrata")
    async def confirm_smart_venue_entry(row_id: str, body: SmartVenueEntryIn, request: Request = None):
        db = get_database()
        reason = _normalized_reason(body.motivazione)
        key = body.idempotency_key.strip()
        if body.quantita <= 0 or not reason or not key:
            raise HTTPException(422, "Quantità, motivazione e idempotency_key sono obbligatori")
        # This lookup deliberately precedes all current-state checks.  A retry
        # must return its original outcome after its first request changed the
        # balance (and hence the current difference).
        existing = await db.smart_venue_movimenti.find_one({"idempotency_key": key}, {"_id": 0})
        if existing:
            expected = {
                "tipo": "ENTRATA_RETTIFICA", "row_id": row_id,
                "codice": body.codice,
                "quantita": body.quantita, "saldo_precedente": body.saldo_atteso,
                "differenza_precedente": body.differenza_attesa,
                "rettifica_id": body.rettifica_id,
                "motivazione": reason,
            }
            if any(existing.get(field) != value for field, value in expected.items()):
                raise HTTPException(409, "idempotency_key già utilizzata con un payload diverso")
            return {"ok": True, "gia_confermato": True, "id": row_id,
                    "smart_venue": existing["saldo_successivo"], "quantita": existing["quantita"],
                    "saldo_precedente": existing["saldo_precedente"],
                    "saldo_successivo": existing["saldo_successivo"],
                    "differenza": existing["differenza_successiva"]}
        row = await db.smart_venue.find_one({"id": row_id}, {"_id": 0})
        if not row: raise HTTPException(404, "Riga SMARTV VENUE non trovata")
        if body.codice != row.get("codice"):
            raise HTTPException(422, "Codice non valido per questa riga Smart Venue")
        if await db.smart_venue_movimenti.find_one({"tipo": "CARICO_IN_ENTRATA", "stato": "PENDENTE", "codice": row.get("codice")}, {"_id": 0, "id": 1}):
            raise HTTPException(409, "Esiste un carico pendente: confermalo prima dallo Storico ordini")
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
            kwargs = session_kwargs(session)
            raced = await db.smart_venue_movimenti.find_one(
                {"idempotency_key": key}, {"_id": 0}, **kwargs
            )
            if raced:
                return raced, True
            result = await db.smart_venue.update_one({"id": row_id, "smart_venue": current}, {"$set": {"smart_venue": updated, "updated_at": now}}, **kwargs)
            if result.matched_count != 1: raise HTTPException(409, "Saldo Smart Venue cambiato: ricarica e riprova")
            movement = {"id": str(uuid.uuid4()), "row_id": row_id, "idempotency_key": key, "codice": row["codice"], "tipo": "ENTRATA_RETTIFICA", "quantita": body.quantita, "stato": "CONFERMATO", "saldo_precedente": current, "saldo_successivo": updated, "differenza_precedente": difference, "differenza_successiva": updated - shop - vend, "rettifica_id": body.rettifica_id, "motivazione": reason, "operatore": request_operator(request), "created_at": now, "confirmed_at": now}
            try:
                await db.smart_venue_movimenti.insert_one(movement, **kwargs)
            except Exception:
                if session is None:
                    await db.smart_venue.update_one(
                        {"id": row_id, "smart_venue": updated},
                        {"$set": {"smart_venue": current}},
                    )
                raise
            return movement, False
        movement, already = await run_transactionally(confirm)
        if already:
            expected = {
                "tipo": "ENTRATA_RETTIFICA", "row_id": row_id,
                "codice": body.codice, "quantita": body.quantita,
                "saldo_precedente": body.saldo_atteso,
                "differenza_precedente": body.differenza_attesa,
                "rettifica_id": body.rettifica_id, "motivazione": reason,
            }
            if any(movement.get(field) != value for field, value in expected.items()):
                raise HTTPException(409, "idempotency_key già utilizzata con un payload diverso")
        else:
            await record_audit_event("CONFERMATO", "SMART_VENUE_ENTRATA", movement["id"], prima={"saldo": current, "differenza": difference}, dopo={"saldo": updated, "differenza": updated - shop - vend, "quantita": body.quantita, "motivazione": reason}, operatore=movement["operatore"], idempotency_key=key)
        return {"ok": True, "gia_confermato": already, "id": row_id, "smart_venue": movement["saldo_successivo"], "quantita": movement["quantita"], "differenza": movement["saldo_successivo"] - shop - vend}

    @router.get("/smart-venue/diagnostica/riconciliazioni")
    async def smart_venue_reconciliation_diagnostic():
        rows = await list_smart_venue()
        stati = sorted({row.get("stato") for row in rows})
        per_stato = {
            stato: sum(row.get("stato") == stato for row in rows)
            for stato in stati
        }
        return {
            "prodotti_totali": len(rows),
            "allineati": per_stato.get("ALLINEATO", 0),
            "codici_mancanti": per_stato.get("CODICE MANCANTE", 0),
            "uscite_da_confermare": per_stato.get("USCITA DA CONFERMARE", 0),
            "somma_uscite": sum(max(0, int(row.get("differenza") or 0)) for row in rows),
            "entrate_da_verificare": per_stato.get("ENTRATA DA VERIFICARE", 0),
            "somma_entrate": sum(
                abs(min(0, int(row.get("differenza") or 0)))
                for row in rows
                if row.get("stato") in {"ENTRATA DA VERIFICARE", "RETTIFICA DA VERIFICARE"}
            ),
            "carichi_da_inserire": per_stato.get("CARICO DA INSERIRE", 0),
            "quantita_carichi_pendenti": sum(int(row.get("carichi_pendenti") or 0) for row in rows),
            "rettifiche_da_verificare": per_stato.get("RETTIFICA DA VERIFICARE", 0),
            "anomalie_vending": per_stato.get("ANOMALIA VENDING", 0),
            "differenza_totale": sum(int(row.get("differenza") or 0) for row in rows),
            "per_stato": per_stato,
            "righe": rows,
        }

    @router.delete("/smart-venue/{row_id}")
    async def delete_smart_venue_row(row_id: str):
        db = get_database(); row = await db.smart_venue.find_one({"id": row_id}, {"_id": 0, "id": 1, "codice": 1})
        if not row: raise HTTPException(404, "Riga SMARTV VENUE non trovata")
        if not await db.smart_venue_hidden.find_one({"product_id": row_id}): await db.smart_venue_hidden.insert_one({"id": str(uuid.uuid4()), "product_id": row_id, "codice": row.get("codice"), "hidden_at": datetime.now(timezone.utc).isoformat()})
        return {"ok": True, "id": row_id}

    return {"router": router, "import_smart_venue": import_smart_venue, "parse_smart_venue_bulk_text": parse_bulk_text, "resolve_smart_venue_product_rows": resolve_rows, "smart_venue_bulk_comparison": comparison, "preview_smart_venue_bulk": preview_smart_venue_bulk, "import_smart_venue_bulk": import_smart_venue_bulk, "import_smart_venue_excel": import_smart_venue_excel, "list_smart_venue": list_smart_venue, "create_smart_venue_product": create_smart_venue_product, "update_smart_venue_barcode": update_smart_venue_barcode, "confirm_smart_venue_exit": confirm_smart_venue_exit, "confirm_smart_venue_difference": confirm_smart_venue_difference, "confirm_smart_venue_entry": confirm_smart_venue_entry, "smart_venue_reconciliation_diagnostic": smart_venue_reconciliation_diagnostic, "delete_smart_venue_row": delete_smart_venue_row}

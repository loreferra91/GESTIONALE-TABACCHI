"""Vending column catalogue, availability proposals, and manual corrections."""
import asyncio
from datetime import datetime, timezone
import io
import re
import uuid
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
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

    @router.post("/vending/{column_id}/ricarica")
    async def ricarica_vending(column_id: str, body: Dict[str, Any]):
        raw_quantity = body.get("quantita", 0)
        try:
            quantity = int(raw_quantity)
        except (TypeError, ValueError):
            raise HTTPException(422, "quantita non valida")
        if isinstance(raw_quantity, bool) or (
            isinstance(raw_quantity, float) and not raw_quantity.is_integer()
        ):
            raise HTTPException(422, "La quantità deve essere un numero intero")
        if isinstance(raw_quantity, str) and not re.fullmatch(
            r"[+-]?\d+", raw_quantity.strip()
        ):
            raise HTTPException(422, "La quantità deve essere un numero intero")
        if quantity <= 0:
            raise HTTPException(422, "quantita deve essere maggiore di zero")

        database = get_database()
        column, params = await asyncio.gather(
            database.vending.find_one({"id": column_id}), get_params()
        )
        if not column:
            raise HTTPException(404, "not found")
        reserve = max(
            0,
            int(param_value(params, "SCORTA_MINIMA_NEGOZIO_VENDING", 2)),
        )
        stock = int(column.get("giacenza", 0) or 0)
        capacity = int(column.get("capacita_max", 0) or 0)
        code = column.get("codice")
        product = (
            await database.prodotti.find_one({"codice": code}) if code else None
        )
        available = max(
            0, int((product or {}).get("giacenza_negozio", 0) or 0)
        )
        needed = max(0, capacity - stock)
        if needed <= 0:
            raise HTTPException(409, "Colonna già alla capacità massima")
        if quantity > needed:
            raise HTTPException(
                422,
                f"La quantità supera la capacità della colonna: puoi caricare al massimo {needed} pezzi",
            )
        loadable = max(0, available - reserve)
        if loadable <= 0:
            raise HTTPException(
                409,
                f"Scorta negozio protetta: devono restare almeno {reserve} pezzi",
            )
        if quantity > loadable:
            raise HTTPException(
                409,
                f"Scorta negozio protetta: richiesti {quantity} pezzi, caricabili {loadable} "
                f"(disponibili {available}, riserva {reserve})",
            )

        new_stock = stock + quantity
        updated_at = datetime.now(timezone.utc).isoformat()
        vending_result = await database.vending.update_one(
            {"id": column_id, "giacenza": column.get("giacenza", 0)},
            {
                "$inc": {"giacenza": quantity},
                "$set": {
                    "giacenza_aggiornata_il": updated_at,
                    "giacenza_sorgente": "RICARICA",
                },
            },
        )
        if vending_result.matched_count == 0:
            raise HTTPException(
                409,
                "La colonna è cambiata nel frattempo: aggiorna la pagina e riprova",
            )
        try:
            product_result = await database.prodotti.update_one(
                {
                    "codice": code,
                    "giacenza_negozio": {"$gte": quantity + reserve},
                },
                {"$inc": {
                    "giacenza_negozio": -quantity,
                    "giacenza_vending": quantity,
                }},
            )
            if product_result.matched_count == 0:
                raise RuntimeError("la disponibilità negozio è cambiata")
        except Exception as exc:
            await database.vending.update_one(
                {"id": column_id, "giacenza": new_stock},
                {
                    "$inc": {"giacenza": -quantity},
                    "$set": {
                        "giacenza_aggiornata_il": column.get(
                            "giacenza_aggiornata_il"
                        ),
                        "giacenza_sorgente": column.get("giacenza_sorgente"),
                    },
                },
            )
            raise HTTPException(409, f"Ricarica annullata: {exc}")
        return {
            "ok": True,
            "colonna": column["colonna"],
            "nuova_giacenza": new_stock,
            "quantita_caricata": quantity,
            "giacenza_magazzino_residua": available - quantity,
            "scorta_minima_negozio": reserve,
        }

    @router.post("/vending/ricarica-completa")
    async def ricarica_vending_completa(body: Dict[str, Any]):
        input_rows = body.get("righe")
        if not isinstance(input_rows, list) or not input_rows:
            raise HTTPException(422, "Nessuna riga da caricare")
        if len(input_rows) > 500:
            raise HTTPException(
                422, "Sono consentite al massimo 500 righe per caricamento"
            )

        quantities_by_id: Dict[str, int] = {}
        for row in input_rows:
            if not isinstance(row, dict):
                raise HTTPException(422, "Formato riga non valido")
            column_id = str(row.get("id") or "").strip()
            if not column_id:
                raise HTTPException(422, "ID colonna mancante")
            if column_id in quantities_by_id:
                raise HTTPException(
                    422, f"Colonna duplicata nel caricamento: {column_id}"
                )
            raw_quantity = row.get("quantita", 0)
            try:
                quantity = int(raw_quantity)
            except (TypeError, ValueError):
                raise HTTPException(
                    422, f"Quantità non valida per la colonna {column_id}"
                )
            if isinstance(raw_quantity, bool) or (
                isinstance(raw_quantity, float) and not raw_quantity.is_integer()
            ):
                raise HTTPException(
                    422,
                    f"La quantità della colonna {column_id} deve essere un numero intero",
                )
            if isinstance(raw_quantity, str) and not re.fullmatch(
                r"[+-]?\d+", raw_quantity.strip()
            ):
                raise HTTPException(
                    422,
                    f"La quantità della colonna {column_id} deve essere un numero intero",
                )
            if quantity <= 0:
                raise HTTPException(
                    422,
                    f"La quantità della colonna {column_id} deve essere maggiore di zero",
                )
            quantities_by_id[column_id] = quantity

        database = get_database()
        ids = list(quantities_by_id)
        columns, params = await asyncio.gather(
            database.vending.find({"id": {"$in": ids}}).to_list(500),
            get_params(),
        )
        reserve = max(
            0,
            int(param_value(params, "SCORTA_MINIMA_NEGOZIO_VENDING", 2)),
        )
        columns_by_id = {str(document.get("id")): document for document in columns}
        missing = [column_id for column_id in ids if column_id not in columns_by_id]
        if missing:
            raise HTTPException(
                404, f"Colonne vending non trovate: {', '.join(missing)}"
            )

        codes = sorted({document.get("codice") for document in columns if document.get("codice")})
        products = await database.prodotti.find(
            {"codice": {"$in": codes}}
        ).to_list(5000)
        products_by_code = {
            str(document.get("codice")): document for document in products
        }
        prepared = []
        requested_by_code: Dict[str, int] = {}
        for column_id in ids:
            column = columns_by_id[column_id]
            quantity = quantities_by_id[column_id]
            stock = int(column.get("giacenza", 0) or 0)
            capacity = int(column.get("capacita_max", 0) or 0)
            needed = max(0, capacity - stock)
            code = str(column.get("codice") or "")
            column_name = str(column.get("colonna") or column_id)
            if needed <= 0:
                raise HTTPException(
                    409, f"Colonna {column_name} già alla capacità massima"
                )
            if quantity > needed:
                raise HTTPException(
                    422,
                    f"Colonna {column_name}: puoi caricare al massimo {needed} pezzi",
                )
            if not code or code not in products_by_code:
                raise HTTPException(
                    409, f"Prodotto non trovato per la colonna {column_name}"
                )
            prepared.append({
                "id": column_id,
                "colonna": column_name,
                "codice": code,
                "giacenza": stock,
                "quantita": quantity,
            })
            requested_by_code[code] = requested_by_code.get(code, 0) + quantity

        for code, requested in requested_by_code.items():
            available = max(
                0,
                int(products_by_code[code].get("giacenza_negozio", 0) or 0),
            )
            loadable = max(0, available - reserve)
            if requested > loadable:
                raise HTTPException(
                    409,
                    f"Scorta negozio protetta per {code}: richiesti {requested} pezzi, "
                    f"caricabili {loadable} (disponibili {available}, riserva {reserve})",
                )

        applied_columns = []
        applied_products = []
        updated_at = datetime.now(timezone.utc).isoformat()
        try:
            for item in prepared:
                original = columns_by_id[item["id"]]
                item["giacenza_aggiornata_il"] = original.get(
                    "giacenza_aggiornata_il"
                )
                item["giacenza_sorgente"] = original.get("giacenza_sorgente")
                result = await database.vending.update_one(
                    {"id": item["id"], "giacenza": item["giacenza"]},
                    {
                        "$inc": {"giacenza": item["quantita"]},
                        "$set": {
                            "giacenza_aggiornata_il": updated_at,
                            "giacenza_sorgente": "RICARICA_COMPLETA",
                        },
                    },
                )
                if result.matched_count == 0:
                    raise RuntimeError(
                        f"La colonna {item['colonna']} è cambiata durante il caricamento"
                    )
                applied_columns.append(item)

            for code, quantity in requested_by_code.items():
                result = await database.prodotti.update_one(
                    {
                        "codice": code,
                        "giacenza_negozio": {"$gte": quantity + reserve},
                    },
                    {"$inc": {
                        "giacenza_negozio": -quantity,
                        "giacenza_vending": quantity,
                    }},
                )
                if result.matched_count == 0:
                    raise RuntimeError(
                        f"La disponibilità di {code} è cambiata durante il caricamento"
                    )
                applied_products.append((code, quantity))
        except Exception as exc:
            for code, quantity in reversed(applied_products):
                await database.prodotti.update_one(
                    {"codice": code},
                    {"$inc": {
                        "giacenza_negozio": quantity,
                        "giacenza_vending": -quantity,
                    }},
                )
            for item in reversed(applied_columns):
                await database.vending.update_one(
                    {"id": item["id"]},
                    {
                        "$inc": {"giacenza": -item["quantita"]},
                        "$set": {
                            "giacenza_aggiornata_il": item.get(
                                "giacenza_aggiornata_il"
                            ),
                            "giacenza_sorgente": item.get("giacenza_sorgente"),
                        },
                    },
                )
            raise HTTPException(409, f"Caricamento annullato: {exc}")

        return {
            "ok": True,
            "colonne_caricate": len(prepared),
            "pezzi_caricati": sum(item["quantita"] for item in prepared),
            "scorta_minima_negozio": reserve,
            "righe": [{
                "id": item["id"],
                "colonna": item["colonna"],
                "quantita_caricata": item["quantita"],
                "nuova_giacenza": item["giacenza"] + item["quantita"],
            } for item in prepared],
        }

    @router.get("/vending/ricarica-pdf")
    async def vending_ricarica_pdf():
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

        rows = await list_vending()
        to_load = sorted(
            (row for row in rows if row.get("proposta", 0) > 0),
            key=lambda row: row["colonna"],
        )
        total_pieces = sum(row.get("proposta", 0) for row in to_load)
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
            "t", parent=styles["Heading1"], fontName="Helvetica-Bold",
            fontSize=18, textColor=colors.HexColor("#0F172A"), spaceAfter=2,
        )
        subtitle_style = ParagraphStyle(
            "s", parent=styles["Normal"], fontName="Helvetica", fontSize=9,
            textColor=colors.HexColor("#64748B"), spaceAfter=12,
        )
        story = [Paragraph("RICARICA VENDING — DA CARICARE", title_style)]
        now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M")
        story.append(Paragraph(
            f"Data: <b>{now}</b> &nbsp;·&nbsp; Colonne da caricare: "
            f"<b>{len(to_load)}</b> &nbsp;·&nbsp; Pezzi totali: <b>{total_pieces}</b>",
            subtitle_style,
        ))
        if not to_load:
            story.append(Paragraph(
                "Nessuna colonna necessita ricarica.", styles["Normal"]
            ))
        else:
            data = [[
                "COLONNA", "CODICE", "ARTICOLO", "VENDING",
                "NEGOZIO (RIS.)", "CAPACITÀ", "DA CARICARE",
            ]]
            for row in to_load:
                data.append([
                    row["colonna"], row.get("codice", ""),
                    (row.get("descrizione") or "")[:45],
                    str(row.get("giacenza", 0)),
                    f"{row.get('giacenza_magazzino', 0)} ({row.get('scorta_minima_negozio', 0)})",
                    str(row.get("capacita_max", 0)), str(row.get("proposta", 0)),
                ])
            data.append(["", "", "", "", "", "TOTALE", str(total_pieces)])
            table = Table(
                data,
                colWidths=[20*mm, 25*mm, 60*mm, 20*mm, 22*mm, 20*mm, 23*mm],
                repeatRows=1,
            )
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("ALIGN", (0, 0), (-1, 0), "LEFT"),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("FONTNAME", (0, 1), (-1, -2), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#F8FAFC")]),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F1F5F9")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(table)
        document.build(story)
        buffer.seek(0)
        filename = f"ricarica_vending_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.pdf"
        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    return {
        "router": router,
        "create_vending_column": create_vending_column,
        "list_vending": list_vending,
        "sync_product_vending_stock": sync_product_vending_stock,
        "update_vending_giacenza": update_vending_giacenza,
        "update_vending": update_vending,
        "ricarica_vending": ricarica_vending,
        "ricarica_vending_completa": ricarica_vending_completa,
        "vending_ricarica_pdf": vending_ricarica_pdf,
    }

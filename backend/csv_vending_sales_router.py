import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict

from fastapi import APIRouter, File, HTTPException, UploadFile


def create_csv_vending_sales_router(
    *,
    get_database: Callable[[], Any],
    read_capped: Callable[[UploadFile], Any],
    prepare_csv_vending_import: Callable[..., Any],
    parse_csv_vending: Callable[..., Dict[str, Any]],
    summarize_vending_csv: Callable[[Dict[str, Any]], Dict[str, Any]],
    sum_money: Callable[[Any], float],
    is_cash_payment: Callable[[Any], bool],
    session_kwargs: Callable[[Any], Dict[str, Any]],
    sync_product_vending_stock: Callable[..., Any],
    product_code_text: Callable[[Any], str],
    adm_local_code: Callable[[Any], str],
    sale_model: Any,
    run_transactionally: Callable[[Any], Any],
    adjust_vending_cash_balance: Callable[..., Any],
    get_store_vending_accounting_snapshot: Callable[..., Any],
    accounting_state_id: str,
    create_backup_snapshot: Callable[..., Any],
) -> Dict[str, Any]:
    router = APIRouter()

    @router.post("/vendite/preview-csv-vending")
    async def preview_csv_vending(
        file: UploadFile = File(...), pagamento: str = "CONTANTI"
    ):
        content = await read_capped(file)
        raw = content.decode("utf-8-sig", errors="replace")
        parsed, cutoff, file_rows = await prepare_csv_vending_import(raw, pagamento)
        accounting_summary = summarize_vending_csv(parse_csv_vending(raw, pagamento))
        payment_counts: Dict[str, int] = {}
        payment_amounts: Dict[str, float] = {}
        total = 0.0
        for row in parsed["righe"]:
            method = row["pagamento"]
            payment_counts[method] = payment_counts.get(method, 0) + 1
            payment_amounts[method] = sum_money(
                [payment_amounts.get(method, 0), row["prezzo"]]
            )
            total = sum_money([total, row["prezzo"]])
        dates = [row["data"][:10] for row in parsed["righe"]]
        return {
            "righe": len(parsed["righe"]),
            "saltati": parsed["saltati"],
            "errori": parsed["errori"],
            "delimitatore": parsed["delimitatore"],
            "pagamenti": payment_counts,
            "importi_pagamenti": payment_amounts,
            "totale": round(total, 2),
            "data_da": min(dates, default=None),
            "data_a": max(dates, default=None),
            "righe_file": file_rows,
            "righe_gia_presenti": file_rows - len(parsed["righe"]),
            "righe_storico_excel": parsed["righe_storico_excel"],
            "righe_csv_gia_importate": parsed["righe_csv_gia_importate"],
            "ultima_vendita_excel": cutoff.isoformat() if cutoff else None,
            "file_sha256": hashlib.sha256(content).hexdigest(),
            **parsed["stock"],
            "importabile": parsed["stock"]["importabile"] and not parsed["errori"],
            "contabilita_csv": accounting_summary,
            "vendite": parsed["righe"],
        }

    async def rollback_csv_import_sales(batch_id: str, session=None) -> Dict[str, Any]:
        db = get_database()
        kwargs = session_kwargs(session)
        sales = await db.vendite.find(
            {"batch_id": batch_id, "sorgente": "CSV_VENDING"},
            {"_id": 0},
            **kwargs,
        ).to_list(None)
        removed = 0
        cash_total = 0.0
        touched_codes = set()
        for sale in sales:
            undo_meta = sale.get("undo_meta") or {}
            codice = str(sale.get("codice") or "")
            if undo_meta.get("product_stock_adjusted"):
                await db.prodotti.update_one(
                    {"codice": codice},
                    {"$inc": {"venduti_vending": -int(sale.get("quantita") or 0)}},
                    **kwargs,
                )
            elif undo_meta.get("product_created"):
                product = await db.prodotti.find_one(
                    {"id": undo_meta.get("product_id")}, {"_id": 0}, **kwargs
                )
                can_delete = product and product.get("created_from_csv_batch") == batch_id and all(
                    int(product.get(field) or 0) == expected
                    for field, expected in {
                        "acquistati": 0,
                        "venduti_negozio": 0,
                        "venduti_vending": 1,
                        "giacenza_negozio": 0,
                        "giacenza_vending": 0,
                    }.items()
                )
                if can_delete:
                    await db.prodotti.delete_one(
                        {"id": product["id"], "created_from_csv_batch": batch_id}, **kwargs
                    )
                else:
                    await db.prodotti.update_one(
                        {"codice": codice}, {"$inc": {"venduti_vending": -1}}, **kwargs
                    )
            if undo_meta.get("vending_stock_decremented"):
                await db.vending.update_one(
                    {"id": undo_meta.get("vending_column_id")},
                    {"$inc": {"giacenza": int(sale.get("quantita") or 0)}},
                    **kwargs,
                )
            if codice:
                touched_codes.add(codice)
            if is_cash_payment(sale.get("pagamento")):
                cash_total = sum_money([cash_total, sale.get("importo") or 0])
            deletion = await db.vendite.delete_one(
                {"id": sale.get("id"), "batch_id": batch_id}, **kwargs
            )
            if deletion.deleted_count != 1:
                raise RuntimeError(
                    "Rollback CSV incompleto: una vendita non è stata eliminata"
                )
            removed += 1
        for codice in touched_codes:
            await sync_product_vending_stock(codice, session)
        return {"rimossi": removed, "contanti": round(cash_total, 2)}

    @router.post("/vendite/import-csv-vending")
    async def import_csv_vending(
        file: UploadFile = File(...), pagamento: str = "CONTANTI"
    ):
        db = get_database()
        content = await read_capped(file)
        raw = content.decode("utf-8-sig", errors="replace")
        parsed, cutoff, file_rows = await prepare_csv_vending_import(raw, pagamento)
        if parsed["errori"]:
            raise HTTPException(
                422,
                f"Il CSV contiene {len(parsed['errori'])} righe non valide: "
                "correggile prima dell'importazione",
            )
        stock_summary = parsed["stock"]
        if not stock_summary["importabile"]:
            details = []
            if stock_summary.get("colonne_non_trovate"):
                details.append(
                    "colonne non configurate: "
                    + ", ".join(stock_summary["colonne_non_trovate"])
                )
            if stock_summary["colonne_senza_riferimento_giacenza"]:
                details.append(
                    "riferimento giacenza mancante per "
                    + ", ".join(stock_summary["colonne_senza_riferimento_giacenza"])
                )
            if stock_summary["errori_giacenza"]:
                details.append("vendite superiori alla giacenza disponibile")
            raise HTTPException(409, "Import CSV bloccato: " + "; ".join(details))

        backup = await create_backup_snapshot(
            "Prima dell'import vendite vending",
            "pre-import-vendite-vending",
            [
                "prodotti", "vending", "vendite", "vendite_bulk_imports",
                "cassa_vending_stato", "vending_accounting_snapshots",
                "vending_accounting_state", "vending_accounting_events",
            ],
        )

        batch_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        file_sha256 = hashlib.sha256(content).hexdigest()
        inserted = 0
        imported_cash = 0.0
        errors = list(parsed["errori"])
        payment_counts: Dict[str, int] = {}
        payment_amounts: Dict[str, float] = {}
        for csv_row in parsed["righe"]:
            undo_meta: Dict[str, Any] = {}
            codice = ""
            try:
                codice = csv_row["codice"]
                nome = csv_row["nome"]
                price = csv_row["prezzo"]
                data_iso = csv_row["data"]
                colonna = csv_row["colonna"]
                payment = csv_row["pagamento"]

                product = None
                if codice:
                    raw_code = product_code_text(codice)
                    candidates = [raw_code]
                    if raw_code.isdigit() or raw_code.startswith("AMMS"):
                        candidates.append(adm_local_code(raw_code))
                    product = await db.prodotti.find_one(
                        {"codice": {"$in": list(dict.fromkeys(candidates))}}
                    )
                    if product:
                        codice = product["codice"]
                if not codice and nome:
                    product = await db.prodotti.find_one(
                        {
                            "descrizione": {
                                "$regex": f"^{re.escape(nome)}$",
                                "$options": "i",
                            }
                        }
                    )
                    if product:
                        codice = product["codice"]
                if not codice:
                    raise ValueError(
                        f"prodotto non riconosciuto: {nome or 'senza nome'}"
                    )

                if colonna:
                    column = await db.vending.find_one({"colonna": colonna})
                    if column:
                        if codice and column.get("codice") and str(column.get("codice")) != codice:
                            raise ValueError(
                                f"colonna {colonna} associata a {column.get('codice')}, "
                                f"CSV indica {codice}"
                            )
                        undo_meta["vending_column_id"] = column["id"]
                        undo_meta["vending_stock_decremented"] = False
                        undo_meta["vending_stock_skip_reason"] = csv_row.get("giacenza_motivo")
                        if csv_row.get("scala_giacenza"):
                            result = await db.vending.update_one(
                                {"id": column["id"], "giacenza": {"$gte": 1}},
                                {"$inc": {"giacenza": -1}},
                            )
                            if result.modified_count != 1:
                                raise ValueError(
                                    f"giacenza insufficiente nella colonna {colonna}"
                                )
                            undo_meta["vending_stock_decremented"] = True
                product = product or await db.prodotti.find_one({"codice": codice})
                if product:
                    await db.prodotti.update_one(
                        {"codice": codice}, {"$inc": {"venduti_vending": 1}}
                    )
                    undo_meta["product_stock_adjusted"] = True
                    if colonna:
                        await sync_product_vending_stock(codice)
                else:
                    raise ValueError(f"prodotto non trovato: {codice}")

                sale = sale_model(
                    data=data_iso,
                    codice=codice,
                    descrizione=nome,
                    quantita=1,
                    importo=price,
                    canale="VENDING",
                    pagamento=payment,
                    sorgente="CSV_VENDING",
                    colonna=colonna or None,
                    batch_id=batch_id,
                    undo_meta=undo_meta,
                )
                sale_doc = sale.model_dump()
                sale_doc.update(
                    {
                        "csv_event_key": csv_row["csv_event_key"],
                        "csv_event_occurrence": csv_row["csv_event_occurrence"],
                        "csv_event_compatibility_key": csv_row[
                            "csv_event_compatibility_key"
                        ],
                        "csv_accounting_occurrence": csv_row[
                            "csv_accounting_occurrence"
                        ],
                        "csv_accounting_key": csv_row["csv_accounting_key"],
                        "csv_file_sha256": file_sha256,
                        "stock_effect_applied": bool(
                            undo_meta.get("vending_stock_decremented")
                        ),
                        "stock_effect_reason": csv_row.get("giacenza_motivo"),
                    }
                )
                await db.vendite.insert_one(sale_doc)
                inserted += 1
                payment_counts[payment] = payment_counts.get(payment, 0) + 1
                payment_amounts[payment] = sum_money(
                    [payment_amounts.get(payment, 0), price]
                )
                if is_cash_payment(payment):
                    imported_cash = sum_money([imported_cash, price])
            except Exception as exc:
                if undo_meta.get("product_stock_adjusted"):
                    await db.prodotti.update_one(
                        {"codice": codice}, {"$inc": {"venduti_vending": -1}}
                    )
                elif undo_meta.get("product_created"):
                    await db.prodotti.delete_one(
                        {
                            "id": undo_meta.get("product_id"),
                            "created_from_csv_batch": batch_id,
                        }
                    )
                if undo_meta.get("vending_stock_decremented"):
                    await db.vending.update_one(
                        {"id": undo_meta.get("vending_column_id")},
                        {"$inc": {"giacenza": 1}},
                    )
                if codice:
                    await sync_product_vending_stock(codice)
                errors.append({"riga": csv_row["riga"], "errore": str(exc)})

        if errors:
            await run_transactionally(
                lambda session: rollback_csv_import_sales(batch_id, session)
            )
            raise HTTPException(
                409,
                detail={
                    "message": "Import CSV rifiutato: nessuna vendita registrata",
                    "errori": errors,
                },
            )

        previous_accounting_state = await db.vending_accounting_state.find_one(
            {"id": accounting_state_id}, {"_id": 0}
        )
        cash_adjusted = False
        try:
            if imported_cash:
                await adjust_vending_cash_balance(imported_cash)
                cash_adjusted = True
            accounting_snapshot, _ = await get_store_vending_accounting_snapshot(
                content,
                file.filename or "vendite-vending.csv",
                source_batch_id=batch_id if inserted else None,
            )
            if inserted:
                await db.vendite_bulk_imports.insert_one(
                    {
                        "id": batch_id,
                        "created_at": created_at,
                        "sorgente": "CSV_VENDING",
                        "canale": "VENDING",
                        "pagamento": pagamento,
                        "inseriti": inserted,
                        "saltati": parsed["saltati"],
                        "file_sha256": file_sha256,
                        "file_name": Path(
                            file.filename or "vendite-vending.csv"
                        ).name,
                        "accounting_snapshot_id": accounting_snapshot["id"],
                        "previous_accounting_snapshot_id": accounting_snapshot.get(
                            "previous_snapshot_id"
                        ),
                        "righe_file": file_rows,
                        "righe_storico_excel": parsed["righe_storico_excel"],
                        "righe_csv_gia_importate": parsed[
                            "righe_csv_gia_importate"
                        ],
                        **stock_summary,
                        "status": "active",
                    }
                )
        except Exception as exc:
            if inserted:
                await db.vending_accounting_events.delete_many(
                    {"source_batch_id": batch_id}
                )
                await db.vendite_bulk_imports.delete_one({"id": batch_id})
            if previous_accounting_state is None:
                await db.vending_accounting_state.delete_one({"id": accounting_state_id})
            else:
                await db.vending_accounting_state.replace_one(
                    {"id": accounting_state_id},
                    previous_accounting_state,
                    upsert=True,
                )
            if cash_adjusted:
                await adjust_vending_cash_balance(-imported_cash)
            await run_transactionally(
                lambda session: rollback_csv_import_sales(batch_id, session)
            )
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(500, f"Import CSV annullato: {exc}") from exc

        return {
            "inseriti": inserted,
            "saltati": parsed["saltati"],
            "errori": errors,
            "delimitatore": parsed["delimitatore"],
            "pagamenti": payment_counts,
            "importi_pagamenti": payment_amounts,
            "contanti_aggiunti_giacenza": round(imported_cash, 2),
            "righe_file": file_rows,
            "righe_gia_presenti": file_rows - len(parsed["righe"]),
            "righe_storico_excel": parsed["righe_storico_excel"],
            "righe_csv_gia_importate": parsed["righe_csv_gia_importate"],
            "ultima_vendita_excel": cutoff.isoformat() if cutoff else None,
            "file_sha256": file_sha256,
            **stock_summary,
            "batch_id": batch_id if inserted else None,
            "created_at": created_at if inserted else None,
            "sorgente": "CSV_VENDING",
            "contabilita_allineata": True,
            "contabilita_snapshot_id": accounting_snapshot["id"],
            "contabilita_importi_pagamenti": accounting_snapshot.get(
                "importi_pagamenti", {}
            ),
            "contabilita_totale": accounting_snapshot.get("totale", 0),
            "backup_id": backup["id"],
        }

    @router.get("/vendite/csv/ultimo")
    async def ultimo_csv_vending():
        db = get_database()
        return await db.vendite_bulk_imports.find_one(
            {"status": "active", "sorgente": "CSV_VENDING"},
            {"_id": 0},
            sort=[("created_at", -1)],
        )

    @router.post("/vendite/csv/{batch_id}/annulla")
    async def annulla_csv_vending(batch_id: str):
        db = get_database()
        claim = await db.vendite_bulk_imports.update_one(
            {"id": batch_id, "status": "active", "sorgente": "CSV_VENDING"},
            {"$set": {"status": "annulling"}},
        )
        if claim.modified_count != 1:
            raise HTTPException(404, "Caricamento CSV non trovato o già annullato")
        batch = await db.vendite_bulk_imports.find_one({"id": batch_id}, {"_id": 0})

        async def operation(session):
            kwargs = session_kwargs(session)
            rollback = await rollback_csv_import_sales(batch_id, session)
            cash_to_restore = float(rollback["contanti"])
            if cash_to_restore:
                await adjust_vending_cash_balance(-cash_to_restore, session)

            accounting_restored = False
            ledger_deletion = await db.vending_accounting_events.delete_many(
                {"source_batch_id": batch_id}, **kwargs
            )
            if int(getattr(ledger_deletion, "deleted_count", 0) or 0):
                accounting_restored = True
                await db.vending_accounting_state.update_one(
                    {"id": accounting_state_id},
                    {
                        "$set": {
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                            "last_undo_batch_id": batch_id,
                        }
                    },
                    **kwargs,
                )
            snapshot_id = (batch or {}).get("accounting_snapshot_id")
            if snapshot_id and snapshot_id != accounting_state_id:
                snapshot = await db.vending_accounting_snapshots.find_one(
                    {"id": snapshot_id, "status": "active"}, {"_id": 0}, **kwargs
                )
                if snapshot:
                    reverted_at = datetime.now(timezone.utc).isoformat()
                    await db.vending_accounting_snapshots.update_one(
                        {"id": snapshot_id, "status": "active"},
                        {"$set": {"status": "reverted", "reverted_at": reverted_at}},
                        **kwargs,
                    )
                    previous_id = snapshot.get("previous_snapshot_id")
                    if previous_id:
                        await db.vending_accounting_snapshots.update_one(
                            {"id": previous_id, "status": "superseded"},
                            {
                                "$set": {
                                    "status": "active",
                                    "reactivated_at": reverted_at,
                                }
                            },
                            **kwargs,
                        )
                    accounting_restored = True
            await db.vendite_bulk_imports.update_one(
                {"id": batch_id, "status": "annulling"},
                {
                    "$set": {
                        "status": "annulled",
                        "annulled_at": datetime.now(timezone.utc).isoformat(),
                        "rimossi": rollback["rimossi"],
                    }
                },
                **kwargs,
            )
            return {
                "removed": rollback["rimossi"],
                "cash": cash_to_restore,
                "accounting_restored": accounting_restored,
            }

        try:
            result = await run_transactionally(operation)
        except Exception as exc:
            await db.vendite_bulk_imports.update_one(
                {"id": batch_id, "status": "annulling"},
                {"$set": {"status": "active"}},
            )
            raise HTTPException(500, f"Impossibile annullare il CSV: {exc}") from exc
        return {
            "ok": True,
            "batch_id": batch_id,
            "rimossi": result["removed"],
            "contanti_rimossi_giacenza": round(result["cash"], 2),
            "contabilita_ripristinata": result["accounting_restored"],
        }

    return {
        "router": router,
        "preview_csv_vending": preview_csv_vending,
        "rollback_csv_import_sales": rollback_csv_import_sales,
        "import_csv_vending": import_csv_vending,
        "ultimo_csv_vending": ultimo_csv_vending,
        "annulla_csv_vending": annulla_csv_vending,
    }

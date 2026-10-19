import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pymongo import UpdateOne


VENDING_ACCOUNTING_STATE_ID = "ledger"


def create_csv_vending_accounting_router(
    *,
    get_database: Callable[[], Any],
    read_capped: Callable[[UploadFile], Any],
    parse_csv_vending: Callable[..., Dict[str, Any]],
    summarize_vending_csv: Callable[[Dict[str, Any]], Dict[str, Any]],
    parse_sale_datetime: Callable[[Any], Any],
    filter_new_csv_vending_rows: Callable[..., Any],
    assign_event_keys: Callable[[List[Dict[str, Any]]], None],
    compatibility_key: Callable[[Dict[str, Any]], str],
    assign_accounting_keys: Callable[[List[Dict[str, Any]]], None],
    local_sale_datetime: Callable[[Any], Any],
    normalize_money: Callable[[Any], float],
    decimal_money: Callable[[Any], Decimal],
    sum_money: Callable[[Any], float],
    payment_bucket: Callable[[Any], str],
    bulk_upsert: Callable[..., Any],
    create_backup_snapshot: Callable[..., Any],
) -> Dict[str, Any]:
    router = APIRouter()

    async def exclude_imported_csv_vending_rows(
        rows: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], int]:
        if not rows:
            return [], 0
        db = get_database()
        keys = [row["csv_event_key"] for row in rows]
        parsed_dates = [
            parsed
            for parsed in (parse_sale_datetime(row.get("data")) for row in rows)
            if parsed
        ]
        date_floor = min(parsed_dates).replace(second=0, microsecond=0).isoformat()
        date_ceiling = (
            max(parsed_dates).replace(second=0, microsecond=0) + timedelta(minutes=1)
        ).isoformat()
        existing_rows = await db.vendite.find(
            {"sorgente": "CSV_VENDING", "data": {"$gte": date_floor, "$lt": date_ceiling}},
            {
                "_id": 0,
                "csv_event_key": 1,
                "data": 1,
                "codice": 1,
                "descrizione": 1,
                "importo": 1,
                "colonna": 1,
                "pagamento": 1,
            },
        ).to_list(None)
        existing_exact_keys = {
            str(row.get("csv_event_key") or "")
            for row in existing_rows
            if row.get("csv_event_key")
        }
        exact_matches = {key for key in keys if key in existing_exact_keys}
        compatibility_counts: Dict[str, int] = {}
        for existing in existing_rows:
            key = compatibility_key(existing)
            compatibility_counts[key] = compatibility_counts.get(key, 0) + 1
        for row in rows:
            if row["csv_event_key"] not in exact_matches:
                continue
            key = compatibility_key(row)
            compatibility_counts[key] = max(0, compatibility_counts.get(key, 0) - 1)
        duplicate_ids = {id(row) for row in rows if row["csv_event_key"] in exact_matches}
        for row in reversed(rows):
            if id(row) in duplicate_ids:
                continue
            key = compatibility_key(row)
            if compatibility_counts.get(key, 0) <= 0:
                continue
            compatibility_counts[key] -= 1
            duplicate_ids.add(id(row))
        new_rows = [row for row in rows if id(row) not in duplicate_ids]
        return new_rows, len(rows) - len(new_rows)

    async def latest_restored_vending_snapshot_at() -> Optional[str]:
        db = get_database()
        restored = await db.backup_snapshots.find_one(
            {"last_restored_at": {"$exists": True}},
            {"_id": 0, "created_at": 1},
            sort=[("last_restored_at", -1)],
        )
        return str((restored or {}).get("created_at") or "") or None

    async def annotate_csv_vending_stock_effects(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        db = get_database()
        column_codes = sorted(
            {str(row.get("colonna") or "") for row in rows if row.get("colonna")}
        )
        columns = (
            await db.vending.find({"colonna": {"$in": column_codes}}, {"_id": 0}).to_list(5000)
            if column_codes
            else []
        )
        by_code = {str(column.get("colonna") or ""): column for column in columns}
        restored_snapshot = await latest_restored_vending_snapshot_at()
        missing_baseline = set()
        missing_columns = set()
        stock_rows = 0
        reflected_rows = 0
        required_by_column: Dict[str, int] = {}
        for row in rows:
            column_code = str(row.get("colonna") or "")
            column = by_code.get(column_code)
            row["scala_giacenza"] = False
            if not column:
                row["giacenza_motivo"] = "COLONNA_NON_TROVATA"
                missing_columns.add(column_code or "(non indicata)")
                continue
            snapshot_value = column.get("giacenza_aggiornata_il") or restored_snapshot
            sale_at = local_sale_datetime(row.get("data"))
            snapshot_at = local_sale_datetime(snapshot_value)
            row["giacenza_rilevata_il"] = snapshot_value
            if not snapshot_at:
                missing_baseline.add(column_code)
                row["giacenza_motivo"] = "RIFERIMENTO_GIACENZA_MANCANTE"
                continue
            if sale_at and sale_at <= snapshot_at:
                reflected_rows += 1
                row["giacenza_motivo"] = "GIA_COMPRESA_NELLA_GIACENZA"
                continue
            row["scala_giacenza"] = True
            row["giacenza_motivo"] = "VENDITA_SUCCESSIVA_ALLA_GIACENZA"
            stock_rows += 1
            required_by_column[column_code] = required_by_column.get(column_code, 0) + 1

        stock_errors = []
        for column_code, required in sorted(required_by_column.items()):
            available = max(0, int(by_code[column_code].get("giacenza") or 0))
            if required > available:
                stock_errors.append(
                    {
                        "colonna": column_code,
                        "giacenza": available,
                        "vendite_da_scalare": required,
                        "mancanti": required - available,
                    }
                )
        return {
            "righe_da_scalare": stock_rows,
            "righe_gia_comprese_nella_giacenza": reflected_rows,
            "colonne_non_trovate": sorted(missing_columns),
            "colonne_senza_riferimento_giacenza": sorted(missing_baseline),
            "errori_giacenza": stock_errors,
            "importabile": not missing_columns and not missing_baseline and not stock_errors,
        }

    async def prepare_csv_vending_import(
        raw: str, pagamento: str
    ) -> tuple[Dict[str, Any], Any, int]:
        db = get_database()
        parsed = parse_csv_vending(raw, pagamento)
        historical_documents = await db.db_storico_vending_ext.find(
            {}, {"_id": 0, "raw": 1}
        ).to_list(None)
        all_rows = parsed["righe"]
        historical_rows, cutoff = filter_new_csv_vending_rows(all_rows, historical_documents)
        assign_event_keys(historical_rows)
        parsed["righe"], imported_count = await exclude_imported_csv_vending_rows(historical_rows)
        parsed["righe_storico_excel"] = len(all_rows) - len(historical_rows)
        parsed["righe_csv_gia_importate"] = imported_count
        parsed["stock"] = await annotate_csv_vending_stock_effects(parsed["righe"])
        return parsed, cutoff, len(all_rows)

    async def latest_vending_accounting_snapshot() -> Optional[Dict[str, Any]]:
        db = get_database()
        collection = getattr(db, "vending_accounting_snapshots", None)
        if collection is None:
            return None
        return await collection.find_one(
            {"status": "active"}, {"_id": 0}, sort=[("created_at", -1)]
        )

    def public_vending_accounting_snapshot(document: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in document.items()
            if key not in {"_id", "sha256", "csv_event_keys"}
        }

    async def ensure_vending_accounting_state() -> Dict[str, Any]:
        db = get_database()
        state = await db.vending_accounting_state.find_one(
            {"id": VENDING_ACCOUNTING_STATE_ID}, {"_id": 0}
        )
        if not state:
            previous = await latest_vending_accounting_snapshot()
            now = datetime.now(timezone.utc).isoformat()
            initial = {
                "id": VENDING_ACCOUNTING_STATE_ID,
                "created_at": now,
                "ledger_started_at": (previous or {}).get("created_at") or now,
                "updated_at": now,
                "baseline_importi_pagamenti": dict(
                    (previous or {}).get("importi_pagamenti") or {}
                ),
                "baseline_righe": int((previous or {}).get("righe") or 0),
                "baseline_data_da": (previous or {}).get("data_da"),
                "baseline_data_a": (previous or {}).get("data_a"),
                "baseline_event_keys": list((previous or {}).get("csv_event_keys") or []),
                "baseline_accounting_keys": list(
                    (previous or {}).get("csv_accounting_keys") or []
                ),
                "accounting_identity_version": 2,
                "migrated_from_snapshot_id": (previous or {}).get("id"),
                "last_file_name": (previous or {}).get("file_name"),
                "last_file_sha256": (previous or {}).get("sha256"),
                "last_file_rows": int((previous or {}).get("righe") or 0),
                "last_data_da": (previous or {}).get("data_da"),
                "last_data_a": (previous or {}).get("data_a"),
                "last_aligned_at": (previous or {}).get("created_at"),
            }
            await db.vending_accounting_state.update_one(
                {"id": VENDING_ACCOUNTING_STATE_ID}, {"$setOnInsert": initial}, upsert=True
            )
            state = await db.vending_accounting_state.find_one(
                {"id": VENDING_ACCOUNTING_STATE_ID}, {"_id": 0}
            )

        if not state.get("legacy_sales_seeded_at"):
            baseline_keys = set(state.get("baseline_event_keys") or [])
            legacy_sales = await db.vendite.find(
                {"sorgente": "CSV_VENDING", "csv_event_key": {"$exists": True}},
                {
                    "_id": 0,
                    "csv_event_key": 1,
                    "data": 1,
                    "importo": 1,
                    "pagamento": 1,
                    "batch_id": 1,
                    "created_at": 1,
                    "csv_file_sha256": 1,
                    "csv_accounting_key": 1,
                    "csv_accounting_occurrence": 1,
                    "csv_event_compatibility_key": 1,
                    "codice": 1,
                    "descrizione": 1,
                    "colonna": 1,
                },
            ).to_list(None)
            generated_rows = [
                {**sale, "prezzo": sale.get("importo"), "nome": sale.get("descrizione")}
                for sale in legacy_sales
            ]
            if any(not sale.get("csv_accounting_key") for sale in legacy_sales):
                generated_rows.sort(
                    key=lambda row: (
                        str(row.get("data") or ""),
                        str(row.get("csv_event_key") or ""),
                    ),
                    reverse=True,
                )
                assign_accounting_keys(generated_rows)
                generated_by_exact_key = {
                    row.get("csv_event_key"): row for row in generated_rows
                }
                for sale in legacy_sales:
                    if sale.get("csv_accounting_key"):
                        continue
                    generated = generated_by_exact_key.get(sale.get("csv_event_key"))
                    if not generated:
                        continue
                    sale["csv_event_compatibility_key"] = generated[
                        "csv_event_compatibility_key"
                    ]
                    sale["csv_accounting_occurrence"] = generated[
                        "csv_accounting_occurrence"
                    ]
                    sale["csv_accounting_key"] = generated["csv_accounting_key"]
            baseline_accounting_keys = set(state.get("baseline_accounting_keys") or [])
            operations = [
                UpdateOne(
                    {
                        "$or": [
                            {"csv_accounting_key": sale["csv_accounting_key"]},
                            {"csv_event_key": sale["csv_event_key"]},
                        ]
                    },
                    {
                        "$setOnInsert": {
                            "id": str(uuid.uuid4()),
                            "csv_event_key": sale["csv_event_key"],
                            "csv_event_compatibility_key": sale.get(
                                "csv_event_compatibility_key"
                            ),
                            "csv_accounting_occurrence": sale.get(
                                "csv_accounting_occurrence"
                            ),
                            "csv_accounting_key": sale["csv_accounting_key"],
                            "data": sale.get("data"),
                            "importo": normalize_money(sale.get("importo") or 0),
                            "pagamento": payment_bucket(sale.get("pagamento")),
                            "source_batch_id": sale.get("batch_id"),
                            "first_seen_at": sale.get("created_at") or state.get("created_at"),
                            "file_sha256": sale.get("csv_file_sha256"),
                            "migrated_from_vendite": True,
                        }
                    },
                    upsert=True,
                )
                for sale in legacy_sales
                if sale.get("csv_event_key") not in baseline_keys
                and sale.get("csv_accounting_key") not in baseline_accounting_keys
            ]
            seed_result = await bulk_upsert(
                db.vending_accounting_events, operations, batch_size=500
            )
            if seed_result["errori"]:
                raise HTTPException(500, "Migrazione del registro contabile CSV incompleta")
            seeded_at = datetime.now(timezone.utc).isoformat()
            await db.vending_accounting_state.update_one(
                {"id": VENDING_ACCOUNTING_STATE_ID},
                {
                    "$set": {
                        "legacy_sales_seeded_at": seeded_at,
                        "legacy_sales_seeded": seed_result["inseriti"],
                    }
                },
            )
            state["legacy_sales_seeded_at"] = seeded_at
            state["legacy_sales_seeded"] = seed_result["inseriti"]
        return state

    async def vending_accounting_ledger_summary() -> Optional[Dict[str, Any]]:
        db = get_database()
        state_collection = getattr(db, "vending_accounting_state", None)
        event_collection = getattr(db, "vending_accounting_events", None)
        if state_collection is None or event_collection is None:
            return None
        state = await state_collection.find_one(
            {"id": VENDING_ACCOUNTING_STATE_ID}, {"_id": 0}
        )
        if not state:
            return None
        amounts = {
            str(bucket): decimal_money(amount or 0)
            for bucket, amount in (state.get("baseline_importi_pagamenti") or {}).items()
        }
        dates = [
            value
            for value in [state.get("baseline_data_da"), state.get("baseline_data_a")]
            if value
        ]
        event_count = 0
        async for event in event_collection.find(
            {}, {"_id": 0, "pagamento": 1, "importo": 1, "data": 1}
        ):
            bucket = payment_bucket(event.get("pagamento"))
            amounts[bucket] = amounts.get(bucket, Decimal(0)) + decimal_money(
                event.get("importo") or 0
            )
            event_count += 1
            if event.get("data"):
                dates.append(event["data"])
        amounts = {bucket: normalize_money(amount) for bucket, amount in amounts.items()}
        cash = normalize_money(amounts.get("CONTANTI", 0))
        electronic = sum_money(
            value for bucket, value in amounts.items() if bucket != "CONTANTI"
        )
        return {
            "id": VENDING_ACCOUNTING_STATE_ID,
            "status": "active",
            "created_at": state.get("last_aligned_at")
            or state.get("updated_at")
            or state.get("created_at"),
            "file_name": state.get("last_file_name"),
            "righe": int(state.get("baseline_righe") or 0) + event_count,
            "eventi_incrementali": event_count,
            "importi_pagamenti": amounts,
            "totale_contanti": cash,
            "totale_elettronici": electronic,
            "totale": sum_money([cash, electronic]),
            "data_da": min(dates, default=None),
            "data_a": max(dates, default=None),
            "last_file_rows": int(state.get("last_file_rows") or 0),
            "baseline_event_keys": list(state.get("baseline_event_keys") or []),
            "baseline_accounting_keys": list(state.get("baseline_accounting_keys") or []),
            "accounting_identity_version": int(
                state.get("accounting_identity_version") or 1
            ),
            "migrated_from_snapshot_id": state.get("migrated_from_snapshot_id"),
            "ledger_started_at": state.get("ledger_started_at") or state.get("created_at"),
        }

    async def store_vending_accounting_snapshot(
        content: bytes,
        file_name: str,
        *,
        source_batch_id: Optional[str] = None,
        backup_reason: Optional[str] = None,
    ) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        db = get_database()
        parsed = parse_csv_vending(content.decode("utf-8-sig", errors="replace"))
        assign_event_keys(parsed["righe"])
        summary = summarize_vending_csv(parsed)
        if not summary["righe"]:
            raise HTTPException(422, "Il CSV non contiene vendite valide")
        if summary["errori"]:
            raise HTTPException(
                422,
                f"Il CSV contiene {len(summary['errori'])} righe non valide: "
                "correggile prima dell'allineamento",
            )
        backup = (
            await create_backup_snapshot(
                backup_reason,
                "pre-allineamento-contabile-vending",
                [
                    "vending_accounting_snapshots",
                    "vending_accounting_state",
                    "vending_accounting_events",
                ],
            )
            if backup_reason
            else None
        )
        created_at = datetime.now(timezone.utc).isoformat()
        state = await ensure_vending_accounting_state()
        baseline_keys = set(state.get("baseline_event_keys") or [])
        baseline_accounting_keys = set(state.get("baseline_accounting_keys") or [])
        exact_keys = [row["csv_event_key"] for row in parsed["righe"]]
        accounting_keys = [row["csv_accounting_key"] for row in parsed["righe"]]
        existing_events = await db.vending_accounting_events.find(
            {
                "$or": [
                    {"csv_event_key": {"$in": exact_keys}},
                    {"csv_accounting_key": {"$in": accounting_keys}},
                ]
            },
            {"_id": 0, "csv_event_key": 1, "csv_accounting_key": 1},
        ).to_list(None)
        existing_exact_keys = {
            str(event.get("csv_event_key") or "")
            for event in existing_events
            if event.get("csv_event_key")
        }
        existing_accounting_keys = {
            str(event.get("csv_accounting_key") or "")
            for event in existing_events
            if event.get("csv_accounting_key")
        }
        operations = []
        file_sha256 = hashlib.sha256(content).hexdigest()
        for row in parsed["righe"]:
            if (
                row["csv_event_key"] in baseline_keys
                or row["csv_accounting_key"] in baseline_accounting_keys
                or row["csv_event_key"] in existing_exact_keys
                or row["csv_accounting_key"] in existing_accounting_keys
            ):
                continue
            operations.append(
                UpdateOne(
                    {"csv_accounting_key": row["csv_accounting_key"]},
                    {
                        "$setOnInsert": {
                            "id": str(uuid.uuid4()),
                            "csv_event_key": row["csv_event_key"],
                            "csv_event_compatibility_key": row[
                                "csv_event_compatibility_key"
                            ],
                            "csv_accounting_occurrence": row[
                                "csv_accounting_occurrence"
                            ],
                            "csv_accounting_key": row["csv_accounting_key"],
                            "data": row.get("data"),
                            "importo": normalize_money(row.get("prezzo") or 0),
                            "pagamento": payment_bucket(row.get("pagamento")),
                            "source_batch_id": source_batch_id,
                            "first_seen_at": created_at,
                            "file_sha256": file_sha256,
                        }
                    },
                    upsert=True,
                )
            )
        write_result = await bulk_upsert(
            db.vending_accounting_events, operations, batch_size=500
        )
        if write_result["errori"]:
            raise HTTPException(500, "Registro contabile CSV incompleto: riprova")
        await db.vending_accounting_state.update_one(
            {"id": VENDING_ACCOUNTING_STATE_ID},
            {
                "$set": {
                    "updated_at": created_at,
                    "last_aligned_at": created_at,
                    "last_file_name": Path(file_name or "vendite-vending.csv").name,
                    "last_file_sha256": file_sha256,
                    "last_file_rows": int(summary.get("righe") or 0),
                    "last_data_da": summary.get("data_da"),
                    "last_data_a": summary.get("data_a"),
                    "accounting_identity_version": 2,
                }
            },
        )
        document = await vending_accounting_ledger_summary()
        document["eventi_nuovi"] = write_result["inseriti"]
        return document, backup

    @router.post("/contabilita/vending/preview-csv")
    async def preview_vending_accounting_csv(file: UploadFile = File(...)):
        content = await read_capped(file)
        return summarize_vending_csv(
            parse_csv_vending(content.decode("utf-8-sig", errors="replace"))
        )

    @router.post("/contabilita/vending/allinea-csv")
    async def align_vending_accounting_csv(file: UploadFile = File(...)):
        content = await read_capped(file)
        document, backup = await store_vending_accounting_snapshot(
            content,
            file.filename or "vendite-vending.csv",
            backup_reason="Prima dell'allineamento contabile al CSV vending",
        )
        response = public_vending_accounting_snapshot(document)
        response["backup_id"] = (backup or {}).get("id")
        return response

    @router.get("/contabilita/vending/fonte")
    async def vending_accounting_source():
        ledger = await vending_accounting_ledger_summary()
        if ledger:
            return public_vending_accounting_snapshot(ledger)
        snapshot = await latest_vending_accounting_snapshot()
        return public_vending_accounting_snapshot(snapshot) if snapshot else None

    return {
        "router": router,
        "exclude_imported_csv_vending_rows": exclude_imported_csv_vending_rows,
        "latest_restored_vending_snapshot_at": latest_restored_vending_snapshot_at,
        "annotate_csv_vending_stock_effects": annotate_csv_vending_stock_effects,
        "prepare_csv_vending_import": prepare_csv_vending_import,
        "latest_vending_accounting_snapshot": latest_vending_accounting_snapshot,
        "public_vending_accounting_snapshot": public_vending_accounting_snapshot,
        "ensure_vending_accounting_state": ensure_vending_accounting_state,
        "vending_accounting_ledger_summary": vending_accounting_ledger_summary,
        "store_vending_accounting_snapshot": store_vending_accounting_snapshot,
        "preview_vending_accounting_csv": preview_vending_accounting_csv,
        "align_vending_accounting_csv": align_vending_accounting_csv,
        "vending_accounting_source": vending_accounting_source,
    }

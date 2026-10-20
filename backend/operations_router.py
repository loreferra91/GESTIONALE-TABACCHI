"""Operational status, search, anomaly, and daily-report routes."""
import asyncio
from datetime import datetime, timezone
import hashlib
import logging

from fastapi import APIRouter, HTTPException


def create_operations_router(
    *,
    get_database,
    backup_collections,
    parse_sale_date,
    latest_sale_date,
    query_regex,
    vending_stock_by_code,
    apply_vending_stock,
    get_current_anomalies,
    get_dashboard,
    get_auto_order,
    schedule_daily_backup,
):
    router = APIRouter()

    async def data_status_payload():
        database = get_database()
        app_dates, imported_dates, counts, latest_import, latest_backup = await asyncio.gather(
            database.vendite.find({}, {"_id": 0, "data": 1}).to_list(None),
            database.db_storico_vend.find({}, {"_id": 0, "data": 1}).to_list(None),
            asyncio.gather(*[
                database[name].count_documents({}) for name in backup_collections
            ]),
            database.import_history.find_one({}, {"_id": 0}, sort=[("created_at", -1)]),
            database.backup_snapshots.find_one(
                {"status": {"$ne": "PRUNED"}},
                {"_id": 0},
                sort=[("created_at", -1)],
            ),
        )
        count_map = dict(zip(backup_collections, counts))

        def date_only(raw):
            value = (raw or {}).get("data") if isinstance(raw, dict) else raw
            if not value:
                return None
            parsed = parse_sale_date(value)
            return parsed.isoformat() if parsed else str(value)[:10]

        latest_app = latest_sale_date(app_dates)
        latest_imported = latest_sale_date(imported_dates)
        latest_days = [parsed for parsed in (latest_app, latest_imported) if parsed]
        latest_sales_day = max(latest_days).isoformat() if latest_days else None
        delay = None
        if latest_sales_day:
            try:
                delay = max(
                    0,
                    (
                        datetime.now(timezone.utc).date()
                        - datetime.fromisoformat(latest_sales_day).date()
                    ).days,
                )
            except ValueError:
                delay = None
        return {
            "counts": count_map,
            "latest_app_sale": date_only(latest_app),
            "latest_imported_sale": date_only(latest_imported),
            "latest_sales_day": latest_sales_day,
            "sales_data_delay_days": delay,
            "latest_import": latest_import,
            "latest_backup": latest_backup,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    @router.get("/data-status")
    async def data_status():
        daily_backup = None
        daily_backup_error = None
        try:
            daily_backup = await schedule_daily_backup()
            if daily_backup.get("error"):
                daily_backup_error = daily_backup["error"]
        except Exception:
            logging.exception("Creazione del backup giornaliero non riuscita")
            daily_backup_error = "Backup giornaliero non riuscito"
        return {
            **(await data_status_payload()),
            "daily_backup": daily_backup,
            "daily_backup_error": daily_backup_error,
        }

    @router.get("/global-search")
    async def global_search(q: str, limit: int = 8):
        text = (q or "").strip()
        if len(text) < 2:
            return {"items": []}
        capped_limit = max(1, min(int(limit or 8), 20))
        database = get_database()
        products, vending_totals = await asyncio.gather(
            database.prodotti.find(
                {"$or": [
                    {"codice": query_regex(text)},
                    {"descrizione": query_regex(text)},
                ]},
                {"_id": 0},
            ).sort("codice", 1).limit(capped_limit).to_list(capped_limit),
            vending_stock_by_code(),
        )
        apply_vending_stock(products, vending_totals)
        return {"items": [{
            "type": "prodotto",
            "codice": product.get("codice"),
            "descrizione": product.get("descrizione", ""),
            "categoria": product.get("categoria", ""),
            "prezzo": product.get("prezzo", 0),
            "giacenza_negozio": product.get("giacenza_negozio", 0),
            "giacenza_vending": product.get("giacenza_vending", 0),
        } for product in products]}

    @router.get("/anomalie")
    async def anomalie():
        database = get_database()
        products = await database.prodotti.find({}, {"_id": 0}).to_list(5000)
        product_codes = {product.get("codice") for product in products if product.get("codice")}
        items = []
        for product in products:
            code = product.get("codice", "")
            if int(product.get("giacenza_negozio", 0) or 0) < 0:
                items.append({
                    "tipo": "stock_negativo", "severita": "alta", "codice": code,
                    "descrizione": product.get("descrizione", ""),
                    "messaggio": f"Giacenza negozio negativa ({product.get('giacenza_negozio')})",
                    "azione": "Verifica import o movimenti recenti",
                })
            if not float(product.get("prezzo", 0) or 0):
                items.append({
                    "tipo": "prezzo_mancante", "severita": "media", "codice": code,
                    "descrizione": product.get("descrizione", ""),
                    "messaggio": "Prezzo a zero",
                    "azione": "Aggiorna prodotto o listino ADM",
                })
            if not product.get("descrizione"):
                items.append({
                    "tipo": "descrizione_mancante", "severita": "bassa", "codice": code,
                    "descrizione": "", "messaggio": "Descrizione mancante",
                    "azione": "Completa anagrafica prodotto",
                })

        sales_codes, imported_codes = await asyncio.gather(
            database.vendite.distinct("codice"),
            database.db_storico_vend.distinct("codice"),
        )
        unknown_codes = sorted({
            code for code in [*sales_codes, *imported_codes]
            if code and code not in product_codes
        })[:200]
        for code in unknown_codes:
            items.append({
                "tipo": "vendita_senza_prodotto", "severita": "media", "codice": code,
                "descrizione": "", "messaggio": "Vendite presenti ma prodotto non trovato",
                "azione": "Importa listino/prodotti aggiornati o crea prodotto",
            })

        status = await data_status_payload()
        if status.get("sales_data_delay_days") is not None and status["sales_data_delay_days"] > 2:
            items.insert(0, {
                "tipo": "storico_vecchio", "severita": "alta", "codice": "",
                "descrizione": "",
                "messaggio": (
                    f"Ultima vendita disponibile: {status.get('latest_sales_day')} "
                    f"({status['sales_data_delay_days']} giorni fa)"
                ),
                "azione": "Importa Excel aggiornato prima di confermare ordini",
            })
        for item in items:
            item["id"] = hashlib.sha256(
                f"{item.get('tipo', '')}\0{item.get('codice', '')}".encode("utf-8")
            ).hexdigest()[:24]
        ignored = set(await database.anomalie_ignorate.distinct("key"))
        items = [item for item in items if item["id"] not in ignored]
        return {
            "summary": {
                "alta": sum(1 for item in items if item["severita"] == "alta"),
                "media": sum(1 for item in items if item["severita"] == "media"),
                "bassa": sum(1 for item in items if item["severita"] == "bassa"),
                "totale": len(items),
            },
            "items": items,
        }

    @router.delete("/anomalie")
    async def dismiss_all_anomalies():
        current = await get_current_anomalies()
        now = datetime.now(timezone.utc).isoformat()
        keys = [item["id"] for item in current["items"]]
        if keys:
            collection = get_database().anomalie_ignorate
            existing = set(await collection.distinct("key", {"key": {"$in": keys}}))
            missing = [{"key": key, "dismissed_at": now} for key in keys if key not in existing]
            if missing:
                await collection.insert_many(missing)
        return {"ok": True, "eliminate": len(keys)}

    @router.delete("/anomalie/{item_key}")
    async def dismiss_anomaly(item_key: str):
        current = await get_current_anomalies()
        if item_key not in {item["id"] for item in current["items"]}:
            raise HTTPException(404, "Anomalia non trovata")
        await get_database().anomalie_ignorate.update_one(
            {"key": item_key},
            {"$set": {
                "key": item_key,
                "dismissed_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        return {"ok": True}

    @router.get("/report/giornaliero")
    async def report_giornaliero():
        status, dashboard_data, anomaly_data, auto_order = await asyncio.gather(
            data_status_payload(),
            get_dashboard(),
            get_current_anomalies(),
            get_auto_order(),
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "dashboard": dashboard_data,
            "anomalie": anomaly_data["summary"],
            "auto_order": {
                "righe": auto_order.get("n_righe", 0),
                "totale": auto_order.get("totale", 0),
                "data_riferimento_domanda": auto_order.get("data_riferimento_domanda"),
                "da_controllare": (
                    auto_order.get("riepilogo_stati", {}).get("CONTROLLO MANUALE", 0) or 0
                ) + (auto_order.get("n_anomalie_stock", 0) or 0),
            },
        }

    return {
        "router": router,
        "data_status_payload": data_status_payload,
        "data_status": data_status,
        "global_search": global_search,
        "anomalie": anomalie,
        "dismiss_all_anomalies": dismiss_all_anomalies,
        "dismiss_anomaly": dismiss_anomaly,
        "report_giornaliero": report_giornaliero,
    }

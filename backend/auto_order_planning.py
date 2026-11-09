"""Pure, conservative planning rules for Auto-Order.

Dates without a confirmed delivery day are never treated as available stock.
The legacy policy remains active until a lead time is configured.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
import math
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")
PENDING_STATES = {"CREATO", "CONFERMATO", "INVIATO", "PARZIALMENTE_RICEVUTO"}


def commercial_day(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return (value.astimezone(ROME) if value.tzinfo else value).date()
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return commercial_day(parsed)
        except ValueError:
            for date_format in ("%d/%m/%Y", "%d-%m-%Y"):
                try:
                    return datetime.strptime(str(value)[:10], date_format).date()
                except ValueError:
                    continue
            return None
    return None


def reconcile_sales(native_rows, reference_day, short_days, long_days):
    """Count only persisted native sales, retaining channel and source evidence."""
    daily = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for row in native_rows:
        key = row.get("_id") or {}
        if not isinstance(key, dict):
            continue
        code = str(key.get("codice") or "").strip()
        day = commercial_day(key.get("day"))
        if not code or day is None or not (reference_day - timedelta(days=long_days - 1) <= day <= reference_day):
            continue
        channel = str(key.get("canale") or "").upper()
        source = "CSV_VENDING" if str(key.get("sorgente") or "").upper() == "CSV_VENDING" else "VENDITE"
        daily[code][day][(channel, source)] += int(row.get("tot") or 0)
    result = {}
    for code, days in daily.items():
        short = long = shop = vending = shop_short = vending_short = 0
        unknown_channel = False
        sources, anomalies = set(), set()
        for day, feeds in days.items():
            if ("VENDING", "VENDITE") in feeds and ("VENDING", "CSV_VENDING") in feeds:
                anomalies.add("VENDING_APP_CSV_POTENZIALMENTE_SOVRAPPOSTE")
            amount = sum(feeds.values())
            if amount < 0:
                anomalies.add("VENDITE_NEGATIVE_O_RETTIFICHE")
            long += amount
            if day >= reference_day - timedelta(days=short_days - 1):
                short += amount
            for (channel, source), quantity in feeds.items():
                sources.add(source)
                if channel == "VENDING":
                    vending += quantity
                    if day >= reference_day - timedelta(days=short_days - 1):
                        vending_short += quantity
                elif channel == "NEGOZIO":
                    shop += quantity
                    if day >= reference_day - timedelta(days=short_days - 1):
                        shop_short += quantity
                else:
                    unknown_channel = True
                    anomalies.add("CANALE_VENDITA_NON_RICONOSCIUTO")
        result[code] = {
            "venduto_breve": max(0, short), "venduto_lungo": max(0, long),
            "venduto_negozio": None if unknown_channel else max(0, shop),
            "venduto_vending": None if unknown_channel else max(0, vending),
            "venduto_negozio_breve": None if unknown_channel else max(0, shop_short),
            "venduto_vending_breve": None if unknown_channel else max(0, vending_short),
            "venduto_totale": max(0, long),
            "fonti_utilizzate": sorted(sources),
            "fonte_prevalente": "VENDITE" if sources else "NESSUNA",
            "qualita_dati": "DATI_PARZIALI" if anomalies or unknown_channel else "DATI_AFFIDABILI",
            "anomalie_riconciliazione": sorted(anomalies),
            "periodo_coperto": {"dal": min(days).isoformat(), "al": max(days).isoformat()},
        }
    return result


def pending_by_code(batch_rows, order_rows, today, horizon_days):
    batches = {row.get("batch_key"): row for row in batch_rows if row.get("batch_key")}
    result = defaultdict(lambda: {"quantita_in_arrivo": 0, "quantita_senza_data": 0, "ricezioni": [], "anomalie": []})
    for row in order_rows:
        batch = batches.get(row.get("batch_key"))
        if not batch or batch.get("status", "COMPLETED") != "COMPLETED" or str(batch.get("stato") or "").upper() not in PENDING_STATES:
            continue
        code = row.get("codice")
        if not code:
            continue
        ordered = int(row.get("quantita_ordinata", row.get("quantita", 0)) or 0)
        received = int(row.get("quantita_ricevuta", 0) or 0)
        if ordered <= 0 or received < 0 or received > ordered:
            result[code]["anomalie"].append("QUANTITA_ORDINE_INCOERENTE")
            continue
        if str(row.get("stato") or batch.get("stato") or "").upper() not in PENDING_STATES:
            continue
        remaining = max(0, ordered - received)
        if not remaining:
            continue
        delivery = commercial_day(batch.get("data_consegna_prevista") or row.get("data_consegna_prevista"))
        if delivery is None:
            result[code]["quantita_senza_data"] += remaining
        else:
            if delivery <= today:
                result[code]["quantita_senza_data"] += remaining
                continue
            result[code]["ricezioni"].append({"data": delivery.isoformat(), "quantita": remaining})
            if delivery <= today + timedelta(days=horizon_days):
                result[code]["quantita_in_arrivo"] += remaining
    return result


def projected_shortage(stock, daily_demand, receipts, today, days):
    balance = float(stock)
    arrivals = defaultdict(int)
    for receipt in receipts:
        day = commercial_day(receipt.get("data"))
        if day:
            arrivals[day] += int(receipt.get("quantita") or 0)
    for offset in range(days + 1):
        day = today + timedelta(days=offset)
        if offset:
            balance += arrivals[day]
        if balance < 0:
            return day.isoformat()
        balance -= daily_demand
        if balance < 0:
            return day.isoformat()
    return None


def rounded_order_quantity(need, lot):
    if lot <= 0:
        raise ValueError("Lotto non valido")
    return max(0, math.ceil(max(0, need) / lot) * lot)

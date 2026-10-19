import asyncio
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List

from fastapi import APIRouter


def parse_sale_date(value: Any):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for date_format in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw[:10], date_format).date()
        except ValueError:
            continue
    return None


def latest_sale_date(documents: List[Dict[str, Any]]):
    parsed_dates = [parse_sale_date(document.get("data")) for document in documents]
    return max((parsed for parsed in parsed_dates if parsed), default=None)


def parse_utc_datetime(value: Any):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_sale_after_excel_import(
    document: Dict[str, Any], latest_imported_sale: Any, latest_import_created_at: Any
) -> bool:
    import_cutoff = parse_utc_datetime(latest_import_created_at)
    created_at = parse_utc_datetime(document.get("created_at"))
    if import_cutoff and created_at:
        return created_at > import_cutoff
    sale_cutoff = parse_sale_date(latest_imported_sale)
    sale_day = parse_sale_date(document.get("data"))
    return not sale_cutoff or bool(sale_day and sale_day > sale_cutoff)


def is_cash_payment(payment_method: Any) -> bool:
    normalized = (
        unicodedata.normalize("NFKD", str(payment_method or ""))
        .encode("ascii", "ignore")
        .decode()
        .upper()
    )
    return not normalized or "CONTANT" in normalized


def vending_payment_bucket(payment_method: Any) -> str:
    normalized = (
        unicodedata.normalize("NFKD", str(payment_method or ""))
        .encode("ascii", "ignore")
        .decode()
        .upper()
    )
    normalized = re.sub(r"[^A-Z0-9]+", "", normalized)
    if not normalized or "CONTANT" in normalized:
        return "CONTANTI"
    if "PAGOBANCOMAT" in normalized or normalized in {"BANCOMAT", "PAGOBANCO"}:
        return "PAGOBANCOMAT"
    if "CART" in normalized:
        return "CARTE"
    return "ALTRO_ELETTRONICO"


def create_dashboard_router(
    *,
    get_database: Callable[[], Any],
    normalize_money: Callable[[Any], float],
    decimal_money: Callable[[Any], Decimal],
    sum_money: Callable[[Any], float],
    get_pivot: Callable[[], Any],
    get_latest_accounting_import: Callable[[], Any],
    get_vending_accounting_ledger_summary: Callable[[], Any],
    get_latest_vending_accounting_snapshot: Callable[[], Any],
    get_versamenti_summary: Callable[[], Any],
    get_prelievi_vending_summary: Callable[[], Any],
    get_scontrini_vending_summary: Callable[[], Any],
    get_vending_list: Callable[[], Any],
) -> Dict[str, Any]:
    router = APIRouter()

    def vending_payment_values(documents: List[Dict[str, Any]], legacy_raw: bool) -> List[tuple]:
        values = []
        for document in documents:
            if legacy_raw:
                raw = document.get("raw") or []
                if len(raw) < 12:
                    continue
                contabilizzata = str(raw[15] if len(raw) > 15 else "").strip().upper()
                esito = str(raw[16] if len(raw) > 16 else "").strip().upper()
                if contabilizzata and contabilizzata not in {"SI", "SÌ", "YES", "1", "TRUE"}:
                    continue
                if "ANNULL" in esito:
                    continue
                importo = raw[7]
                pagamento = raw[11]
            else:
                importo = document.get("importo")
                pagamento = document.get("pagamento")
            try:
                amount = normalize_money(importo or 0)
            except (TypeError, ValueError):
                continue
            values.append((amount, str(pagamento or "").strip()))
        return values

    def latest_historical_vending_date(documents: List[Dict[str, Any]]):
        parsed_dates = []
        for document in documents:
            raw = document.get("raw") or []
            value = raw[2] if len(raw) > 2 and raw[2] else (raw[1] if len(raw) > 1 else None)
            parsed = parse_sale_date(value)
            if parsed:
                parsed_dates.append(parsed)
        return max(parsed_dates, default=None)

    def calculate_dashboard_balances(
        vending_payments: List[tuple],
        saldo_cassa: float,
        totale_prelievi_vending: float = 0,
        totale_scontrini: float = 0,
        giacenza_iniziale_vending: float = 0,
    ) -> Dict[str, float]:
        cash = cards = pagobancomat = other_electronic = Decimal(0)
        for amount, payment_method in vending_payments:
            amount = decimal_money(amount)
            bucket = vending_payment_bucket(payment_method)
            if bucket == "CONTANTI":
                cash += amount
            elif bucket == "CARTE":
                cards += amount
            elif bucket == "PAGOBANCOMAT":
                pagobancomat += amount
            else:
                other_electronic += amount

        cash, cards, pagobancomat, other_electronic = map(
            normalize_money, (cash, cards, pagobancomat, other_electronic)
        )
        electronic = sum_money([cards, pagobancomat, other_electronic])
        total = sum_money([cash, electronic])
        saldo_cassa = normalize_money(saldo_cassa or 0)
        prelievo_vending_lordo = normalize_money(totale_prelievi_vending or 0)
        totale_scontrini = normalize_money(totale_scontrini or 0)
        prelievo_vending = sum_money([prelievo_vending_lordo, -totale_scontrini])
        giacenza_iniziale_vending = normalize_money(giacenza_iniziale_vending or 0)
        giacenza_vending = sum_money([giacenza_iniziale_vending, cash, -prelievo_vending_lordo])
        saldo_casse = sum_money([prelievo_vending, saldo_cassa])
        return {
            "saldoVendingTotale": total,
            "venditeVendingContanti": cash,
            "giacenzaInizialeVendingContanti": giacenza_iniziale_vending,
            "giacenzaVendingContanti": giacenza_vending,
            "prelievoVendingLordo": prelievo_vending_lordo,
            "prelievoVendingNetto": prelievo_vending,
            "prelievoVending": prelievo_vending,
            "scontriniVending": totale_scontrini,
            "prelievoDaVending": prelievo_vending,
            "prelieviContantiDaVending": prelievo_vending,
            "prelieviContantiCassaVending": prelievo_vending,
            "cassaVending": giacenza_vending,
            "giacenzaAttualeCassaVending": giacenza_vending,
            "prelieviVending": prelievo_vending,
            "saldoCassaNegozioEVending": saldo_casse,
            "saldoVendingContanti": cash,
            "saldoVendingElettronico": electronic,
            "venditeVendingCarte": cards,
            "venditeVendingPagoBancomat": pagobancomat,
            "venditeVendingAltroElettronico": other_electronic,
            "saldoCassa": saldo_cassa,
            "totalePrelievi": prelievo_vending,
            "differenzaCassaVendingContanti": saldo_casse,
        }

    async def dashboard_balances(
        saldo_cassa: float,
        totale_prelievi_vending: float = 0,
        totale_scontrini: float = 0,
        latest_import_created_at: Any = None,
        giacenza_iniziale_vending: float = 0,
    ) -> Dict[str, float]:
        db = get_database()
        historical_docs, app_docs, accounting_ledger, accounting_snapshot = await asyncio.gather(
            db.db_storico_vending_ext.find({}, {"_id": 0, "raw": 1}).to_list(None),
            db.vendite.find(
                {"canale": {"$regex": "^VENDING$", "$options": "i"}},
                {
                    "_id": 0,
                    "data": 1,
                    "created_at": 1,
                    "importo": 1,
                    "pagamento": 1,
                    "sorgente": 1,
                    "csv_event_key": 1,
                    "csv_file_sha256": 1,
                },
            ).to_list(None),
            get_vending_accounting_ledger_summary(),
            get_latest_vending_accounting_snapshot(),
        )
        accounting_source = accounting_ledger or accounting_snapshot
        if accounting_source:
            amounts = accounting_source.get("importi_pagamenti") or {}
            payments = [(float(amount or 0), bucket) for bucket, amount in amounts.items()]
            aligned_at = parse_utc_datetime(
                accounting_source.get("ledger_started_at") or accounting_source.get("created_at")
            )
            snapshot_event_keys = set(
                accounting_source.get("csv_event_keys")
                or [
                    *(accounting_source.get("baseline_event_keys") or []),
                    *(accounting_source.get("event_keys") or []),
                ]
            )
            snapshot_file_sha256 = str(accounting_source.get("sha256") or "")
            for document in app_docs:
                if accounting_ledger and str(document.get("sorgente") or "").upper() == "CSV_VENDING":
                    continue
                event_key = str(document.get("csv_event_key") or "")
                file_sha256 = str(document.get("csv_file_sha256") or "")
                if event_key and event_key in snapshot_event_keys:
                    continue
                if snapshot_file_sha256 and file_sha256 == snapshot_file_sha256:
                    continue
                created_at = parse_utc_datetime(document.get("created_at"))
                if not aligned_at or not created_at or created_at <= aligned_at:
                    continue
                payments.extend(vending_payment_values([document], legacy_raw=False))
            result = calculate_dashboard_balances(
                payments,
                saldo_cassa,
                totale_prelievi_vending,
                totale_scontrini,
                giacenza_iniziale_vending,
            )
            result.update(
                {
                    "fonteVendingContabile": "CSV_VENDING",
                    "fonteVendingFile": accounting_source.get("file_name"),
                    "fonteVendingRighe": accounting_source.get("righe"),
                    "fonteVendingDataDa": accounting_source.get("data_da"),
                    "fonteVendingDataA": accounting_source.get("data_a"),
                    "fonteVendingAllineataIl": accounting_source.get("created_at"),
                }
            )
            return result

        payments = vending_payment_values(historical_docs, legacy_raw=True)
        cutoff = latest_historical_vending_date(historical_docs)
        supplemental_app_docs = []
        for document in app_docs:
            if historical_docs and cutoff is None and not latest_import_created_at:
                continue
            if not is_sale_after_excel_import(document, cutoff, latest_import_created_at):
                continue
            supplemental_app_docs.append(document)
        payments.extend(vending_payment_values(supplemental_app_docs, legacy_raw=False))
        return calculate_dashboard_balances(
            payments,
            saldo_cassa,
            totale_prelievi_vending,
            totale_scontrini,
            giacenza_iniziale_vending,
        )

    async def supplemental_store_cash_sales(
        latest_imported_sale: Any, latest_import_created_at: Any = None
    ) -> float:
        db = get_database()
        cutoff = parse_sale_date(latest_imported_sale)
        documents = await db.vendite.find(
            {}, {"_id": 0, "data": 1, "created_at": 1, "canale": 1, "pagamento": 1, "importo": 1}
        ).to_list(None)
        total = Decimal(0)
        for document in documents:
            channel = str(document.get("canale") or "NEGOZIO").strip().upper()
            if channel == "VENDING" or not is_cash_payment(document.get("pagamento")):
                continue
            if not is_sale_after_excel_import(document, cutoff, latest_import_created_at):
                continue
            try:
                total += decimal_money(document.get("importo") or 0)
            except (TypeError, ValueError):
                continue
        return normalize_money(total)

    async def shop_sold_accounting_corrections(latest_import_created_at: Any = None) -> float:
        db = get_database()
        cutoff = parse_utc_datetime(latest_import_created_at)
        documents = await db.rettifiche_venduti.find(
            {"campo": "venduti_negozio"},
            {"_id": 0, "created_at": 1, "importo_contabile": 1},
        ).to_list(None)
        total = Decimal(0)
        for document in documents:
            created_at = parse_utc_datetime(document.get("created_at"))
            if cutoff and (not created_at or created_at <= cutoff):
                continue
            try:
                total += decimal_money(document.get("importo_contabile") or 0)
            except (TypeError, ValueError):
                continue
        return normalize_money(total)

    async def dashboard_sales_trend(days: int = 30) -> Dict[str, Any]:
        db = get_database()
        app_sales, imported_sales = await asyncio.gather(
            db.vendite.find({}, {"_id": 0, "data": 1, "importo": 1}).to_list(None),
            db.db_storico_vend.find({}, {"_id": 0, "data": 1, "importo": 1}).to_list(None),
        )
        totals_by_date: Dict[Any, Decimal] = {}
        for sale in [*imported_sales, *app_sales]:
            parsed_day = parse_sale_date(sale.get("data"))
            if parsed_day is None:
                continue
            try:
                amount = decimal_money(sale.get("importo") or 0)
            except (TypeError, ValueError):
                continue
            totals_by_date[parsed_day] = totals_by_date.get(parsed_day, 0) + amount

        if not totals_by_date:
            return {
                "ultimo_giorno": None,
                "totale_ultimo_giorno": 0,
                "variazione_pct": None,
                "totale_periodo": 0,
                "serie": [],
            }
        end_date = max(totals_by_date)
        start_date = end_date - timedelta(days=days - 1)
        series = [
            {
                "data": (start_date + timedelta(days=offset)).isoformat(),
                "importo": normalize_money(totals_by_date.get(start_date + timedelta(days=offset), 0)),
            }
            for offset in range(days)
        ]
        latest_total = normalize_money(totals_by_date.get(end_date, 0))
        previous_values = [point["importo"] for point in series[:-1]]
        previous_average = sum(previous_values) / len(previous_values) if previous_values else 0
        variation = (
            round(((latest_total - previous_average) / previous_average) * 100, 1)
            if previous_average
            else None
        )
        return {
            "ultimo_giorno": end_date.isoformat(),
            "totale_ultimo_giorno": latest_total,
            "variazione_pct": variation,
            "totale_periodo": sum_money(point["importo"] for point in series),
            "serie": series,
        }

    @router.get("/dashboard")
    async def dashboard():
        db = get_database()
        pivot_result = await get_pivot()
        ultimo_import_contabile, date_vendite_importate = await asyncio.gather(
            get_latest_accounting_import(),
            db.db_storico_vend.find({}, {"_id": 0, "data": 1}).to_list(None),
        )
        ultima_vendita_importata = latest_sale_date(date_vendite_importate)
        import_totali = (ultimo_import_contabile or {}).get("totali", {})
        venduto_negozio_excel = normalize_money(import_totali.get("valore_venduto_negozio_excel") or 0)
        venduto_vending_excel = normalize_money(import_totali.get("valore_venduto_vending_excel") or 0)
        venduto_totale_excel = normalize_money(import_totali.get("valore_venduto_totale_excel") or 0)
        if venduto_totale_excel:
            pivot_result["kpi"]["valore_venduto"] = round(venduto_totale_excel, 2)
            pivot_result["kpi"]["valore_venduto_negozio_excel"] = round(venduto_negozio_excel, 2)
            pivot_result["kpi"]["valore_venduto_vending_excel"] = round(venduto_vending_excel, 2)
            pivot_result["kpi"]["valore_venduto_totale_excel"] = round(venduto_totale_excel, 2)
            pivot_result["kpi"]["margine_lordo"] = sum_money(
                [venduto_totale_excel, -(pivot_result["kpi"].get("valore_acquistato") or 0)]
            )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_sales = {"tot": Decimal(0), "pezzi": 0}
        async for sale in db.vendite.find(
            {"data": {"$regex": f"^{today}"}}, {"importo": 1, "quantita": 1}
        ):
            today_sales["tot"] += decimal_money(sale.get("importo") or 0)
            today_sales["pezzi"] += sale.get("quantita") or 0

        (
            versamenti,
            prelievi,
            scontrini,
            giacenza_iniziale_doc,
            venduto_negozio_app_contanti,
            rettifiche_venduto_negozio,
        ) = await asyncio.gather(
            get_versamenti_summary(),
            get_prelievi_vending_summary(),
            get_scontrini_vending_summary(),
            db.parametri.find_one(
                {"nome": "GIACENZA_INIZIALE_CONTANTI_VENDING"},
                {"_id": 0, "valore": 1},
            ),
            supplemental_store_cash_sales(
                ultima_vendita_importata, (ultimo_import_contabile or {}).get("created_at")
            ),
            shop_sold_accounting_corrections((ultimo_import_contabile or {}).get("created_at")),
        )
        giacenza_iniziale_vending = normalize_money((giacenza_iniziale_doc or {}).get("valore") or 0)
        venduto_negozio_contabilizzato = sum_money(
            [venduto_negozio_excel, venduto_negozio_app_contanti, rettifiche_venduto_negozio]
        )
        liquidita_residua = sum_money([venduto_negozio_contabilizzato, -versamenti["totale"]])
        saldi, andamento_vendite = await asyncio.gather(
            dashboard_balances(
                liquidita_residua,
                prelievi["totale"],
                scontrini["totale"],
                (ultimo_import_contabile or {}).get("created_at"),
                giacenza_iniziale_vending,
            ),
            dashboard_sales_trend(),
        )
        vending = await get_vending_list()
        vend_da_caricare = sum(1 for item in vending if (item.get("proposta") or 0) > 0)
        return {
            "kpi": pivot_result["kpi"],
            "vendite_oggi": {
                "importo": normalize_money(today_sales.get("tot") or 0),
                "pezzi": today_sales.get("pezzi") or 0,
            },
            "totale_versamenti": versamenti["totale"],
            "totale_prelievi": prelievi["totale"],
            "totale_scontrini_vending": scontrini["totale"],
            "giacenza_vending_contanti": saldi["giacenzaVendingContanti"],
            "giacenza_iniziale_vending_contanti": giacenza_iniziale_vending,
            "venduto_negozio_excel": round(venduto_negozio_excel, 2),
            "venduto_negozio_app_contanti": venduto_negozio_app_contanti,
            "rettifiche_venduto_negozio": rettifiche_venduto_negozio,
            "venduto_negozio_contabilizzato": venduto_negozio_contabilizzato,
            "liquidita_residua": liquidita_residua,
            "saldo_cassa": liquidita_residua,
            "saldi": saldi,
            "andamento_vendite": andamento_vendite,
            "vending_da_caricare": vend_da_caricare,
            "vending_totale": len(vending),
        }

    return {
        "router": router,
        "vending_payment_values": vending_payment_values,
        "latest_historical_vending_date": latest_historical_vending_date,
        "calculate_dashboard_balances": calculate_dashboard_balances,
        "dashboard_balances": dashboard_balances,
        "supplemental_store_cash_sales": supplemental_store_cash_sales,
        "shop_sold_accounting_corrections": shop_sold_accounting_corrections,
        "dashboard_sales_trend": dashboard_sales_trend,
        "dashboard": dashboard,
    }

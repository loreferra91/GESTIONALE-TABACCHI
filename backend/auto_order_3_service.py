"""Read-only comparison between the active order policy and deterministic 3.0."""
from __future__ import annotations

from datetime import date, timedelta

try:
    from .auto_order_planning import commercial_day
    from .forecasting.demand_models import NativeSalesAccumulator, native_daily_series
    from .forecasting.model_selection import choose_forecast
    from .replenishment.planning import plan_inventory
except ImportError:  # direct ASGI module import from backend/
    from auto_order_planning import commercial_day
    from forecasting.demand_models import NativeSalesAccumulator, native_daily_series
    from forecasting.model_selection import choose_forecast
    from replenishment.planning import plan_inventory


def compare_policies(active: dict, native_sales: list[dict]) -> dict:
    today = commercial_day(active.get("planning_day")) or date.today()
    first = today - timedelta(days=89)
    if isinstance(native_sales, NativeSalesAccumulator):
        # Cursor-backed input has already been reconciled incrementally.
        first_observed = native_sales.first_observed
        available_days = min(90, (today - first_observed).days + 1) if first_observed else 0
        series, evidence = native_sales.finish()
        # Match the historical baseline semantics: do not pad absent early days
        # when the native event history begins less than 90 days ago.
        for channels in series.values():
            channels["NEGOZIO"] = channels["NEGOZIO"][-max(1, available_days):]
            channels["VENDING"] = channels["VENDING"][-max(1, available_days):]
    else:
        observed = [commercial_day(row.get("data")) for row in native_sales]
        observed = [day for day in observed if day and first <= day <= today]
        available_days = min(90, (today - min(observed)).days + 1) if observed else 0
        series, evidence = native_daily_series(native_sales, today, max(1, available_days))
    params = active.get("parametri") or {}
    short = int(active.get("finestra_breve_gg") or 10)
    long = int(active.get("finestra_lunga_gg") or 30)
    weight = float(active.get("peso_breve", .7))
    lead = max(0, int(params.get("AUTO_ORDER_LEAD_TIME_GG") or 0))
    configured_review = max(0, int(params.get("AUTO_ORDER_REVIEW_PERIOD_GG") or 0))
    review = configured_review if lead else max(1, int(params.get("GIORNI_COPERTURA_TARGET") or 14))
    safety_factor = max(1, float(active.get("fattore_sicurezza") or 1.15))
    rows = []
    for old in [*(active.get("righe") or []), *(active.get("esclusi") or [])]:
        code = old["codice"]
        channels = series.get(code, {"NEGOZIO": [0] * max(1, available_days),
                                     "VENDING": [0] * max(1, available_days)})
        created = commercial_day(old.get("data_creazione_prodotto"))
        created_recently = bool(created and first <= created <= today and (today - created).days < 30)
        shop_history = channels["NEGOZIO"]
        vending_history = channels["VENDING"]
        shop = choose_forecast(shop_history, short=short, long=long, weight=weight,
                               created_recently=created_recently)
        vending = choose_forecast(vending_history, short=short, long=long, weight=weight,
                                  created_recently=created_recently)
        info = evidence.get(code, {"issues": [], "sale_days": 0, "sources": []})
        issues = sorted(set(info["issues"] + (old.get("anomalie_riconciliazione") or [])
                            + (old.get("anomalie_forniture") or [])))
        if old.get("anomalia"):
            issues.append("ANOMALIA_MAGAZZINO_O_FORNITURA")
        if old.get("quantita_senza_data"):
            issues.append("FORNITURA_SENZA_DATA_AFFIDABILE")
        stock_shop = int(old.get("giacenza_negozio") or 0)
        stock_vending = int(old.get("giacenza_vending") or 0)
        if stock_shop < 0 or stock_vending < 0:
            issues.append("GIACENZA_NEGATIVA")
            stock_shop, stock_vending = max(0, stock_shop), max(0, stock_vending)
        try:
            raw_lot = old.get("lotto_ordine") or 1
            lot = int(raw_lot)
            if lot <= 0 or float(raw_lot) != lot:
                raise ValueError
        except (TypeError, ValueError):
            lot = 1
            issues.append("LOTTO_NON_VALIDO")
        vending_capacity = old.get("vending_capacity")
        if vending_capacity is not None:
            try:
                capacity = int(vending_capacity)
                if capacity < stock_vending or capacity < 0 or float(vending_capacity) != capacity:
                    raise ValueError("inconsistent vending capacity")
                vending_capacity = capacity
            except (ValueError, TypeError):
                issues.append("CAPACITA_VENDING_INCOERENTE")
                vending_capacity = None
        plan = plan_inventory(
            today=today, shop_stock=stock_shop, vending_stock=stock_vending,
            demand_shop=shop["daily"], demand_vending=vending["daily"],
            receipts=old.get("ricezioni_previste") or [],
            minimum_shop=max(0, int(old.get("scorta_minima_negozio_auto_order") or 0)),
            lead_days=lead, review_days=review, lot=lot,
            safety_factor=safety_factor, vending_capacity=vending_capacity,
        )
        quality = "DATI_AMBIGUI" if issues else (
            "NESSUNA DOMANDA OSSERVATA" if sum(shop_history) + sum(vending_history) == 0 else
            "STORICO INSUFFICIENTE" if available_days < 60 or
            shop["quality"] == "STORICO INSUFFICIENTE" or
            vending["quality"] == "STORICO INSUFFICIENTE" else "OSSERVABILE")
        model = "PREVISIONE ADATTIVA" if "PREVISIONE ADATTIVA" in (shop["quality"], vending["quality"]) else "BASELINE 10/30"
        if sum(shop_history) + sum(vending_history) == 0:
            model = "CONTROLLO MANUALE" if old.get("scorta_minima_negozio_auto_order") else "STORICO INSUFFICIENTE"
        suggested = 0 if issues else plan["order_quantity"]
        def simulate_proposal(proposed_quantity: int):
            if lead <= 0:
                return None
            simulated_receipts = [*(old.get("ricezioni_previste") or [])]
            if proposed_quantity > 0:
                simulated_receipts.append({"data": (today + timedelta(days=lead)).isoformat(),
                                           "quantita": proposed_quantity})
            projected = plan_inventory(
                today=today, shop_stock=stock_shop, vending_stock=stock_vending,
                demand_shop=shop["daily"], demand_vending=vending["daily"],
                receipts=simulated_receipts,
                minimum_shop=max(0, int(old.get("scorta_minima_negozio_auto_order") or 0)),
                lead_days=lead, review_days=review,
                lot=lot, safety_factor=safety_factor,
                vending_capacity=vending_capacity,
            )
            return {"stockout_date": projected["predicted_stockout_date"],
                    "ending_stock": projected["expected_inventory_at_horizon"]}
        simulated_2 = simulate_proposal(int(old.get("qta_da_ordinare") or 0))
        simulated_3 = simulate_proposal(suggested)
        reasons = [f"Stock fisico {stock_shop + stock_vending}; arrivi entro {plan['protection_period']} gg "
                   f"{plan['relevant_receipts']}; obiettivo {plan['order_up_to_level']}; "
                   f"fabbisogno netto {plan['net_requirement']}; lotto {lot}."]
        if plan["planned_vending_transfer"]:
            reasons.append(f"Ricariche interne vending pianificate: {plan['planned_vending_transfer']} pezzi dal negozio in più giorni; non sono movimenti eseguiti.")
        if not plan["vending_capacity_known"] and vending["daily"] > 0:
            reasons.append("Capacità vending non verificata: la simulazione dei trasferimenti è indicativa.")
        if plan["intraday_demand_not_modelled"]:
            reasons.append("Giacenza live: il consumo giornaliero completo è proiettato da domani; domanda residua odierna non stimata.")
        if plan["stockout_before_supplier_delivery"]:
            reasons.append("Rischio prima del lead time: anticipare la consegna o intervenire manualmente.")
        if issues:
            reasons.append("Proposta sospesa: " + ", ".join(sorted(set(issues))) + ".")
        unit_cost = old.get("costo_unitario_acquisto")
        rows.append({
            "codice": code, "descrizione": old.get("descrizione"), "categoria": old.get("categoria"),
            "venduto_breve": old.get("venduto_breve"), "venduto_lungo": old.get("venduto_lungo"),
            "domanda_prevista_gg": round(shop["daily"] + vending["daily"], 4),
            "domanda_negozio_gg": round(shop["daily"], 4),
            "domanda_vending_gg": round(vending["daily"], 4),
            "metodo_previsione": model, "metodo_negozio": shop["model"],
            "metodo_vending": vending["model"], "qualita_previsione": quality,
            "classe_rotazione": shop["class"] if sum(shop_history) >= sum(vending_history) else vending["class"],
            "giorni_storico_osservabile": available_days,
            "giorni_con_vendite": info["sale_days"], "fonti": info["sources"],
            "anomalie": sorted(set(issues)), "backtest_negozio": shop["backtest"],
            "backtest_vending": vending["backtest"],
            "giacenza_negozio": stock_shop, "giacenza_vending": stock_vending,
            "giacenza_totale": stock_shop + stock_vending,
            "ordini_pendenti": int(old.get("quantita_in_arrivo") or 0) + int(old.get("quantita_senza_data") or 0),
            "ricezioni_previste": old.get("ricezioni_previste") or [],
            "copertura_negozio": round(stock_shop / shop["daily"], 2) if shop["daily"] else None,
            "copertura_vending": round(stock_vending / vending["daily"], 2) if vending["daily"] else None,
            "copertura_totale": round((stock_shop + stock_vending) / (shop["daily"] + vending["daily"]), 2) if shop["daily"] + vending["daily"] else None,
            "data_rischio_esaurimento": plan["predicted_stockout_date"],
            "data_prossima_consegna": plan["next_delivery_date"],
            "scorta_sicurezza": plan["safety_stock"], "punto_riordino": plan["reorder_point"],
            "scorta_obiettivo": plan["order_up_to_level"], "fabbisogno_netto": plan["net_requirement"],
            "lotto_ordine": lot,
            "quantita_suggerita": suggested, "quantita_2": old.get("qta_da_ordinare") or 0,
            "simulazione_2": simulated_2, "simulazione_3": simulated_3,
            "costo_acquisto_stimato": round(float(unit_cost) * suggested, 2) if unit_cost is not None else None,
            "valore_listino": round(float(old.get("prezzo") or 0) * suggested, 2),
            "stato": "CONTROLLO MANUALE" if issues else "ORDINA" if suggested else "MONITORA",
            "motivazione": " ".join(reasons), **plan,
        })
    errors_by_class = {}
    for row in rows:
        metrics = row["backtest_negozio"].get(row["metodo_negozio"])
        if metrics:
            bucket = errors_by_class.setdefault(row["classe_rotazione"], {"products": 0, "mae_sum": 0.0})
            bucket["products"] += 1
            bucket["mae_sum"] += metrics["mae"]
    errors_by_class = {key: {"products": value["products"],
                             "mean_shop_mae": round(value["mae_sum"] / value["products"], 4)}
                       for key, value in sorted(errors_by_class.items())}
    return {
        "mode": "CONFRONTO_SENZA_CONFERMA", "active_policy": "AUTO_ORDER_2",
        "candidate_policy": "AUTO_ORDER_3", "planning_day": today.isoformat(),
        "native_history_days": available_days,
        "source_watermarks": active.get("source_watermarks"),
        "rows": rows,
        "summary": {
            "products": len(rows),
            "stockout_risk": sum(bool(row["data_rischio_esaurimento"]) for row in rows),
            "supplementary_orders": sum(bool(row["quantita_suggerita"] and row["ordini_pendenti"]) for row in rows),
            "already_on_order": sum(bool(row["ordini_pendenti"]) for row in rows),
            "pieces_2": sum(row["quantita_2"] for row in rows),
            "pieces_3": sum(row["quantita_suggerita"] for row in rows),
            "insufficient_data": sum(row["qualita_previsione"] != "OSSERVABILE" for row in rows),
            "vending_refills": sum(bool(row["planned_vending_transfer"]) for row in rows),
            "known_purchase_cost": round(sum(row["costo_acquisto_stimato"] or 0 for row in rows), 2),
            "missing_purchase_costs": sum(bool(row["quantita_suggerita"] and row["costo_acquisto_stimato"] is None) for row in rows),
            "simulated_stockouts_2": sum(bool(row["simulazione_2"] and row["simulazione_2"]["stockout_date"]) for row in rows) if lead else None,
            "simulated_stockouts_3": sum(bool(row["simulazione_3"] and row["simulazione_3"]["stockout_date"]) for row in rows) if lead else None,
            "mean_positive_ending_stock_2": round(sum(max(0, row["simulazione_2"]["ending_stock"]) for row in rows) / len(rows), 2) if lead and rows else None,
            "mean_positive_ending_stock_3": round(sum(max(0, row["simulazione_3"]["ending_stock"]) for row in rows) / len(rows), 2) if lead and rows else None,
            "forecast_errors_by_class": errors_by_class,
        },
    }

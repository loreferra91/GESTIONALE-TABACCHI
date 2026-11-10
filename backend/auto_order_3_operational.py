"""Guarded Auto-Order 3.1 operational bridge.

Pure validation helpers. Never accept client-computed quantities/forecast as
authoritative; always rebuild the 3.1 comparison against live MongoDB.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from fastapi import HTTPException


POLICY_2 = "AUTO_ORDER_2"
POLICY_31 = "AUTO_ORDER_3_1"


def readiness(ao: dict, comparison: dict) -> dict:
    params = ao.get("parametri") or {}
    reasons = []
    if int(params.get("AUTO_ORDER_3_1_ENABLED") or 0) != 1:
        reasons.append("Attivare esplicitamente AUTO_ORDER_3_1_ENABLED=1 nei Parametri.")
    if int(params.get("AUTO_ORDER_LEAD_TIME_GG") or 0) <= 0:
        reasons.append("Configurare il lead time reale del fornitore (giorni > 0).")
    if int(params.get("AUTO_ORDER_REVIEW_PERIOD_GG") or 0) <= 0:
        reasons.append("Configurare il periodo reale tra revisioni ordine (giorni > 0).")
    if ao.get("data_quality") != "DATI_AFFIDABILI":
        reasons.append("Verificare la qualità delle vendite e delle forniture operative.")
    if int(comparison.get("native_history_days") or 0) < 60:
        reasons.append("Occorrono almeno 60 giorni di storico nativo osservabile.")
    eligible = sum(
        1 for row in comparison.get("rows") or []
        if int(row.get("quantita_suggerita") or 0) > 0
        and row.get("qualita_previsione") == "OSSERVABILE"
        and not row.get("anomalie")
    )
    return {
        "operational_ready": not reasons,
        "operational_blockers": reasons,
        "eligible_products": eligible,
        "active_policy": POLICY_31 if not reasons else POLICY_2,
    }


def policy_snapshot(ao: dict, comparison: dict) -> str:
    """Hash the fresh 2.0 inventory snapshot and forecast-affecting 3.1 data."""
    params = ao.get("parametri") or {}
    rows = sorted(({
        "codice": row.get("codice"),
        "quantita_suggerita": row.get("quantita_suggerita"),
        "qualita_previsione": row.get("qualita_previsione"),
        "anomalie": row.get("anomalie"),
        "domanda_prevista_gg": row.get("domanda_prevista_gg"),
        "giacenza_negozio": row.get("giacenza_negozio"),
        "giacenza_vending": row.get("giacenza_vending"),
        "ordini_pendenti": row.get("ordini_pendenti"),
        "ricezioni_previste": row.get("ricezioni_previste"),
        "vending_transfer_schedule": row.get("vending_transfer_schedule"),
        "vending_capacity_known": row.get("vending_capacity_known"),
        "lotto_ordine": row.get("lotto_ordine"),
        "metodo_negozio": row.get("metodo_negozio"),
        "metodo_vending": row.get("metodo_vending"),
    } for row in comparison.get("rows") or []), key=lambda row: str(row["codice"]))
    payload = {
        "policy": POLICY_31,
        "active_snapshot": ao["snapshot_key"],
        "enabled": int(params.get("AUTO_ORDER_3_1_ENABLED") or 0),
        "lead": int(params.get("AUTO_ORDER_LEAD_TIME_GG") or 0),
        "review": int(params.get("AUTO_ORDER_REVIEW_PERIOD_GG") or 0),
        "quality": ao.get("data_quality"),
        "history": comparison.get("native_history_days"),
        "rows": rows,
    }
    return "auto-order-3-1:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def select_operational_rows(
    ao: dict,
    comparison: dict,
    requested: list,
    multiply_money: Callable[[Any, Any], float],
) -> list[dict]:
    """Derive persisted order lines from server data, with strict positive upper cap."""
    if not requested:
        raise HTTPException(422, "Selezionare almeno un articolo per l'ordine 3.1.")
    baseline = {row["codice"]: row for row in [*(ao.get("righe") or []), *(ao.get("esclusi") or [])]}
    proposals = {row["codice"]: row for row in comparison.get("rows") or []}
    seen = set()
    result = []
    for item in requested:
        code = str(item.codice).strip()
        if code in seen:
            raise HTTPException(422, f"Articolo duplicato nella selezione: {code}")
        seen.add(code)
        old = baseline.get(code)
        candidate = proposals.get(code)
        if not old or not candidate:
            raise HTTPException(422, f"Articolo non riconosciuto nella proposta 3.1: {code}")
        if (old.get("anomalia") or old.get("anomalie_riconciliazione")
            or old.get("anomalie_forniture") or int(old.get("quantita_senza_data") or 0)):
            raise HTTPException(409, f"Anomalie operative da risolvere per {code}")
        if candidate.get("anomalie") or candidate.get("qualita_previsione") != "OSSERVABILE":
            raise HTTPException(409, f"Previsione 3.1 non certificabile per {code}")
        maximum = int(candidate.get("quantita_suggerita") or 0)
        lot = int(old.get("lotto_ordine") or 1)
        quantity = int(item.quantita)
        if not (maximum > 0 and lot > 0 and 0 < quantity <= maximum and quantity % lot == 0):
            raise HTTPException(422, f"Quantità 3.1 per {code} non valida (lotto {lot}, massimo {maximum})")
        result.append({
            **old,
            "stato": "ORDINA ORA",
            "qta_da_ordinare": quantity,
            "totale": multiply_money(old.get("prezzo") or 0, quantity),
            "totale_costo_stimato": (
                multiply_money(old.get("costo_unitario_acquisto"), quantity)
                if old.get("costo_unitario_acquisto") is not None else None
            ),
            "motivo": candidate.get("motivazione") or "Riordino Auto-Order 3.1",
            "policy_version": POLICY_31,
            "quantita_3_1_massima": maximum,
            "modello_negozio": candidate.get("metodo_negozio"),
            "modello_vending": candidate.get("metodo_vending"),
        })
    return result

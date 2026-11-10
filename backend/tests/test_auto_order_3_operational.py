"""3.1 live order policy: authorization, snapshot, bounds and native integration.

All MongoDB operations use mongomock-motor; never open production connections.
"""
import asyncio
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

from backend import server
from backend.auto_order_3_operational import (
    POLICY_2, POLICY_31, policy_snapshot, readiness, select_operational_rows,
)
from backend.auto_order_router import AutoOrderConfermaIn, AutoOrderRigaIn


def fixture_ao(**changes):
    base = {
        "snapshot_key": "auto-order:old",
        "data_quality": "DATI_AFFIDABILI",
        "parametri": {
            "AUTO_ORDER_3_1_ENABLED": 1,
            "AUTO_ORDER_LEAD_TIME_GG": 2,
            "AUTO_ORDER_REVIEW_PERIOD_GG": 12,
        },
        "righe": [],
        "esclusi": [{
            "codice": "P", "descrizione": "P", "prezzo": 5,
            "stato": "MONITORA", "giacenza_negozio": 2,
            "lotto_ordine": 10, "quantita_in_arrivo": 20,
            "quantita_senza_data": 0, "auto_order_version": 0,
        }],
    }
    base.update(changes)
    return base


def fixture_comparison(**changes):
    result = {
        "native_history_days": 90,
        "rows": [{
            "codice": "P", "quantita_suggerita": 30,
            "qualita_previsione": "OSSERVABILE", "anomalie": [],
            "domanda_prevista_gg": 3, "lotto_ordine": 10,
            "motivazione": "Fabbisogno netto 30",
            "metodo_negozio": "BASELINE 10/30", "metodo_vending": "NESSUNA DOMANDA",
        }],
    }
    result.update(changes)
    return result


def test_readiness_needs_explicit_opt_in_real_supplier_schedule_and_native_history():
    ao = fixture_ao()
    comparison = fixture_comparison()
    assert readiness(ao, comparison)["operational_ready"] is True
    assert readiness(ao, comparison)["eligible_products"] == 1
    no_opt_in = fixture_ao(parametri={**ao["parametri"], "AUTO_ORDER_3_1_ENABLED": 0})
    assert readiness(no_opt_in, comparison)["operational_ready"] is False
    no_lead = fixture_ao(parametri={**ao["parametri"], "AUTO_ORDER_LEAD_TIME_GG": 0})
    assert readiness(no_lead, comparison)["operational_ready"] is False
    no_review = fixture_ao(parametri={**ao["parametri"], "AUTO_ORDER_REVIEW_PERIOD_GG": 0})
    assert readiness(no_review, comparison)["operational_ready"] is False
    assert readiness(ao, fixture_comparison(native_history_days=30))["operational_ready"] is False
    assert readiness(fixture_ao(data_quality="DATI_PARZIALI"), comparison)["operational_ready"] is False


def test_31_selector_allows_verified_supplementary_order_not_unbounded_override():
    ao, comparison = fixture_ao(), fixture_comparison()
    rows = select_operational_rows(
        ao, comparison, [AutoOrderRigaIn(codice="P", quantita=20)],
        lambda price, q: price * q,
    )
    assert rows[0]["qta_da_ordinare"] == 20
    assert rows[0]["quantita_3_1_massima"] == 30
    assert rows[0]["policy_version"] == POLICY_31
    for quantity in (31, 40, 7):
        with pytest.raises(HTTPException) as err:
            select_operational_rows(ao, comparison, [AutoOrderRigaIn(codice="P", quantita=quantity)], lambda p, q: p*q)
        assert err.value.status_code == 422


def test_31_selector_rejects_anomalies_low_quality_and_duplicate_products():
    ao, forecast = fixture_ao(), fixture_comparison()
    with pytest.raises(HTTPException) as err:
        select_operational_rows(ao, forecast, [AutoOrderRigaIn(codice="P", quantita=10)] * 2, lambda p, q: p*q)
    assert err.value.status_code == 422
    forecast["rows"][0]["anomalie"] = ["CSV_SENZA_EVENT_KEY"]
    with pytest.raises(HTTPException) as err:
        select_operational_rows(ao, forecast, [AutoOrderRigaIn(codice="P", quantita=10)], lambda p, q: p*q)
    assert err.value.status_code == 409
    forecast["rows"][0]["anomalie"] = []
    forecast["rows"][0]["qualita_previsione"] = "STORICO INSUFFICIENTE"
    with pytest.raises(HTTPException):
        select_operational_rows(ao, forecast, [AutoOrderRigaIn(codice="P", quantita=10)], lambda p, q: p*q)
    ao["esclusi"][0]["quantita_senza_data"] = 20
    forecast["rows"][0]["qualita_previsione"] = "OSSERVABILE"
    with pytest.raises(HTTPException):
        select_operational_rows(ao, forecast, [AutoOrderRigaIn(codice="P", quantita=10)], lambda p, q: p*q)


def test_31_snapshot_changes_with_stock_forecast_policy_and_pending_supply():
    ao, result = fixture_ao(), fixture_comparison()
    initial = policy_snapshot(ao, result)
    assert policy_snapshot(ao, result) == initial
    assert policy_snapshot(fixture_ao(snapshot_key="other"), result) != initial
    assert policy_snapshot(ao, fixture_comparison(rows=[{**result["rows"][0], "quantita_suggerita": 40}])) != initial
    assert policy_snapshot(ao, fixture_comparison(rows=[{**result["rows"][0], "vending_transfer_schedule": [{"data": "2026-11-12", "quantita": 3}]}])) != initial
    changed = fixture_ao(parametri={**ao["parametri"], "AUTO_ORDER_3_1_ENABLED": 0})
    assert policy_snapshot(changed, result) != initial


@pytest.fixture
def isolated_database(monkeypatch):
    database = AsyncMongoMockClient()["auto_order_live_test"]
    monkeypatch.setattr(server, "db", database)
    return database


def test_31_end_to_end_shadow_to_confirm_replay_and_server_revalidation(isolated_database):
    db = isolated_database
    today = datetime.now(ZoneInfo("Europe/Rome")).date()

    async def exercise():
        await db.ordini_fornitore.create_index("batch_key", unique=True)
        await db.prodotti.insert_one({
            "id": "test-product", "codice": "P", "descrizione": "P",
            "categoria": "SIGARETTE", "prezzo": 5,
            "giacenza_negozio": 0, "scorta_minima_negozio_auto_order": 0,
        })
        await db.parametri.insert_many([
            {"nome": "AUTO_ORDER_3_1_ENABLED", "valore": 1},
            {"nome": "AUTO_ORDER_LEAD_TIME_GG", "valore": 2},
            {"nome": "AUTO_ORDER_REVIEW_PERIOD_GG", "valore": 12},
        ])
        await db.vendite.insert_many([
            {"id": f"native-{n}", "codice": "P",
             "data": (today - timedelta(days=n)).isoformat() + "T12:00:00",
             "quantita": 2, "canale": "NEGOZIO", "sorgente": "MANUALE"}
            for n in range(90)
        ])
        ao = await server.auto_order()
        comp = await server.auto_order_module["build_comparison"](ao)
        assert comp["operational_ready"] is True, comp["operational_blockers"]
        row = next(r for r in comp["rows"] if r["codice"] == "P")
        assert row["quantita_suggerita"] > 0
        amount = int(row["quantita_suggerita"])
        assert amount % 10 == 0

        request = AutoOrderConfermaIn(
            policy_version=POLICY_31,
            snapshot_key=ao["snapshot_key"],
            policy_snapshot_key=comp["policy_snapshot_key"],
            righe=[AutoOrderRigaIn(codice="P", quantita=amount)],
            idempotency_key="operational-31-idempotent",
            data_consegna_prevista=today + timedelta(days=3),
        )
        too_many = request.model_copy(update={"idempotency_key": "too-many",
            "righe": [AutoOrderRigaIn(codice="P", quantita=amount + 10)]})
        with pytest.raises(HTTPException) as excess:
            await server.conferma_auto_order(too_many)
        assert excess.value.status_code == 422

        stale = request.model_copy(update={"idempotency_key": "stale-31",
                                           "policy_snapshot_key": "not-the-forecast"})
        with pytest.raises(HTTPException) as bad_snapshot:
            await server.conferma_auto_order(stale)
        assert bad_snapshot.value.status_code == 409

        result = await server.conferma_auto_order(request)
        repeat = await server.conferma_auto_order(request)
        line = await db.ordini_fornitore_righe.find_one({"batch_key": result["batch_key"]}, {"_id": 0})
        product = await db.prodotti.find_one({"codice": "P"}, {"_id": 0})
        return result, repeat, line, product

    result, repeat, line, product = asyncio.run(exercise())
    assert result["status"] == "COMPLETED" and result["policy_version"] == POLICY_31
    assert repeat["duplicate"] is True
    assert line["policy_version"] == POLICY_31
    assert line["quantita"] == line["quantita_3_1_massima"]
    assert product["giacenza_negozio"] == 0
    assert product["auto_order_version"] == 1

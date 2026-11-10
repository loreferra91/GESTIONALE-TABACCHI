"""Shadow planner tests: no test connects to an operational MongoDB."""
from datetime import date, timedelta

import pytest

from backend.auto_order_3_service import compare_policies
from backend.forecasting.demand_models import native_daily_series
from backend.forecasting.model_selection import choose_forecast, rolling_backtest, rotation_class
from backend.replenishment.planning import plan_inventory


TODAY = date(2026, 11, 10)


def plan(**changes):
    values = dict(today=TODAY, shop_stock=10, vending_stock=0,
                  demand_shop=0, demand_vending=0,
                  receipts=[], minimum_shop=60, lead_days=2, review_days=12,
                  lot=10, safety_factor=1.15)
    values.update(changes)
    return plan_inventory(**values)


def test_integrative_order_and_prevention_of_duplicate():
    receipt = [{"data": "2026-11-12", "quantita": 20}]
    result = plan(receipts=receipt)
    assert result["inventory_position"] == 30
    assert result["net_requirement"] == result["order_quantity"] == 30
    sufficient = plan(receipts=[{"data": "2026-11-12", "quantita": 50}])
    assert sufficient["order_quantity"] == 0


def test_late_delivery_does_not_count_as_today_stock_or_prevent_early_warning():
    result = plan(shop_stock=10, minimum_shop=0, demand_shop=4,
                  receipts=[{"data": "2026-11-16", "quantita": 20}],
                  lead_days=5, review_days=7)
    assert result["predicted_stockout_date"] == "2026-11-12"
    assert result["stockout_before_supplier_delivery"] is True
    assert result["next_delivery_date"] == "2026-11-16"
    assert result["order_quantity"] > 0


def test_vending_refill_is_internal_and_cannot_be_double_allocated():
    refill = plan(shop_stock=100, vending_stock=0, minimum_shop=0,
                  demand_vending=2, lead_days=2, review_days=12)
    assert refill["planned_vending_transfer"] == 28
    assert refill["order_quantity"] == 0
    assert refill["inventory_position"] == 100
    bank_empty = plan(shop_stock=0, vending_stock=100, minimum_shop=0,
                      demand_shop=2, lead_days=2, review_days=12)
    assert bank_empty["planned_vending_transfer"] == 0
    assert bank_empty["order_quantity"] >= 30


def test_lot_safety_and_legacy_lead_zero():
    result = plan(shop_stock=0, minimum_shop=0, demand_shop=1,
                  lead_days=0, review_days=14, lot=7)
    assert result["safety_stock"] == 3
    assert result["order_up_to_level"] == 17
    assert result["order_quantity"] == 21
    assert result["supplier_delivery_date"] is None
    assert result["stockout_before_supplier_delivery"] is None
    with pytest.raises(ValueError):
        plan(lot=0)
    with pytest.raises(ValueError):
        plan(lot=2.5)


def test_native_csv_duplicate_and_rome_dates():
    documents = [
        {"codice": "P", "data": "2026-10-25T00:30:00+02:00", "canale": "VENDING",
         "sorgente": "CSV_VENDING", "csv_event_key": "event-1", "quantita": 2},
        {"codice": "P", "data": "2026-10-24T22:30:00Z", "canale": "VENDING",
         "sorgente": "CSV_VENDING", "csv_event_key": "event-1", "quantita": 2},
        {"codice": "P", "data": "25/10/2026", "canale": "NEGOZIO",
         "sorgente": "MANUALE", "quantita": 3},
    ]
    series, evidence = native_daily_series(documents, TODAY, 30)
    assert sum(series["P"]["VENDING"]) == 2
    assert sum(series["P"]["NEGOZIO"]) == 3
    assert evidence["P"]["issues"] == ["CSV_EVENT_KEY_DUPLICATO"]


def test_native_sale_id_duplicate_is_not_counted_twice_and_csv_without_key_is_ambiguous():
    documents = [
        {"id": "sale-1", "codice": "P", "data": "2026-11-10", "canale": "NEGOZIO",
         "quantita": 3},
        {"id": "sale-1", "codice": "P", "data": "2026-11-10", "canale": "NEGOZIO",
         "quantita": 3},
        {"codice": "Q", "data": "2026-11-10", "canale": "VENDING",
         "sorgente": "CSV_VENDING", "quantita": 2},
    ]
    series, evidence = native_daily_series(documents, TODAY, 30)
    assert sum(series["P"]["NEGOZIO"]) == 3
    assert "ID_VENDITA_DUPLICATO" in evidence["P"]["issues"]
    assert "CSV_SENZA_EVENT_KEY" in evidence["Q"]["issues"]


def test_rotation_classes_and_no_invented_new_product_demand():
    assert rotation_class([0] * 30, created_recently=True) == "NUOVO PRODOTTO"
    assert rotation_class([0] * 30) == "SENZA DOMANDA OSSERVATA"
    assert rotation_class([1] + [0] * 29) == "SLOW MOVER"
    assert rotation_class([2, 2, 2] + [0] * 27) == "DOMANDA INTERMITTENTE"
    assert rotation_class([1] * 5 + [0] * 25) == "NORMAL MOVER"
    assert rotation_class([1] * 10 + [0] * 20) == "FAST MOVER"
    forecast = choose_forecast([0] * 90, created_recently=True)
    assert forecast["daily"] == 0
    assert forecast["model"] == "STORICO INSUFFICIENTE"


def test_backtest_uses_only_past_and_avoids_zero_denominator(monkeypatch):
    history = [0] * 60 + [1] * 30
    scores = rolling_backtest(history)
    assert scores["BASELINE 10/30"]["origins"] == 28
    assert scores["BASELINE 10/30"]["wape"] is not None
    zero = rolling_backtest([0] * 90)
    assert all(score["wape"] is None for score in zero.values())
    seen = []
    monkeypatch.setattr("backend.forecasting.model_selection.MODELS",
                        {"PROBE": lambda prefix: (seen.append(tuple(prefix)) or 0)})
    rolling_backtest(history, origins=28)
    assert seen == [tuple(history[:index]) for index in range(62, 90)]


def test_comparison_is_read_only_and_legacy_history_is_not_an_input():
    active = {"planning_day": TODAY.isoformat(), "finestra_breve_gg": 10,
              "finestra_lunga_gg": 30, "peso_breve": .7, "fattore_sicurezza": 1.15,
              "parametri": {"GIORNI_COPERTURA_TARGET": 14},
              "righe": [], "esclusi": [{"codice": "P", "descrizione": "P", "categoria": "ACCESSORI",
                  "giacenza_negozio": 0, "giacenza_vending": 0,
                  "scorta_minima_negozio_auto_order": 0, "lotto_ordine": 1,
                  "prezzo": 3, "qta_da_ordinare": 0}]}
    native = [{"codice": "P", "data": (TODAY - timedelta(days=day)).isoformat(),
               "canale": "NEGOZIO", "sorgente": "MANUALE", "quantita": 1}
              for day in range(30)]
    before = compare_policies(active, native)
    assert before["rows"][0]["quantita_suggerita"] > 0
    assert before["rows"][0]["domanda_prevista_gg"] > 0
    assert active["esclusi"][0]["qta_da_ordinare"] == 0
    assert compare_policies(active, native) == before


def test_policy_comparison_projects_each_quantity_on_the_same_inventory():
    active = {"planning_day": TODAY.isoformat(), "finestra_breve_gg": 10,
              "finestra_lunga_gg": 30, "peso_breve": .7, "fattore_sicurezza": 1.15,
              "parametri": {"AUTO_ORDER_LEAD_TIME_GG": 2,
                           "AUTO_ORDER_REVIEW_PERIOD_GG": 12},
              "righe": [], "esclusi": [{"codice": "P", "descrizione": "P",
                  "categoria": "ACCESSORI", "giacenza_negozio": 5,
                  "giacenza_vending": 0, "lotto_ordine": 1, "qta_da_ordinare": 0}]}
    native = [{"codice": "P", "data": (TODAY - timedelta(days=day)).isoformat(),
               "canale": "NEGOZIO", "quantita": 1}
              for day in range(90)]
    result = compare_policies(active, native)
    row = result["rows"][0]
    assert row["quantita_suggerita"] > 0
    assert row["simulazione_3"]["ending_stock"] > row["simulazione_2"]["ending_stock"]
    assert result["summary"]["forecast_errors_by_class"]["FAST MOVER"]["products"] == 1


def test_invalid_product_lot_suspends_only_that_product():
    active = {"planning_day": TODAY.isoformat(), "parametri": {}, "righe": [],
              "esclusi": [{"codice": "P", "descrizione": "P", "lotto_ordine": 2.5,
                           "giacenza_negozio": 0, "giacenza_vending": 0,
                           "scorta_minima_negozio_auto_order": 10}]}
    row = compare_policies(active, [])["rows"][0]
    assert row["quantita_suggerita"] == 0
    assert "LOTTO_NON_VALIDO" in row["anomalie"]

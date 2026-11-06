from backend.dashboard_router import calculate_purchase_breakdown
from backend.money import money, money_decimal


def test_splits_excel_base_from_later_receipts_without_double_counting():
    pivot = {
        "kpi": {"valore_acquistato": 153, "aggio_pct": 0.10},
        "righe": [
            {"codice": "A", "acq": 15, "prezzo": 10},
            {"codice": "B", "acq": 4, "prezzo": 5},
        ],
    }
    orders = [
        {"codice": "A", "quantita": 5, "prezzo": 12},
        {"codice": "C", "quantita": 2, "prezzo": 8},
        {"codice": "B", "quantita": 99, "prezzo": 5, "annullato": True},
    ]

    result = calculate_purchase_breakdown(
        pivot,
        orders,
        {"created_at": "2026-10-02T09:47:09+00:00", "file": "GODSERVICES38.xlsm"},
        money,
        money_decimal,
    )

    assert result == {
        "valore_acquistato_base_excel": 108.0,
        "valore_acquistato_carico_merce": 68.4,
        "valore_acquistato": 176.4,
        "acquistati_base_excel_pezzi": 14,
        "acquistati_carico_merce_pezzi": 7,
        "acquisti_excel_data": "2026-10-02T09:47:09+00:00",
        "acquisti_excel_file": "GODSERVICES38.xlsm",
        "acquisti_ricostruzione_anomalie": 0,
    }


def test_preserves_existing_total_when_there_is_no_excel_baseline():
    result = calculate_purchase_breakdown(
        {
            "kpi": {"valore_acquistato": 25.2, "aggio_pct": 0.10},
            "righe": [{"codice": "A", "acq": 4, "prezzo": 7}],
        },
        [{"codice": "A", "quantita": 2, "prezzo": 7}],
        None,
        money,
        money_decimal,
    )

    assert result["valore_acquistato"] == 25.2
    assert result["valore_acquistato_base_excel"] == 25.2
    assert result["valore_acquistato_carico_merce"] == 0

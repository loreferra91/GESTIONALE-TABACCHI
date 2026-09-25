"""Tests for POST /api/import/excel-full multi-sheet importer (iteration_7).

Covers:
- Full import returns totali/dettaglio with all 5 sheets found.
- Custom parameters (AGGIO_PCT) preserved across import.
- DB-only products (TEST_DB_ONLY) preserved (not deleted).
- Storico is full-replace: after import, GET /api/ordini count == storico_ricreato.
- Upload size cap (>20MB → 413).
- Invalid non-xlsx binary returns 422.
- Regressions: /api/dashboard KPIs still coherent (aggio_pct=0.10).
"""
import io
import os
import pytest
import requests

BASE = os.environ.get("TEST_BACKEND_URL", "").rstrip("/")
if not BASE:
    pytest.skip("requires TEST_BACKEND_URL", allow_module_level=True)

FIXTURE = "/app/backend/tests/fixtures/gods27.xlsm"


@pytest.fixture(scope="module")
def s():
    return requests.Session()


@pytest.fixture(scope="module")
def prepare(s):
    """Before import: set AGGIO_PCT=0.10 and create TEST_DB_ONLY product."""
    # Ensure AGGIO_PCT
    s.put(f"{BASE}/api/parametri/AGGIO_PCT", json={"valore": 0.10})
    # Create DB-only product
    s.post(
        f"{BASE}/api/prodotti",
        json={
            "codice": "TEST_DB_ONLY",
            "descrizione": "DB-only preserve test",
            "categoria": "ACCESSORI",
            "acquistati": 0,
            "venduti_negozio": 0,
            "giacenza_negozio": 5,
            "prezzo": 1.0,
            "giacenza_vending": 0,
            "venduti_vending": 0,
        },
    )
    yield
    # Restore AGGIO_PCT at end of module
    s.put(f"{BASE}/api/parametri/AGGIO_PCT", json={"valore": 0.10})


def test_import_excel_full_happy_path(s, prepare):
    assert os.path.exists(FIXTURE), f"fixture missing: {FIXTURE}"
    with open(FIXTURE, "rb") as fh:
        files = {"file": ("gods27.xlsm", fh, "application/vnd.ms-excel.sheet.macroEnabled.12")}
        r = s.post(f"{BASE}/api/import/excel-full", files=files, timeout=180)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("ok") is True
    expected_sheets = {"RIEP_VENDITA", "LISTINO ADM", "RICARICA VENDING", "STORICO_ORDINI", "PARAMETRI"}
    assert set(j["fogli_trovati"]) == expected_sheets, f"got {j['fogli_trovati']}"
    assert j["fogli_mancanti"] == []
    t = j["totali"]
    # store storico count for later test
    pytest.storico_ricreato = t["storico_ricreato"]
    # Plausible counts
    assert (t["prodotti_inseriti"] + t["prodotti_aggiornati"]) > 0
    assert (t["listino_inseriti"] + t["listino_aggiornati"]) > 0
    assert (t["vending_inseriti"] + t["vending_aggiornati"]) > 0
    assert t["storico_ricreato"] > 0
    assert t["parametri_aggiornati"] > 0


def test_custom_parametri_preserved(s):
    r = s.get(f"{BASE}/api/parametri")
    assert r.status_code == 200
    params = {p["nome"]: p["valore"] for p in r.json()}
    assert "AGGIO_PCT" in params, "AGGIO_PCT was deleted by import"
    assert abs(float(params["AGGIO_PCT"]) - 0.10) < 1e-9, f"AGGIO_PCT overwritten: {params['AGGIO_PCT']}"


def test_db_only_product_preserved(s):
    r = s.get(f"{BASE}/api/prodotti", params={"q": "TEST_DB_ONLY"})
    assert r.status_code == 200
    matches = [p for p in r.json() if p["codice"] == "TEST_DB_ONLY"]
    assert matches, "TEST_DB_ONLY product was deleted by import"


def test_storico_ordini_count_matches(s):
    expected = getattr(pytest, "storico_ricreato", None)
    if expected is None:
        pytest.skip("previous import test did not run")
    r = s.get(f"{BASE}/api/ordini", params={"limit": 100000})
    assert r.status_code == 200
    data = r.json()
    rows = data if isinstance(data, list) else data.get("items", [])
    # Allow small delta if endpoint caps limit
    assert len(rows) > 0
    # If count exactly matches or is capped at MAX_LIMIT (5000)
    assert len(rows) == expected or len(rows) == 5000, f"expected {expected} storico rows, got {len(rows)}"


def test_upload_size_cap_413(s):
    big = b"x" * (21 * 1024 * 1024)
    files = {"file": ("big.xlsx", io.BytesIO(big), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = s.post(f"{BASE}/api/import/excel-full", files=files)
    assert r.status_code == 413, f"expected 413, got {r.status_code}: {r.text[:200]}"


def test_invalid_binary_returns_422(s):
    junk = b"This is not an excel file, just plain text bytes." * 10
    files = {"file": ("bad.xlsx", io.BytesIO(junk), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = s.post(f"{BASE}/api/import/excel-full", files=files)
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text[:200]}"
    assert "non leggibile" in r.text.lower() or "not readable" in r.text.lower()


def test_regression_dashboard_aggio(s):
    r = s.get(f"{BASE}/api/dashboard")
    assert r.status_code == 200
    kpi = r.json().get("kpi", {})
    assert kpi.get("valore_acquistato", 0) > 0
    assert "margine_lordo" in kpi
    assert abs(float(kpi.get("aggio_pct", 0)) - 0.10) < 1e-9


def test_regression_primary_endpoints_ok(s):
    for path in ["/api/prodotti?limit=5", "/api/listino?limit=5", "/api/vending", "/api/parametri", "/api/pivot"]:
        r = s.get(f"{BASE}{path}")
        assert r.status_code == 200, f"{path} -> {r.status_code}"

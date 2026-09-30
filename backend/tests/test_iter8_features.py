"""Iteration 8 tests: multi-sheet import (7 sheets), auto-order semantic change,
vending ricarica PDF, and regression checks.
"""
import os
import pytest
import requests
from pathlib import Path

BASE = os.environ.get("TEST_BACKEND_URL", "").rstrip("/")
if not BASE:
    pytest.skip("requires TEST_BACKEND_URL", allow_module_level=True)

pytestmark = pytest.mark.external

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gods34.xlsm"


@pytest.fixture(scope="module")
def s():
    ses = requests.Session()
    yield ses
    # Restore AGGIO_PCT
    try:
        ses.put(f"{BASE}/api/parametri/AGGIO_PCT", json={"valore": 0.10}, timeout=15)
    except Exception:
        pass


# ---------- Excel full import (7 sheets) ----------
def test_import_excel_full_7_sheets(s):
    assert FIXTURE.exists(), f"fixture missing: {FIXTURE}"
    with FIXTURE.open("rb") as fh:
        files = {"file": ("gods34.xlsm", fh,
                          "application/vnd.ms-excel.sheet.macroEnabled.12")}
        r = s.post(f"{BASE}/api/import/excel-full", files=files, timeout=240)
    assert r.status_code == 200, r.text[:500]
    j = r.json()
    assert j.get("ok") is True
    found = set(j["fogli_trovati"])
    assert len(found) == 7, f"expected 7 sheets, got {len(found)}: {found}"
    for must in ("DB_STORICO_VEND", "DB_STORICO_VENDING_EXT"):
        assert must in found, f"missing sheet {must} in {found}"
    t = j["totali"]
    assert "db_storico_vend_righe" in t
    assert "db_storico_vending_ext_righe" in t
    assert isinstance(t["db_storico_vend_righe"], int)
    assert isinstance(t["db_storico_vending_ext_righe"], int)


# ---------- Auto-order semantic change ----------
def _get_prod_id_by_codice(s, codice):
    r = s.get(f"{BASE}/api/prodotti", params={"q": codice}, timeout=30)
    if r.status_code != 200:
        return None
    for p in r.json():
        if p.get("codice") == codice:
            return p.get("id")
    return None


def test_auto_order_uses_negozio_only(s):
    codice = "TEST_AO_VEND"
    # Cleanup any pre-existing
    pid = _get_prod_id_by_codice(s, codice)
    if pid:
        s.delete(f"{BASE}/api/prodotti/{pid}")

    # Parte da 2 pezzi fisici liberi e 5 nella vending. La vendita recente
    # esaurisce solo il negozio: la scorta vending non deve coprire il fabbisogno.
    payload = {
        "codice": codice,
        "descrizione": "TEST",
        "categoria": "SIGARETTE",
        "prezzo": 5.0,
        "giacenza_negozio": 2,
        "giacenza_vending": 5,
        "acquistati": 10,
        "venduti_negozio": 0,
        "venduti_vending": 0,
    }
    r = s.post(f"{BASE}/api/prodotti", json=payload, timeout=30)
    assert r.status_code in (200, 201), r.text[:300]
    new_id = r.json().get("id")
    assert new_id

    vendita_id = None
    try:
        vendita = s.post(f"{BASE}/api/vendite", json={
            "data": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "codice": codice,
            "descrizione": "TEST",
            "quantita": 2,
            "importo": 10,
            "canale": "NEGOZIO",
            "pagamento": "CONTANTI",
        }, timeout=30)
        assert vendita.status_code in (200, 201), vendita.text[:300]
        vendita_id = vendita.json().get("id")

        # GET auto-order
        r = s.get(f"{BASE}/api/auto-order", timeout=60)
        assert r.status_code == 200, r.text[:300]
        ao = r.json()
        assert "righe" in ao
        row = next((x for x in ao["righe"] if x.get("codice") == codice), None)
        assert row is not None, f"{codice} not present in auto-order righe"
        assert row["qta_da_ordinare"] > 0, f"expected qta>0, got {row['qta_da_ordinare']}"
        assert "COPERTURA" in row["motivo"].upper(), row["motivo"]
        assert row["giacenza_negozio"] == 0
        assert row["giacenza_vending"] == 5
    finally:
        if vendita_id:
            s.delete(f"{BASE}/api/vendite/{vendita_id}")
        s.delete(f"{BASE}/api/prodotti/{new_id}")


# ---------- Vending ricarica PDF ----------
def test_vending_ricarica_pdf(s):
    r = s.get(f"{BASE}/api/vending/ricarica-pdf", timeout=60)
    assert r.status_code == 200, r.text[:300]
    ctype = r.headers.get("content-type", "")
    assert "application/pdf" in ctype, ctype
    body = r.content
    assert body.startswith(b"%PDF-"), body[:20]
    # Might or might not be > 2000 bytes depending on data; log info
    print(f"PDF size: {len(body)} bytes")


# ---------- Regression ----------
@pytest.mark.parametrize("path", [
    "/api/dashboard", "/api/pivot", "/api/prodotti?limit=5",
    "/api/listino?limit=5", "/api/vending", "/api/parametri",
])
def test_regression_gets(s, path):
    r = s.get(f"{BASE}{path}", timeout=60)
    assert r.status_code == 200, f"{path} -> {r.status_code}"

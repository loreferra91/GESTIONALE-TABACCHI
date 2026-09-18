"""Regression tests per code-review remediation iteration_3."""
import os
import io
import pytest
import requests

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if False else None
# Read from frontend/.env explicitly
def _get_base():
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                return line.split("=", 1)[1].strip().rstrip("/")
    raise RuntimeError("REACT_APP_BACKEND_URL missing")

BASE = _get_base()

@pytest.fixture(scope="module")
def s():
    return requests.Session()

# ---- Smoke ----
def test_root(s):
    r = s.get(f"{BASE}/api/")
    assert r.status_code == 200

def test_dashboard(s):
    r = s.get(f"{BASE}/api/dashboard")
    assert r.status_code == 200
    d = r.json()
    assert "kpi" in d and isinstance(d["kpi"], dict)
    assert d["kpi"].get("pezzi_magazzino", 0) > 0

def test_pivot(s):
    r = s.get(f"{BASE}/api/pivot")
    assert r.status_code == 200

def test_parametri_list(s):
    r = s.get(f"{BASE}/api/parametri")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_auto_order_pdf(s):
    r = s.get(f"{BASE}/api/auto-order/pdf")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/pdf")

def test_prodotti_limit(s):
    r = s.get(f"{BASE}/api/prodotti", params={"limit": 5})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) <= 5

def test_vending(s):
    r = s.get(f"{BASE}/api/vending")
    assert r.status_code == 200
    data = r.json()
    # Should have 83 columns
    cols = data if isinstance(data, list) else data.get("colonne") or data.get("celle") or []
    assert len(cols) == 83, f"Expected 83 vending cells, got {len(cols)}"

def test_auto_order(s):
    r = s.get(f"{BASE}/api/auto-order")
    assert r.status_code == 200
    d = r.json()
    assert "n_righe" in d
    assert "totale" in d

# ---- Parametri PUT ----
def test_parametri_put_soglia(s):
    # Get current
    r0 = s.get(f"{BASE}/api/parametri")
    assert r0.status_code == 200
    original = None
    for p in r0.json():
        if p.get("nome") == "SOGLIA_ALLERT_PCT":
            original = p.get("valore")
            break
    assert original is not None
    r = s.put(f"{BASE}/api/parametri/SOGLIA_ALLERT_PCT", json={"valore": 0.35})
    assert r.status_code == 200
    d = r.json()
    assert abs(float(d.get("valore", 0)) - 0.35) < 1e-9
    # Restore
    s.put(f"{BASE}/api/parametri/SOGLIA_ALLERT_PCT", json={"valore": original})

# ---- Vendite bulk ----
def test_vendite_bulk(s):
    # Grab 2 existing prodotti codes
    r0 = s.get(f"{BASE}/api/prodotti", params={"limit": 5})
    prods = r0.json()
    assert len(prods) >= 2
    p1, p2 = prods[0], prods[1]
    # snapshot giacenze
    g1_before = p1.get("giacenza_negozio", 0)
    payload = {
        "canale": "NEGOZIO",
        "pagamento": "CONTANTI",
        "righe": [
            {"data": "2026-01-15", "codice": p1["codice"], "descrizione": p1["descrizione"], "quantita": 1, "importo": float(p1.get("prezzo", 1))},
            {"data": "2026-01-15", "codice": p2["codice"], "descrizione": p2["descrizione"], "quantita": 1, "importo": float(p2.get("prezzo", 1))},
        ],
    }
    r = s.post(f"{BASE}/api/vendite/bulk", json=payload)
    assert r.status_code in (200, 201), r.text
    d = r.json()
    assert d.get("inseriti", 0) >= 2
    # Verify giacenza decrement
    r2 = s.get(f"{BASE}/api/prodotti", params={"q": p1["codice"]})
    match = [x for x in r2.json() if x["codice"] == p1["codice"]]
    assert match
    assert match[0].get("giacenza_negozio", 0) == g1_before - 1

# ---- Vendite CSV vending ----
def test_vendite_csv_vending(s):
    csv_content = (
        "data,nome prodotto,prezzo,colonna,codice AAMS,categoria,pagamento\n"
        "2026-01-15,COCA COLA 33cl,1.50,A01,,BEVANDE,CONTANTI\n"
        "2026-01-15,ACQUA NATURALE,1.00,A02,,BEVANDE,CONTANTI\n"
    )
    files = {"file": ("test.csv", io.BytesIO(csv_content.encode()), "text/csv")}
    r = s.post(f"{BASE}/api/vendite/import-csv-vending", files=files, params={"pagamento": "CONTANTI"})
    assert r.status_code in (200, 201), r.text
    d = r.json()
    assert "delimitatore" in d
    assert d.get("inseriti", 0) >= 1

# ---- Ordini bulk (carico) ----
def test_ordini_bulk(s):
    r0 = s.get(f"{BASE}/api/prodotti", params={"limit": 3})
    prods = r0.json()
    p1, p2 = prods[0], prods[1]
    g1_before = p1.get("giacenza_negozio", 0)
    payload = {
        "file_sorgente": "TEST_ordini_bulk",
        "data": "2026-01-15T00:00:00",
        "righe": [
            {"codice": p1["codice"], "descrizione": p1["descrizione"], "quantita": 2, "prezzo": float(p1.get("prezzo", 1))},
            {"codice": p2["codice"], "descrizione": p2["descrizione"], "quantita": 3, "prezzo": float(p2.get("prezzo", 1))},
        ],
    }
    r = s.post(f"{BASE}/api/ordini/bulk", json=payload)
    assert r.status_code in (200, 201), r.text
    d = r.json()
    assert d.get("caricate", 0) >= 2
    # Verify giacenza increment
    r2 = s.get(f"{BASE}/api/prodotti", params={"q": p1["codice"]})
    match = [x for x in r2.json() if x["codice"] == p1["codice"]]
    assert match
    assert match[0].get("giacenza_negozio", 0) == g1_before + 2

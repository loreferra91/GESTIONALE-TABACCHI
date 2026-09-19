"""Regression tests per code-review remediation iteration_3."""
import os
import io
import pytest
import requests
import openpyxl

BASE = os.environ.get("TEST_BACKEND_URL", "").rstrip("/")
if not BASE:
    pytest.skip(
        "integration suite requires an isolated TEST_BACKEND_URL",
        allow_module_level=True,
    )

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


def test_import_excel_new_product_has_complete_model(s):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "RIEP_VENDITA"
    sheet.append([])
    sheet.append([])
    row = [None] * 18
    row[0] = "AUDIT-XLSX-001"
    row[1] = "Prodotto import audit"
    row[2] = 4
    row[3] = 1
    row[6] = 3
    row[7] = 2.5
    row[13] = 0
    row[17] = 0
    sheet.append(row)
    content = io.BytesIO()
    workbook.save(content)
    content.seek(0)

    response = s.post(
        f"{BASE}/api/import/excel",
        files={"file": ("audit.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["inseriti"] == 1

    products = s.get(f"{BASE}/api/prodotti", params={"q": "AUDIT-XLSX-001"}).json()
    imported = next(p for p in products if p["codice"] == "AUDIT-XLSX-001")
    assert imported["id"]
    assert imported["categoria"] == "ACCESSORI"
    assert imported["created_at"]

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

# ---- Security regressions ----
def test_sec002_prodotti_regex_escape(s):
    # '.*' must be treated as literal, so no product code contains '.*' → 0 results
    r = s.get(f"{BASE}/api/prodotti", params={"q": ".*"})
    assert r.status_code == 200
    assert r.json() == []

def test_sec002_listino_regex_escape(s):
    r = s.get(f"{BASE}/api/listino", params={"q": ".*"})
    assert r.status_code == 200
    d = r.json()
    items = d if isinstance(d, list) else d.get("items", [])
    assert items == []

def test_sec002_vendite_giorno_regex(s):
    r = s.get(f"{BASE}/api/vendite", params={"giorno": "2026-01-15"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_p3_limit_cap(s):
    r = s.get(f"{BASE}/api/prodotti", params={"limit": 99999})
    assert r.status_code == 200
    data = r.json()
    assert len(data) <= 5000

def test_p3_pdf_xml_injection(s):
    r = s.get(f"{BASE}/api/auto-order/pdf", params={"fornitore": "<script>alert(1)</script>"})
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/pdf")
    assert r.content[:5] == b"%PDF-"

def test_sec003_upload_size_413(s):
    # Create ~21MB payload
    big = b"x" * (21 * 1024 * 1024)
    files = {"file": ("big.csv", io.BytesIO(big), "text/csv")}
    r = s.post(f"{BASE}/api/vendite/import-csv-vending", files=files, params={"pagamento": "CONTANTI"})
    assert r.status_code == 413, f"expected 413, got {r.status_code}: {r.text[:200]}"
    assert "troppo grande" in r.text.lower() or "too large" in r.text.lower()

def test_cors_still_works(s):
    r = s.get(f"{BASE}/api/", headers={"Origin": "https://workflow-hub-929.preview.emergentagent.com"})
    assert r.status_code == 200

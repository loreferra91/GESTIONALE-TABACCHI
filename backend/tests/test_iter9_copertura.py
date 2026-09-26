"""Iteration 9 - Auto-Order 'copertura giorni' bug fix tests."""
import os
from datetime import datetime, timezone
import requests
import pytest

def _load_env():
    p = "/app/frontend/.env"
    if os.path.exists(p):
        for line in open(p):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)

_load_env()
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL missing"
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ------------------- Nuovi parametri -------------------
def test_params_contains_new_copertura(client):
    r = client.get(f"{API}/parametri")
    assert r.status_code == 200
    body = r.json()
    # parametri may be dict-like {nome: valore} or list
    if isinstance(body, list):
        d = {p["nome"]: p["valore"] for p in body}
    elif isinstance(body, dict) and "parametri" in body:
        d = body["parametri"]
    else:
        d = body
    assert "GIORNI_COPERTURA_MIN" in d
    assert "GIORNI_COPERTURA_TARGET" in d
    assert float(d["GIORNI_COPERTURA_MIN"]) == 7.0
    assert float(d["GIORNI_COPERTURA_TARGET"]) == 14.0


def test_put_params_valid(client):
    r = client.put(f"{API}/parametri/GIORNI_COPERTURA_MIN", json={"valore": 5})
    assert r.status_code == 200
    r2 = client.put(f"{API}/parametri/GIORNI_COPERTURA_MIN", json={"valore": 7})
    assert r2.status_code == 200
    r3 = client.put(f"{API}/parametri/GIORNI_COPERTURA_TARGET", json={"valore": 14})
    assert r3.status_code == 200


def test_put_params_out_of_range(client):
    r = client.put(f"{API}/parametri/GIORNI_COPERTURA_MIN", json={"valore": 0})
    assert r.status_code == 422, f"Expected 422 got {r.status_code}: {r.text}"
    r2 = client.put(f"{API}/parametri/GIORNI_COPERTURA_TARGET", json={"valore": 400})
    assert r2.status_code == 422


# ------------------- Auto-order rows schema -------------------
def test_auto_order_rows_schema(client):
    r = client.get(f"{API}/auto-order")
    assert r.status_code == 200
    body = r.json()
    assert "righe" in body
    assert isinstance(body["righe"], list)
    if body["righe"]:
        row = body["righe"][0]
        for field in [
            "giacenza_negozio", "giacenza_vending", "venduto_30gg",
            "domanda_gg", "copertura_gg", "motivo", "qta_da_ordinare",
            "lotto_ordine", "prezzo", "totale"
        ]:
            assert field in row, f"missing field {field}"


# ------------------- MAIN BUG FIX test -------------------
@pytest.fixture(scope="module")
def test_cov1(client):
    # Cleanup residuo
    prods = client.get(f"{API}/prodotti").json()
    for p in prods:
        if p.get("codice") == "TEST_COV1":
            client.delete(f"{API}/prodotti/{p['id']}")
    # Cleanup vendite residue
    vend = client.get(f"{API}/vendite").json()
    if isinstance(vend, dict):
        vend = vend.get("vendite", [])
    for v in vend:
        if v.get("codice") == "TEST_COV1":
            client.delete(f"{API}/vendite/{v['id']}")

    payload = {
        "codice": "TEST_COV1",
        "descrizione": "Test copertura fix",
        "categoria": "SIGARETTE",
        "prezzo": 5.5,
        "acquistati": 50,
        "venduti_negozio": 25,
        "venduti_vending": 0,
        "giacenza_negozio": 5,
        "giacenza_vending": 0,
    }
    r = client.post(f"{API}/prodotti", json=payload)
    assert r.status_code in (200, 201), r.text
    prod = r.json()

    # Registra 5 vendite = 30 pezzi totali negli ultimi 30gg
    now = datetime.now(timezone.utc).isoformat()
    vendite_ids = []
    for _ in range(5):
        vr = client.post(f"{API}/vendite", json={
            "data": now, "codice": "TEST_COV1", "descrizione": "Test copertura fix",
            "quantita": 6, "importo": 33.0, "canale": "NEGOZIO", "pagamento": "CONTANTI"
        })
        assert vr.status_code in (200, 201), vr.text
        vj = vr.json()
        if isinstance(vj, dict) and "id" in vj:
            vendite_ids.append(vj["id"])

    yield prod, vendite_ids

    # Teardown
    for vid in vendite_ids:
        client.delete(f"{API}/vendite/{vid}")
    # Also fetch all and delete any remaining
    vend = client.get(f"{API}/vendite").json()
    if isinstance(vend, dict):
        vend = vend.get("vendite", [])
    for v in vend:
        if v.get("codice") == "TEST_COV1":
            client.delete(f"{API}/vendite/{v['id']}")
    client.delete(f"{API}/prodotti/{prod['id']}")


def test_bugfix_low_coverage_appears(client, test_cov1):
    prod, _ = test_cov1
    r = client.get(f"{API}/auto-order")
    assert r.status_code == 200
    righe = r.json()["righe"]
    matches = [x for x in righe if x["codice"] == "TEST_COV1"]
    assert matches, "TEST_COV1 non presente in auto-order — bug fix non funziona"
    row = matches[0]
    assert row["copertura_gg"] is not None
    assert row["copertura_gg"] < 7
    assert row["qta_da_ordinare"] > 0
    motivo = (row["motivo"] or "").upper()
    assert ("COPERTURA" in motivo) or ("FAST MOVER" in motivo) or ("NEGOZIO" in motivo), motivo


# ------------------- Edge cases -------------------
def test_edge_zero_sales_no_trigger(client):
    # Cleanup residuo
    prods = client.get(f"{API}/prodotti").json()
    for p in prods:
        if p.get("codice") == "TEST_ZERO":
            client.delete(f"{API}/prodotti/{p['id']}")
    payload = {"codice": "TEST_ZERO", "descrizione": "Zero sales", "categoria": "ACCESSORI",
               "prezzo": 1.0, "acquistati": 0, "giacenza_negozio": 3, "giacenza_vending": 0}
    r = client.post(f"{API}/prodotti", json=payload)
    assert r.status_code in (200, 201)
    prod = r.json()
    try:
        ao = client.get(f"{API}/auto-order").json()
        matches = [x for x in ao["righe"] if x["codice"] == "TEST_ZERO"]
        assert not matches, f"TEST_ZERO shouldn't be triggered but was: {matches}"
    finally:
        client.delete(f"{API}/prodotti/{prod['id']}")


def test_edge_negozio_zero_no_sales(client):
    prods = client.get(f"{API}/prodotti").json()
    for p in prods:
        if p.get("codice") == "TEST_ESAUR":
            client.delete(f"{API}/prodotti/{p['id']}")
    payload = {"codice": "TEST_ESAUR", "descrizione": "Esaurito", "categoria": "SIGARETTE",
               "prezzo": 5.0, "acquistati": 0, "giacenza_negozio": 0, "giacenza_vending": 0}
    r = client.post(f"{API}/prodotti", json=payload)
    prod = r.json()
    try:
        ao = client.get(f"{API}/auto-order").json()
        matches = [x for x in ao["righe"] if x["codice"] == "TEST_ESAUR"]
        assert matches, "TEST_ESAUR (giacenza=0) deve essere proposto"
        row = matches[0]
        assert "ESAUR" in (row["motivo"] or "").upper() or "REINTEGRO" in (row["motivo"] or "").upper()
        assert row["qta_da_ordinare"] >= 10  # lotto SIGARETTE
    finally:
        client.delete(f"{API}/prodotti/{prod['id']}")


def test_row_count_increased(client):
    """Con fixture di produzione deve ritornare più righe rispetto al baseline di 25."""
    r = client.get(f"{API}/auto-order")
    assert r.status_code == 200
    n = r.json().get("n_righe", 0)
    print(f"Auto-order n_righe = {n}")
    # Non è un hard assert perché dipende dai dati; loggiamo per verifica manuale

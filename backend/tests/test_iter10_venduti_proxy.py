"""
Iter10: Test bug fix per auto-order usando 'venduti_negozio' come proxy della domanda.
"""
import os
import requests
import pytest
import time
from pathlib import Path

def _load_frontend_env():
    envp = Path(__file__).resolve().parents[2] / "frontend" / ".env"
    if envp.exists():
        for line in envp.read_text().splitlines():
            if line.startswith("REACT_APP_BACKEND_URL="):
                return line.split("=", 1)[1].strip()
    return None

BASE_URL = os.environ.get("TEST_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    pytest.skip("requires TEST_BACKEND_URL", allow_module_level=True)
API = f"{BASE_URL}/api"

pytestmark = pytest.mark.external
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gods34.xlsm"


@pytest.fixture(scope="module", autouse=True)
def reset_fixture_for_module():
    """Rende Iter10 indipendente dalle mutazioni dei moduli external precedenti."""
    assert FIXTURE.exists(), f"fixture missing: {FIXTURE}"
    with FIXTURE.open("rb") as fh:
        response = requests.post(
            f"{API}/import/excel-full",
            files={"file": (
                "gods34.xlsm",
                fh,
                "application/vnd.ms-excel.sheet.macroEnabled.12",
            )},
            timeout=240,
        )
    assert response.status_code == 200, response.text[:500]


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _get_param(client, name):
    r = client.get(f"{API}/parametri")
    assert r.status_code == 200
    for p in r.json():
        if p["nome"] == name:
            return p["valore"]
    return None


def _set_param(client, name, valore):
    r = client.put(f"{API}/parametri/{name}", json={"valore": valore})
    return r


# --- 1. PERIODO_VENDUTI_GG parametro esistente e bounds ---
class TestParametroPeriodoVendutiGG:
    def test_get_parametri_contains_periodo_venduti_gg(self, client):
        val = _get_param(client, "PERIODO_VENDUTI_GG")
        assert val is not None, "PERIODO_VENDUTI_GG missing"
        # Default 90 (potrebbe essere già modificato da precedenti test)
        assert 1 <= val <= 3650

    def test_put_valid_value(self, client):
        r = _set_param(client, "PERIODO_VENDUTI_GG", 30)
        assert r.status_code == 200
        assert _get_param(client, "PERIODO_VENDUTI_GG") == 30
        # reset
        assert _set_param(client, "PERIODO_VENDUTI_GG", 90).status_code == 200

    def test_put_out_of_bounds_zero(self, client):
        r = _set_param(client, "PERIODO_VENDUTI_GG", 0)
        assert r.status_code == 422, f"expected 422 got {r.status_code}: {r.text}"

    def test_put_out_of_bounds_high(self, client):
        r = _set_param(client, "PERIODO_VENDUTI_GG", 4000)
        assert r.status_code == 422, f"expected 422 got {r.status_code}: {r.text}"


# --- 2. Bug fix concreto AMMS395 / AMMS396 ---
class TestAutoOrderAMMS395_396:
    def test_amms395_in_auto_order(self, client):
        # Assicurati PERIODO=90
        _set_param(client, "PERIODO_VENDUTI_GG", 90)
        r = client.get(f"{API}/auto-order")
        assert r.status_code == 200
        data = r.json()
        righe = {row["codice"]: row for row in data["righe"]}
        assert "AMMS395" in righe, f"AMMS395 not in auto-order (n_righe={data['n_righe']})"
        row = righe["AMMS395"]
        # RIMANENZE Excel viene convertita in stock fisico libero.
        assert row["giacenza_negozio"] == 0, row
        assert row["qta_da_ordinare"] > 0
        assert ("COPERTURA" in row["motivo"]) or ("FAST MOVER" in row["motivo"]), row["motivo"]
        assert row["domanda_gg"] > 4
        assert row["copertura_gg"] is not None and row["copertura_gg"] < 10

    def test_amms396_has_sufficient_physical_coverage(self, client):
        r = client.get(f"{API}/auto-order")
        data = r.json()
        rows = {row["codice"]: row for row in [*data["righe"], *data["esclusi"]]}
        row = rows["AMMS396"]
        assert row["giacenza_negozio"] == 20, row
        assert row["stato"] == "NESSUN ORDINE"
        assert row["qta_da_ordinare"] == 0
        assert row["copertura_gg"] is not None and row["copertura_gg"] >= 14

    def test_auto_order_returns_actionable_subset(self, client):
        r = client.get(f"{API}/auto-order")
        data = r.json()
        assert data["n_righe"] > 0
        assert data["n_righe"] == len(data["righe"])
        assert all(not row["anomalia"] for row in data["righe"])


# --- 3. Effetto parametro ---
class TestPeriodoEffect:
    def test_periodo_legacy_does_not_change_recent_demand(self, client):
        _set_param(client, "PERIODO_VENDUTI_GG", 90)
        r = client.get(f"{API}/auto-order")
        row90 = next(x for x in r.json()["righe"] if x["codice"] == "AMMS395")

        _set_param(client, "PERIODO_VENDUTI_GG", 180)
        r = client.get(f"{API}/auto-order")
        row180 = next(x for x in r.json()["righe"] if x["codice"] == "AMMS395")
        assert row180["domanda_gg"] == row90["domanda_gg"]
        assert row180["venduto_breve"] == row90["venduto_breve"]
        assert row180["venduto_lungo"] == row90["venduto_lungo"]
        assert _set_param(client, "PERIODO_VENDUTI_GG", 90).status_code == 200


# --- 4. Regressione in-app tracking ---
class TestRegressionInAppTracking:
    codice = "TEST_APP_TRACKED"

    def _cleanup(self, client):
        # elimina vendite e prodotto
        try:
            vendite = client.get(f"{API}/vendite").json()
            for v in vendite:
                if v.get("codice") == self.codice:
                    client.delete(f"{API}/vendite/{v['id']}")
        except Exception:
            pass
        try:
            client.delete(f"{API}/prodotti/{self.codice}")
        except Exception:
            pass

    def test_in_app_tracking(self, client):
        self._cleanup(client)
        # crea prodotto
        prod = {
            "codice": self.codice,
            "descrizione": "Test in-app tracking",
            "categoria": "ACCESSORI",
            "prezzo": 5.0,
            "acquistati": 10,
            "giacenza_negozio": 35,
            "giacenza_vending": 0,
            "venduti_negozio": 0,
            "venduti_vending": 0,
        }
        r = client.post(f"{API}/prodotti", json=prod)
        assert r.status_code in (200, 201), r.text

        # 5 vendite da 6 pezzi ciascuna nel negozio
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        for _ in range(5):
            r = client.post(f"{API}/vendite", json={
                "codice": self.codice,
                "quantita": 6,
                "canale": "NEGOZIO",
                "data": now_iso,
            })
            assert r.status_code in (200, 201), r.text

        # auto-order
        r = client.get(f"{API}/auto-order")
        assert r.status_code == 200
        righe = {row["codice"]: row for row in r.json()["righe"]}
        assert self.codice in righe, "TEST_APP_TRACKED not in auto-order"
        row = righe[self.codice]
        # 30 pezzi venduti in-app, tracked days piccolo → domanda_gg alta (>=1)
        assert row["domanda_gg"] >= 1, row

        self._cleanup(client)


# --- 5. Regressione NEGOZIO ESAURITO ---
class TestNegozioEsaurito:
    codice = "TEST_NEG_ESAURITO"

    def test_negozio_esaurito(self, client):
        # cleanup preventivo
        try:
            client.delete(f"{API}/prodotti/{self.codice}")
        except Exception:
            pass
        prod = {
            "codice": self.codice,
            "descrizione": "Test esaurito",
            "categoria": "ACCESSORI",
            "prezzo": 3.0,
            "acquistati": 2,
            "giacenza_negozio": 2,
            "giacenza_vending": 0,
            "venduti_negozio": 0,
            "venduti_vending": 0,
        }
        r = client.post(f"{API}/prodotti", json=prod)
        assert r.status_code in (200, 201), r.text
        product_id = r.json()["id"]
        sale_ids = []
        from datetime import datetime, timezone
        for _ in range(2):
            sale = client.post(f"{API}/vendite", json={
                "codice": self.codice,
                "quantita": 1,
                "canale": "NEGOZIO",
                "data": datetime.now(timezone.utc).isoformat(),
            })
            assert sale.status_code in (200, 201), sale.text
            sale_ids.append(sale.json()["id"])
        try:
            r = client.get(f"{API}/auto-order")
            righe = {row["codice"]: row for row in r.json()["righe"]}
            assert self.codice in righe
            assert "COPERTURA" in righe[self.codice]["motivo"]
            assert righe[self.codice]["giacenza_negozio"] == 0
        finally:
            for sale_id in sale_ids:
                client.delete(f"{API}/vendite/{sale_id}")
            client.delete(f"{API}/prodotti/{product_id}")


# --- Final teardown: assicura PERIODO_VENDUTI_GG=90 ---
def test_zzz_restore_default(client):
    r = _set_param(client, "PERIODO_VENDUTI_GG", 90)
    assert r.status_code == 200

"""Tests for AGGIO_PCT feature (iteration_6)."""
import os
import pytest
import requests


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


def _get_param(s, name):
    r = s.get(f"{BASE}/api/parametri")
    assert r.status_code == 200
    for p in r.json():
        if p["nome"] == name:
            return p["valore"]
    return None


def test_aggio_pct_default_present(s):
    val = _get_param(s, "AGGIO_PCT")
    assert val is not None, "AGGIO_PCT not found in /api/parametri"
    assert abs(float(val) - 0.10) < 1e-9, f"expected 0.10, got {val}"


def test_dashboard_kpi_has_aggio_and_margine(s):
    r = s.get(f"{BASE}/api/dashboard")
    assert r.status_code == 200
    kpi = r.json()["kpi"]
    assert "aggio_pct" in kpi
    assert "margine_lordo" in kpi
    assert "valore_acquistato" in kpi
    assert "valore_venduto" in kpi
    assert "valore_giacenza" in kpi
    # margine_lordo consistent
    assert abs(kpi["margine_lordo"] - (kpi["valore_venduto"] - kpi["valore_acquistato"])) < 0.05


def test_pivot_kpi_consistent(s):
    r = s.get(f"{BASE}/api/pivot")
    assert r.status_code == 200
    kpi = r.json()["kpi"]
    assert abs(kpi["margine_lordo"] - (kpi["valore_venduto"] - kpi["valore_acquistato"])) < 0.05
    assert abs(float(kpi["aggio_pct"]) - 0.10) < 1e-6


def test_aggio_recompute_015(s):
    # Get baseline valore_acquistato at aggio=0.10
    r0 = s.get(f"{BASE}/api/dashboard")
    kpi0 = r0.json()["kpi"]
    va0 = kpi0["valore_acquistato"]
    # baseline retail sum = va0 / 0.9
    retail_sum = va0 / 0.9

    # Change to 0.15
    r = s.put(f"{BASE}/api/parametri/AGGIO_PCT", json={"valore": 0.15})
    assert r.status_code == 200
    try:
        r1 = s.get(f"{BASE}/api/dashboard")
        kpi1 = r1.json()["kpi"]
        expected = retail_sum * 0.85
        assert abs(kpi1["valore_acquistato"] - expected) < 0.5, (
            f"expected ~{expected:.2f}, got {kpi1['valore_acquistato']:.2f}"
        )
        assert abs(float(kpi1["aggio_pct"]) - 0.15) < 1e-6
        # margine_lordo increased (cost decreased -> margine bigger)
        assert kpi1["margine_lordo"] > kpi0["margine_lordo"]
    finally:
        # Restore
        rr = s.put(f"{BASE}/api/parametri/AGGIO_PCT", json={"valore": 0.10})
        assert rr.status_code == 200

    # Verify restored
    r2 = s.get(f"{BASE}/api/dashboard")
    kpi2 = r2.json()["kpi"]
    assert abs(kpi2["valore_acquistato"] - va0) < 0.05

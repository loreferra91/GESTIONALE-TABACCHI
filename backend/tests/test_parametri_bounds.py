"""Tests for PUT /api/parametri/{nome} bounds validation (iteration 7)."""
import os
import pytest
import requests
from pathlib import Path

def _load_frontend_env():
    p = Path("/app/frontend/.env")
    if p.exists():
        for line in p.read_text().splitlines():
            if line.startswith("REACT_APP_BACKEND_URL="):
                return line.split("=", 1)[1].strip()
    return None

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _load_frontend_env() or "").rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _get_param(client, nome):
    r = client.get(f"{API}/parametri")
    assert r.status_code == 200
    for p in r.json():
        if p["nome"] == nome:
            return p
    return None


# ---------- AGGIO_PCT ----------
def test_aggio_valid(client):
    r = client.put(f"{API}/parametri/AGGIO_PCT", json={"valore": 0.10})
    assert r.status_code == 200
    assert r.json()["valore"] == 0.10


def test_aggio_negative(client):
    r = client.put(f"{API}/parametri/AGGIO_PCT", json={"valore": -0.5})
    assert r.status_code == 422
    assert "fuori range consentito" in r.text


def test_aggio_too_high(client):
    r = client.put(f"{API}/parametri/AGGIO_PCT", json={"valore": 1.5})
    assert r.status_code == 422
    assert "fuori range consentito" in r.text


# ---------- SOGLIA_ALLERT_PCT ----------
def test_soglia_valid(client):
    r = client.put(f"{API}/parametri/SOGLIA_ALLERT_PCT", json={"valore": 0.35})
    assert r.status_code == 200
    assert r.json()["valore"] == 0.35


def test_soglia_out_of_range(client):
    r = client.put(f"{API}/parametri/SOGLIA_ALLERT_PCT", json={"valore": 2})
    assert r.status_code == 422


# ---------- LOTTO_SIGARETTE ----------
def test_lotto_valid(client):
    r = client.put(f"{API}/parametri/LOTTO_SIGARETTE", json={"valore": 10})
    assert r.status_code == 200
    assert r.json()["valore"] == 10


def test_lotto_zero(client):
    r = client.put(f"{API}/parametri/LOTTO_SIGARETTE", json={"valore": 0})
    assert r.status_code == 422


# ---------- GIORNI_STORICO_VEND ----------
def test_giorni_valid(client):
    r = client.put(f"{API}/parametri/GIORNI_STORICO_VEND", json={"valore": 30})
    assert r.status_code == 200
    assert r.json()["valore"] == 30


def test_giorni_zero(client):
    r = client.put(f"{API}/parametri/GIORNI_STORICO_VEND", json={"valore": 0})
    assert r.status_code == 422


# ---------- Unknown param passthrough ----------
def test_unknown_param_passthrough(client):
    r = client.put(f"{API}/parametri/TEST_UNKNOWN_PARAM", json={"valore": 42})
    assert r.status_code == 200
    assert r.json()["valore"] == 42
    # cleanup
    from pymongo import MongoClient
    mc = MongoClient(os.environ.get("MONGO_URL"))
    mc[os.environ.get("DB_NAME")].parametri.delete_one({"nome": "TEST_UNKNOWN_PARAM"})


# ---------- Rollback safe after failed 422 ----------
def test_rollback_after_422(client):
    # ensure baseline
    client.put(f"{API}/parametri/AGGIO_PCT", json={"valore": 0.10})
    # attempt invalid
    r = client.put(f"{API}/parametri/AGGIO_PCT", json={"valore": 99})
    assert r.status_code == 422
    # verify still 0.10
    p = _get_param(client, "AGGIO_PCT")
    assert p is not None
    assert p["valore"] == 0.10


# ---------- Regression dashboard ----------
def test_dashboard_aggio_reflected(client):
    client.put(f"{API}/parametri/AGGIO_PCT", json={"valore": 0.10})
    r = client.get(f"{API}/dashboard")
    assert r.status_code == 200
    data = r.json()
    kpi = data.get("kpi", {})
    assert kpi.get("aggio_pct") == 0.10


def test_final_restore_aggio(client):
    r = client.put(f"{API}/parametri/AGGIO_PCT", json={"valore": 0.10})
    assert r.status_code == 200

import base64
import os

from fastapi.testclient import TestClient

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

from backend.server import app


client = TestClient(app)


def test_unknown_api_is_not_served_by_spa_fallback():
    response = client.get("/api/endpoint-inesistente")
    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")


def test_health_check_bypasses_optional_basic_auth(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "audit-user")
    monkeypatch.setenv("APP_PASSWORD", "audit-password")
    response = client.get("/api/")
    assert response.status_code == 200


def test_head_root_bypasses_optional_basic_auth(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "audit-user")
    monkeypatch.setenv("APP_PASSWORD", "audit-password")
    response = client.head("/")
    assert response.status_code == 200
    assert response.content == b""


def test_basic_auth_rejects_missing_and_malformed_headers(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "audit-user")
    monkeypatch.setenv("APP_PASSWORD", "audit-password")
    for authorization in (None, "Basic !!!not-base64!!!"):
        headers = {"Authorization": authorization} if authorization else {}
        response = client.get("/api/prodotti", headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Basic")


def test_basic_auth_accepts_valid_credentials(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "audit-user")
    monkeypatch.setenv("APP_PASSWORD", "audit-password")
    token = base64.b64encode(b"audit-user:audit-password").decode("ascii")
    response = client.get("/api/endpoint-inesistente", headers={"Authorization": f"Basic {token}"})
    assert response.status_code == 404

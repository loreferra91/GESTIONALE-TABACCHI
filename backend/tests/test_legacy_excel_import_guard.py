"""Legacy workbook writes require an operator action and an explicit opt-in."""
import os

from fastapi.testclient import TestClient

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

from backend import server


def test_legacy_workbook_writes_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_LEGACY_EXCEL_IMPORT", raising=False)
    client = TestClient(server.app)
    for path in ("/api/import/excel-full", "/api/smart-venue/import-excel"):
        response = client.post(path, files={"file": ("legacy.xlsx", b"invalid")}, headers={
            "X-Legacy-Import-Confirmed": "confermo-import-legacy",
        })
        assert response.status_code == 409
        assert "disabilitato" in response.json()["detail"]


def test_legacy_workbook_writes_require_explicit_confirmation(monkeypatch):
    monkeypatch.setenv("ENABLE_LEGACY_EXCEL_IMPORT", "true")
    client = TestClient(server.app)
    response = client.post("/api/import/excel-full", files={"file": ("legacy.xlsx", b"invalid")})
    assert response.status_code == 409
    assert "Conferma esplicita" in response.json()["detail"]


def test_unprotected_old_excel_endpoint_is_retired():
    response = TestClient(server.app).post(
        "/api/import/excel", files={"file": ("legacy.xlsx", b"invalid")},
    )
    assert response.status_code == 410

"""Staging health includes the Render commit without affecting production payload."""
import asyncio
from backend import server


def test_staging_health_returns_deployed_revision_without_credentials(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abcde12345")
    # Do not run live MongoDB calls from this unit test.
    monkeypatch.setattr(server, "_requires_secure_runtime", lambda: False)

    response = asyncio.run(server.root())

    assert response == {
        "app": "God Services Gestionale Tabacchi",
        "status": "ok",
        "database": "ok",
        "revision": "abcde12345",
    }


def test_production_health_does_not_expose_revision(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "prod-sha")
    monkeypatch.setattr(server, "_requires_secure_runtime", lambda: False)

    response = asyncio.run(server.root())

    assert response["status"] == "ok"
    assert response["database"] == "ok"
    assert "revision" not in response

import base64
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

from backend import server


app = server.app


client = TestClient(app)


def test_unknown_api_is_not_served_by_spa_fallback():
    response = client.get("/api/endpoint-inesistente")
    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")


def test_spa_index_is_never_cached_across_deploys():
    response = server._frontend_index_response()
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"


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


def test_openapi_is_not_mistaken_for_health_check_by_malformed_host(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "audit-user")
    monkeypatch.setenv("APP_PASSWORD", "audit-password")
    response = client.get(
        "/openapi.json",
        headers={"Host": "example.invalid/api/?x="},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")


def test_real_health_check_paths_remain_public(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "audit-user")
    monkeypatch.setenv("APP_PASSWORD", "audit-password")

    get_api = client.get("/api/")
    head_root = client.head("/")

    assert get_api.status_code == 200
    assert head_root.status_code == 200
    assert head_root.content == b""


def test_non_health_check_methods_and_paths_require_basic_auth(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "audit-user")
    monkeypatch.setenv("APP_PASSWORD", "audit-password")

    responses = (
        client.post("/api/"),
        client.head("/api/"),
        client.get("/"),
    )

    for response in responses:
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Basic")


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


def test_basic_auth_rejects_wrong_credentials_on_admin_and_write_routes(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "audit-user")
    monkeypatch.setenv("APP_PASSWORD", "audit-password")
    token = base64.b64encode(b"audit-user:wrong-password").decode("ascii")
    headers = {"Authorization": f"Basic {token}"}

    admin = client.get("/api/audit", headers=headers)
    mutation = client.post("/api/ordini", headers=headers, json={})

    assert admin.status_code == 401
    assert mutation.status_code == 401


def test_encoded_traversal_cannot_use_agent_auth_exemption(monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "audit-user")
    monkeypatch.setenv("APP_PASSWORD", "audit-password")

    response = client.get("/api/agent/v1/%2e%2e/%2e%2e/prodotti")

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")


def test_production_refuses_missing_credentials(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("APP_USERNAME", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://example.test")

    with pytest.raises(RuntimeError, match="APP_USERNAME"):
        server._validate_runtime_security()


def test_production_refuses_wildcard_cors(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("APP_USERNAME", "operator")
    monkeypatch.setenv("APP_PASSWORD", "secret")
    monkeypatch.setenv("CORS_ORIGINS", "*")

    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        server._validate_runtime_security()


def test_production_requires_explicit_cors_origin(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("APP_USERNAME", "operator")
    monkeypatch.setenv("APP_PASSWORD", "secret")
    monkeypatch.setenv("CORS_ORIGINS", "")

    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        server._validate_runtime_security()


def test_production_requires_strong_separate_agent_token_when_enabled(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("APP_USERNAME", "operator")
    monkeypatch.setenv("APP_PASSWORD", "secret")
    monkeypatch.setenv("CORS_ORIGINS", "https://example.test")
    monkeypatch.setenv("AGENT_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("AGENT_INTEGRATION_TOKEN", "too-short")

    with pytest.raises(RuntimeError, match="AGENT_INTEGRATION_TOKEN"):
        server._validate_runtime_security()


def test_transaction_capability_accepts_replica_set(monkeypatch):
    fake_client = SimpleNamespace(admin=SimpleNamespace(command=AsyncMock(return_value={
        "setName": "rs0", "logicalSessionTimeoutMinutes": 30,
    })))
    monkeypatch.setattr(server, "client", fake_client)
    monkeypatch.setenv("ENVIRONMENT", "production")

    result = asyncio.run(server._verify_transaction_capability())

    assert result == {"topology": "replica_set", "transactions": True}


def test_transaction_capability_fails_closed_for_production_standalone(monkeypatch):
    fake_client = SimpleNamespace(admin=SimpleNamespace(command=AsyncMock(return_value={
        "logicalSessionTimeoutMinutes": 30,
    })))
    monkeypatch.setattr(server, "client", fake_client)
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="standalone"):
        asyncio.run(server._verify_transaction_capability())


def test_transaction_wrapper_uses_driver_retry_helper(monkeypatch):
    class FakeSession:
        called = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def with_transaction(self, callback):
            self.called = True
            return await callback(self)

    session = FakeSession()
    fake_client = SimpleNamespace(start_session=AsyncMock(return_value=session))
    monkeypatch.setattr(server, "db", SimpleNamespace(client=fake_client))

    result = asyncio.run(server._run_transactionally(lambda active: asyncio.sleep(0, result=active)))

    assert result is session
    assert session.called is True


def test_production_refuses_missing_required_unique_indexes(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    results = [{"collection": "prodotti", "field": "codice", "status": "duplicates"}]

    with pytest.raises(RuntimeError, match="prodotti.codice"):
        server._validate_required_index_results(results)


def test_security_headers_are_present():
    response = client.get("/api/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"

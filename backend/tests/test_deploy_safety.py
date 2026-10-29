import base64
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure

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


def test_transaction_wrapper_surfaces_exhausted_write_conflict_as_retryable(monkeypatch):
    calls = []

    class FakeSession:
        async def with_transaction(self, callback):
            # Simulate the driver's callback retry contract before it gives up.
            await callback(self)
            await callback(self)
            raise OperationFailure(
                "WriteConflict",
                code=112,
                details={"errorLabels": ["TransientTransactionError"]},
            )

        async def end_session(self):
            calls.append("ended")

    session = FakeSession()
    fake_client = SimpleNamespace(start_session=AsyncMock(return_value=session))
    monkeypatch.setattr(server, "db", SimpleNamespace(client=fake_client))

    async def operation(active):
        calls.append(active)

    with pytest.raises(server.HTTPException) as exc:
        asyncio.run(server._run_transactionally(operation))

    assert exc.value.status_code == 503
    assert exc.value.headers == {"Retry-After": "1"}
    assert "stessa chiave idempotente" in exc.value.detail
    assert calls == [session, session, "ended"]


def test_transaction_wrapper_does_not_guess_transience_from_code_alone(monkeypatch):
    class FakeSession:
        async def with_transaction(self, _callback):
            raise OperationFailure("unlabelled conflict", code=112)

        async def end_session(self):
            return None

    fake_client = SimpleNamespace(start_session=AsyncMock(return_value=FakeSession()))
    monkeypatch.setattr(server, "db", SimpleNamespace(client=fake_client))

    with pytest.raises(OperationFailure, match="unlabelled conflict"):
        asyncio.run(server._run_transactionally(lambda _active: asyncio.sleep(0)))


def test_transaction_wrapper_reports_uncertain_commit_without_retry_advice(monkeypatch):
    class FakeSession:
        async def with_transaction(self, _callback):
            raise OperationFailure(
                "commit result unknown",
                code=64,
                details={"errorLabels": ["UnknownTransactionCommitResult"]},
            )

        async def end_session(self):
            return None

    fake_client = SimpleNamespace(start_session=AsyncMock(return_value=FakeSession()))
    monkeypatch.setattr(server, "db", SimpleNamespace(client=fake_client))

    with pytest.raises(server.HTTPException) as exc:
        asyncio.run(server._run_transactionally(lambda _active: asyncio.sleep(0)))

    assert exc.value.status_code == 503
    assert exc.value.headers is None
    assert "verificare lo stato" in exc.value.detail


def test_transaction_wrapper_closes_session_without_transaction_api(monkeypatch):
    calls = []

    class FakeSession:
        async def end_session(self):
            calls.append("ended")

    fake_client = SimpleNamespace(start_session=AsyncMock(return_value=FakeSession()))
    monkeypatch.setattr(server, "db", SimpleNamespace(client=fake_client))
    monkeypatch.setenv("ENVIRONMENT", "test")

    result = asyncio.run(
        server._run_transactionally(
            lambda active: asyncio.sleep(0, result=active)
        )
    )

    assert result is None
    assert calls == ["ended"]


def test_missing_seed_dataset_emits_an_actionable_warning(monkeypatch, caplog, tmp_path):
    monkeypatch.setattr(server, "ROOT_DIR", tmp_path)

    with caplog.at_level("WARNING"):
        asyncio.run(server.seed_if_empty())

    assert "Seed dataset unavailable" in caplog.text
    assert str(tmp_path / "seed" / "seed_data.json") in caplog.text


def test_production_refuses_missing_required_unique_indexes(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    results = [{"collection": "prodotti", "field": "codice", "status": "duplicates"}]

    with pytest.raises(RuntimeError, match="prodotti.codice"):
        server._validate_required_index_results(results)


def test_security_headers_include_transport_and_content_policy():
    response = client.get("/api/")

    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["strict-transport-security"].startswith("max-age=")


def test_security_headers_are_present():
    response = client.get("/api/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_staging_refuses_missing_credentials(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.delenv("APP_USERNAME", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://staging.example.test")

    with pytest.raises(RuntimeError, match="APP_USERNAME"):
        server._validate_runtime_security()


def test_unknown_environment_fails_closed(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_USERNAME", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)

    assert server._requires_secure_runtime() is True
    with pytest.raises(RuntimeError, match="APP_USERNAME"):
        server._validate_runtime_security()


def test_development_allows_missing_credentials(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("APP_USERNAME", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)

    assert server._requires_secure_runtime() is False
    server._validate_runtime_security()


def test_staging_requires_basic_auth_even_when_credentials_exist(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("APP_USERNAME", "audit-user")
    monkeypatch.setenv("APP_PASSWORD", "audit-password")
    monkeypatch.setenv("CORS_ORIGINS", "https://staging.example.test")

    response = client.get("/api/prodotti")

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")


def test_non_local_without_credentials_denies_requests(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.delenv("APP_USERNAME", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)

    response = client.get("/api/prodotti")

    assert response.status_code == 503


def test_non_ascii_credentials_are_rejected_without_server_error(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("APP_USERNAME", "operatore")
    monkeypatch.setenv("APP_PASSWORD", "password-sicura")
    token = base64.b64encode("operatore:pässwörd".encode("utf-8")).decode("ascii")

    response = client.get("/api/prodotti", headers={"Authorization": f"Basic {token}"})

    assert response.status_code == 401


def test_cross_site_mutation_is_rejected(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("APP_USERNAME", "audit-user")
    monkeypatch.setenv("APP_PASSWORD", "audit-password")
    token = base64.b64encode(b"audit-user:audit-password").decode("ascii")

    response = client.post(
        "/api/",
        headers={
            "Authorization": f"Basic {token}",
            "Origin": "https://evil.example",
            "Host": "gestionale.example",
        },
    )

    assert response.status_code == 403


def test_same_origin_mutation_passes_csrf_check(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("APP_USERNAME", "audit-user")
    monkeypatch.setenv("APP_PASSWORD", "audit-password")
    token = base64.b64encode(b"audit-user:audit-password").decode("ascii")

    response = client.post(
        "/api/endpoint-inesistente",
        headers={
            "Authorization": f"Basic {token}",
            "Origin": "https://gestionale.example",
            "Host": "gestionale.example",
        },
    )

    assert response.status_code in {404, 405}


def test_health_check_reports_database_failure(monkeypatch):
    monkeypatch.setattr(server, "_requires_secure_runtime", lambda: True)
    fake_client = SimpleNamespace(
        admin=SimpleNamespace(command=AsyncMock(side_effect=RuntimeError("mongo down")))
    )
    monkeypatch.setattr(server, "client", fake_client)

    response = client.get("/api/")

    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


def test_health_check_reports_database_success(monkeypatch):
    monkeypatch.setattr(server, "_requires_secure_runtime", lambda: True)
    fake_client = SimpleNamespace(
        admin=SimpleNamespace(command=AsyncMock(return_value={"ok": 1}))
    )
    monkeypatch.setattr(server, "client", fake_client)

    response = client.get("/api/")

    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_transaction_wrapper_does_not_swallow_business_attribute_error(monkeypatch):
    class FakeSession:
        async def with_transaction(self, callback):
            return await callback(self)

    fake_client = SimpleNamespace(start_session=AsyncMock(return_value=FakeSession()))
    monkeypatch.setattr(server, "db", SimpleNamespace(client=fake_client))
    monkeypatch.setenv("ENVIRONMENT", "test")

    async def operation(_session):
        raise AttributeError("business bug")

    with pytest.raises(AttributeError, match="business bug"):
        asyncio.run(server._run_transactionally(operation))


def test_transaction_wrapper_fails_closed_without_sessions(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(server, "db", SimpleNamespace(client=SimpleNamespace()))

    with pytest.raises(RuntimeError, match="sessioni"):
        asyncio.run(server._run_transactionally(lambda _active: asyncio.sleep(0)))


def test_data_status_uses_distinct_dates_without_full_scans(monkeypatch):
    calls = []

    class DistinctCollection:
        def __init__(self, values):
            self.values = values

        async def distinct(self, field):
            calls.append(field)
            return self.values

        async def count_documents(self, query):
            return 1

        async def find_one(self, *args, **kwargs):
            return None

    class Database(dict):
        def __getattr__(self, name):
            if name not in self:
                raise AttributeError(f"collezione inattesa: {name}")
            return self[name]

        def __getitem__(self, name):
            if name not in self:
                raise AssertionError(f"collezione inattesa: {name}")
            return dict.__getitem__(self, name)

    database = Database({
        "vendite": DistinctCollection(["2026-10-27", "2026-10-28"]),
        "db_storico_vend": DistinctCollection(["2026-10-26"]),
        "import_history": DistinctCollection([]),
        "backup_snapshots": DistinctCollection([]),
    })
    for name in server.BACKUP_COLLECTIONS:
        if name not in database:
            database[name] = DistinctCollection([name])
    monkeypatch.setattr(server, "db", database)
    operations = server.create_operations_router(
        get_database=lambda: database,
        backup_collections=server.BACKUP_COLLECTIONS,
        parse_sale_date=server._parse_sale_date,
        latest_sale_date=server._latest_sale_date,
        query_regex=server._q_regex,
        vending_stock_by_code=AsyncMock(return_value={}),
        apply_vending_stock=lambda products, _totals: products,
        get_current_anomalies=AsyncMock(return_value=[]),
        get_dashboard=AsyncMock(return_value={}),
        get_auto_order=AsyncMock(return_value={}),
        schedule_daily_backup=AsyncMock(return_value={"status": "ok"}),
    )

    payload = asyncio.run(operations["data_status_payload"]())

    assert calls == ["data", "data"]
    assert payload["latest_sales_day"] == "2026-10-28"


def test_render_blueprint_requires_manual_deploy():
    blueprint = (server.ROOT_DIR.parent / "render.yaml").read_text(encoding="utf-8")

    assert "autoDeploy: false" in blueprint
    assert "autoDeploy: true" not in blueprint

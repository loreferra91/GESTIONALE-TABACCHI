"""Staging intake validation without a database or network."""
from uuid import uuid4
import pytest

from backend.desktop_sync.local_outbox import payload_hash
from backend.desktop_sync.staging_receiver import validate_event, StagingReceiver
from backend.desktop_sync.staging_service import StagingConfig


def sample_record():
    device = str(uuid4())
    sale_id = str(uuid4())
    core = {
        "data": "2026-11-18", "codice": "TEST001", "quantita": 2,
        "importo_cents": 1200, "canale": "NEGOZIO",
        "pagamento": "CONTANTI", "colonna": None,
    }
    return {
        "operation_id": str(uuid4()), "device_id": device,
        "entity_kind": "sale", "entity_id": sale_id, "kind": "sale.create",
        "payload_hash": payload_hash(core),
        "event_payload": {**core, "sale_id": sale_id, "created_at": "2026-11-18T11:00:00+00:00"},
        "schema_version": 1,
    }


def test_validated_receipt_has_no_business_authority():
    item = sample_record()
    assert validate_event(item, item["device_id"])["kind"] == "sale.create"


@pytest.mark.parametrize("field,value", [
    ("quantita", True), ("quantita", -1), ("importo_cents", 2.1),
    ("importo_cents", -1), ("canale", "VENDING"), ("codice", ""),
])
def test_rejects_broken_payload(field, value):
    item = sample_record()
    item["event_payload"][field] = value
    with pytest.raises(ValueError):
        validate_event(item, item["device_id"])


def test_rejects_rewritten_identity_and_device():
    item = sample_record()
    with pytest.raises(ValueError):
        validate_event(item, str(uuid4()))
    item["event_payload"]["sale_id"] = str(uuid4())
    with pytest.raises(ValueError):
        validate_event(item, item["device_id"])


def test_separate_staging_db_required(monkeypatch):
    item = sample_record()
    monkeypatch.setenv("DESKTOP_SYNC_STAGING_ENABLED", "1")
    monkeypatch.setenv("DESKTOP_SYNC_STAGING_WORKSPACE_ID", str(uuid4()))
    monkeypatch.setenv("DESKTOP_SYNC_STAGING_DEVICE_ID", item["device_id"])
    monkeypatch.setenv("DESKTOP_SYNC_STAGING_TOKEN", "s" * 48)
    monkeypatch.setenv("DESKTOP_SYNC_STAGING_MONGO_URL", "mongodb://127.0.0.1:27019/")
    monkeypatch.setenv("DESKTOP_SYNC_STAGING_DB", "gestionale_desktop")
    with pytest.raises(RuntimeError, match="namespaced"):
        StagingConfig.from_env()


def test_remote_access_requires_separate_opt_in(monkeypatch):
    item = sample_record()
    monkeypatch.setenv("DESKTOP_SYNC_STAGING_ENABLED", "1")
    monkeypatch.setenv("DESKTOP_SYNC_STAGING_WORKSPACE_ID", str(uuid4()))
    monkeypatch.setenv("DESKTOP_SYNC_STAGING_DEVICE_ID", item["device_id"])
    monkeypatch.setenv("DESKTOP_SYNC_STAGING_TOKEN", "s" * 48)
    monkeypatch.setenv("DESKTOP_SYNC_STAGING_MONGO_URL", "mongodb+srv://staging.example.invalid/")
    monkeypatch.setenv("DESKTOP_SYNC_STAGING_DB", "desktop_sync_staging_test")
    monkeypatch.delenv("DESKTOP_SYNC_STAGING_ALLOW_REMOTE", raising=False)
    with pytest.raises(RuntimeError, match="opt-in"):
        StagingConfig.from_env()


def test_immutable_record_fingerprint_covers_metadata():
    from backend.desktop_sync.staging_receiver import _immutable_record_digest
    item = sample_record()
    same = {**item, "event_payload": dict(item["event_payload"])}
    assert _immutable_record_digest(item) == _immutable_record_digest(same)
    same["event_payload"]["created_at"] = "2026-11-18T12:00:00+00:00"
    assert _immutable_record_digest(item) != _immutable_record_digest(same)

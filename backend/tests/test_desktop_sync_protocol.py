"""Offline synchronization preflight tests. These are NOT end-to-end tests."""
from uuid import uuid4
import pytest
from backend.desktop_sync.protocol import (
    SyncEnvelope, SyncDecision, canonical_digest, decide_incoming, validate_batch,
)


def op(**changes):
    values = dict(operation_id=str(uuid4()), device_id=str(uuid4()),
                  entity_kind="product", entity_id="000123", kind="stock.receive",
                  base_revision=3, payload={"quantity": 4, "amount_cents": 1200})
    values.update(changes)
    return SyncEnvelope(**values)


def check(o, rev=3, digest=None, authorized=True):
    return decide_incoming(o, current_revision=rev,
                           existing_digest=digest, writer_authorized=authorized)[0]


def test_accept_eligible_preflight():
    assert check(op()) == SyncDecision.ACCEPT


def test_stale_revision_not_merged():
    assert check(op(), rev=4) == SyncDecision.CONFLICT


def test_idempotent_replay_same_digest():
    o = op()
    assert check(o, digest=canonical_digest(o)) == SyncDecision.ALREADY_APPLIED


def test_reused_id_changed_payload_rejected():
    o = op()
    changed = op(operation_id=o.operation_id, payload={"quantity": 1000})
    assert check(changed, digest=canonical_digest(o)) == SyncDecision.REJECT


def test_rejected_untrusted_writer_and_unknown_kind():
    assert check(op(), authorized=False) == SyncDecision.REJECT
    assert check(op(kind="product.overwrite")) == SyncDecision.REJECT


def test_missing_entity_only_valid_at_initial_revision():
    assert check(op(base_revision=0), rev=None) == SyncDecision.ACCEPT
    assert check(op(base_revision=3), rev=None) == SyncDecision.CONFLICT


def test_validation_protects_money_representation():
    with pytest.raises(ValueError, match="floating"):
        canonical_digest(op(payload={"amount": 0.1}))
    with pytest.raises(ValueError, match="UUID"):
        op(operation_id="invalid")
    with pytest.raises(ValueError, match="revision"):
        op(base_revision=-1)


def test_batch_rejects_conflicting_duplicate_ids():
    o = op()
    with pytest.raises(ValueError, match="duplicate"):
        validate_batch([o, o])
    with pytest.raises(ValueError, match="conflicting"):
        validate_batch([o, op(operation_id=o.operation_id, payload={"quantity": 9})])


def test_hash_order_is_stable():
    o = op(payload={"a": 1, "b": {"z": 2, "y": 3}})
    copy = op(operation_id=o.operation_id, device_id=o.device_id,
              payload={"b": {"y": 3, "z": 2}, "a": 1})
    assert canonical_digest(o) == canonical_digest(copy)

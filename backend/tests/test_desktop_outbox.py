"""Pure contract and transactional outbox tests with no external DB."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest
from fastapi import HTTPException

from backend.desktop_sync.local_outbox import (
    payload_hash, sale_payload, operation_id,
    check_sale_replay, record_sale_outbox, create_indexes,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_stable_sale_digest_and_cents():
    a = sale_payload(data="2026-11-18", codice="001", quantita=2,
                     importo=Decimal("12.30"), canale="NEGOZIO",
                     pagamento="CONTANTI", colonna=None)
    assert a["importo_cents"] == 1230
    assert payload_hash(a) == payload_hash(dict(reversed(list(a.items()))))


def test_fractional_cent_refused():
    with pytest.raises(ValueError, match="integer cents"):
        sale_payload(data="now", codice="1", quantita=1,
                     importo="1.234", canale="NEGOZIO", pagamento="C", colonna=None)


def test_operation_id_deterministic_and_scoped():
    d1, d2 = str(uuid4()), str(uuid4())
    assert operation_id(d1, "same-key") == operation_id(d1, "same-key")
    assert operation_id(d1, "same-key") != operation_id(d2, "same-key")


@pytest.mark.anyio
async def test_atomic_insert_requires_session():
    db = MagicMock()
    db.desktop_outbox.insert_one = AsyncMock()
    with pytest.raises(RuntimeError, match="transaction"):
        await record_sale_outbox(db, session=None, op_id="x", device_id="y",
                                 digest="z", sale_id="s", event_payload={"sale_id": "s"})
    db.desktop_outbox.insert_one.assert_not_awaited()


@pytest.mark.anyio
async def test_record_payload_and_session():
    db = MagicMock()
    db.desktop_outbox.insert_one = AsyncMock()
    session = object()
    await record_sale_outbox(db, session=session, op_id="op", device_id="device",
                             digest="hash", sale_id="sale", event_payload={"sale_id": "sale"})
    doc = db.desktop_outbox.insert_one.await_args.args[0]
    assert doc["status"] == "PENDING"
    assert doc["event_payload"]["sale_id"] == "sale"
    assert db.desktop_outbox.insert_one.await_args.kwargs["session"] is session


@pytest.mark.anyio
async def test_replay_different_payload_fails():
    db = MagicMock()
    db.desktop_outbox.find_one = AsyncMock(return_value={"payload_hash": "one", "entity_id": "sale"})
    with pytest.raises(HTTPException) as exc:
        await check_sale_replay(db, op_id="op", digest="two", session=object())
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_replay_same_payload_returns_existing_sale():
    db = MagicMock()
    db.desktop_outbox.find_one = AsyncMock(return_value={"payload_hash": "same", "entity_id": "s"})
    db.vendite.find_one = AsyncMock(return_value={"id": "s", "quantita": 1})
    assert (await check_sale_replay(db, op_id="op", digest="same", session=object()))["id"] == "s"


@pytest.mark.anyio
async def test_replay_orphan_receipt_conflict():
    db = MagicMock()
    db.desktop_outbox.find_one = AsyncMock(return_value={"payload_hash": "same", "entity_id": "s"})
    db.vendite.find_one = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await check_sale_replay(db, op_id="op", digest="same", session=object())
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_unique_outbox_index():
    db = MagicMock()
    db.desktop_outbox.create_index = AsyncMock()
    await create_indexes(db)
    assert db.desktop_outbox.create_index.await_args_list[0].kwargs["unique"] is True

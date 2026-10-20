import asyncio
import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

from backend import server
from backend.smart_venue_router import SmartVenueEntryIn
from backend.tests.test_smart_venue_and_stock_correction import isolated_db


def entry(*, code="P1", quantity=3, key="entry-1", balance=7, difference=-3, adjustment=None):
    return SmartVenueEntryIn(
        codice=code, quantita=quantity, motivazione="inventario fisico",
        idempotency_key=key, saldo_atteso=balance,
        differenza_attesa=difference, rettifica_id=adjustment,
    )


def test_entry_retry_is_idempotent_before_current_state_validation(monkeypatch):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.prodotti.insert_one({"codice": "P1", "giacenza_negozio": 10})
        await database.smart_venue.insert_one({"id": "sv1", "codice": "P1", "smart_venue": 7})
        first = await server.confirm_smart_venue_entry("sv1", entry())
        second = await server.confirm_smart_venue_entry("sv1", entry())
        balance = await database.smart_venue.find_one({"id": "sv1"})
        movements = await database.smart_venue_movimenti.find({}).to_list(10)
        return first, second, balance, movements

    first, second, balance, movements = asyncio.run(scenario())
    assert first["gia_confermato"] is False
    assert second["gia_confermato"] is True
    assert balance["smart_venue"] == 10
    assert len(movements) == 1


def test_entry_reused_key_with_different_payload_is_conflict(monkeypatch):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.prodotti.insert_one({"codice": "P1", "giacenza_negozio": 10})
        await database.smart_venue.insert_one({"id": "sv1", "codice": "P1", "smart_venue": 7})
        await server.confirm_smart_venue_entry("sv1", entry())
        with pytest.raises(HTTPException) as error:
            await server.confirm_smart_venue_entry("sv1", entry(quantity=2))
        return error.value, await database.smart_venue.find_one({"id": "sv1"})

    error, balance = asyncio.run(scenario())
    assert error.status_code == 409
    assert balance["smart_venue"] == 10


def test_entry_is_blocked_while_a_load_is_pending(monkeypatch):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.prodotti.insert_one({"codice": "P1", "giacenza_negozio": 10})
        await database.smart_venue.insert_one({"id": "sv1", "codice": "P1", "smart_venue": 7})
        await database.smart_venue_movimenti.insert_one({"id": "load-1", "codice": "P1", "tipo": "CARICO_IN_ENTRATA", "stato": "PENDENTE", "quantita": 2})
        with pytest.raises(HTTPException) as error:
            await server.confirm_smart_venue_entry("sv1", entry())
        return error.value, await database.smart_venue.find_one({"id": "sv1"}), await database.smart_venue_movimenti.find({}).to_list(10)

    error, balance, movements = asyncio.run(scenario())
    assert error.status_code == 409
    assert "Storico ordini" in error.detail
    assert balance["smart_venue"] == 7
    assert len(movements) == 1


def test_adjustment_selection_is_recent_unreconciled_and_deterministic(monkeypatch):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.prodotti.insert_one({"codice": "P1", "giacenza_negozio": 10})
        await database.smart_venue.insert_one({"id": "sv1", "codice": "P1", "smart_venue": 7, "updated_at": "2026-10-20T10:00:00+00:00"})
        await database.rettifiche_inventario.insert_many([
            {"id": "old", "codice": "P1", "created_at": "2026-10-20T09:00:00+00:00", "motivazione": "superata dall'import"},
            {"id": "used", "codice": "P1", "created_at": "2026-10-20T11:00:00+00:00", "motivazione": "gia riconciliata"},
            {"id": "recent", "codice": "P1", "created_at": "2026-10-20T12:00:00+00:00", "motivazione": "prima candidata"},
            {"id": "newest", "codice": "P1", "created_at": "2026-10-20T13:00:00+00:00", "motivazione": "candidata deterministica"},
        ])
        await database.smart_venue_movimenti.insert_one({"id": "entry-used", "tipo": "ENTRATA_RETTIFICA", "stato": "CONFERMATO", "rettifica_id": "used"})
        return await server.list_smart_venue()

    rows = asyncio.run(scenario())
    assert rows[0]["stato"] == "RETTIFICA DA VERIFICARE"
    assert rows[0]["rettifica_id"] == "newest"


def test_old_or_reconciled_adjustment_does_not_mark_row_pending(monkeypatch):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.prodotti.insert_one({"codice": "P1", "giacenza_negozio": 10})
        await database.smart_venue.insert_one({"id": "sv1", "codice": "P1", "smart_venue": 7, "updated_at": "2026-10-20T10:00:00+00:00"})
        await database.rettifiche_inventario.insert_many([
            {"id": "old", "codice": "P1", "created_at": "2026-10-20T09:00:00+00:00"},
            {"id": "used", "codice": "P1", "created_at": "2026-10-20T11:00:00+00:00"},
        ])
        await database.smart_venue_movimenti.insert_one({"id": "entry-used", "tipo": "ENTRATA_RETTIFICA", "stato": "CONFERMATO", "rettifica_id": "used"})
        return await server.list_smart_venue()

    rows = asyncio.run(scenario())
    assert rows[0]["stato"] == "ENTRATA DA VERIFICARE"
    assert rows[0]["rettifica_id"] is None

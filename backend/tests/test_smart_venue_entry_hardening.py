import asyncio
import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

from backend import server
from backend.smart_venue_router import SmartVenueEntryIn, SmartVenueExitIn
from backend.tests.test_smart_venue_and_stock_correction import isolated_db


def entry(*, code="P1", quantity=3, key="entry-1", balance=7, difference=-3, adjustment=None):
    return SmartVenueEntryIn(
        codice=code, quantita=quantity, motivazione="inventario fisico",
        idempotency_key=key, saldo_atteso=balance,
        differenza_attesa=difference, rettifica_id=adjustment,
    )


def exit_operation(*, code="P1", quantity=3, key="exit-1", balance=13, difference=3, reason="vendita esterna"):
    return SmartVenueExitIn(
        codice=code, quantita=quantity, motivazione=reason,
        idempotency_key=key, saldo_atteso=balance,
        differenza_attesa=difference,
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


def test_entry_reused_key_with_different_reason_is_conflict(monkeypatch):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.prodotti.insert_one({"codice": "P1", "giacenza_negozio": 10})
        await database.smart_venue.insert_one({"id": "sv1", "codice": "P1", "smart_venue": 7})
        await server.confirm_smart_venue_entry("sv1", entry())
        changed = entry()
        changed.motivazione = "motivazione differente"
        with pytest.raises(HTTPException) as error:
            await server.confirm_smart_venue_entry("sv1", changed)
        return error.value

    assert asyncio.run(scenario()).status_code == 409


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


def test_exit_retry_is_idempotent_and_audited_once(monkeypatch):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.prodotti.insert_one({"codice": "P1", "giacenza_negozio": 10})
        await database.smart_venue.insert_one({"id": "sv1", "codice": "P1", "smart_venue": 13})
        first = await server.confirm_smart_venue_exit("sv1", exit_operation())
        second = await server.confirm_smart_venue_exit("sv1", exit_operation())
        return (
            first,
            second,
            await database.smart_venue.find_one({"id": "sv1"}),
            await database.prodotti.find_one({"codice": "P1"}),
            await database.smart_venue_movimenti.find({}).to_list(10),
            await database.audit_events.find({}).to_list(10),
        )

    first, second, balance, product, movements, audits = asyncio.run(scenario())
    assert first["gia_confermato"] is False
    assert second["gia_confermato"] is True
    assert balance["smart_venue"] == 10
    assert product["giacenza_negozio"] == 10
    assert len(movements) == 1
    assert movements[0]["differenza_successiva"] == 0
    assert len(audits) == 1
    assert audits[0]["entita"] == "SMART_VENUE_USCITA"


def test_exit_reused_key_with_different_reason_is_conflict(monkeypatch):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.prodotti.insert_one({"codice": "P1", "giacenza_negozio": 10})
        await database.smart_venue.insert_one({"id": "sv1", "codice": "P1", "smart_venue": 13})
        await server.confirm_smart_venue_exit("sv1", exit_operation())
        with pytest.raises(HTTPException) as error:
            await server.confirm_smart_venue_exit(
                "sv1", exit_operation(reason="causale differente")
            )
        return error.value, await database.smart_venue.find_one({"id": "sv1"})

    error, balance = asyncio.run(scenario())
    assert error.status_code == 409
    assert balance["smart_venue"] == 10


@pytest.mark.parametrize("quantity", [0, -1, 4])
def test_exit_rejects_invalid_or_excessive_quantity(monkeypatch, quantity):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.prodotti.insert_one({"codice": "P1", "giacenza_negozio": 10})
        await database.smart_venue.insert_one({"id": "sv1", "codice": "P1", "smart_venue": 13})
        with pytest.raises(HTTPException) as error:
            await server.confirm_smart_venue_exit(
                "sv1", exit_operation(quantity=quantity)
            )
        return error.value, await database.smart_venue.find_one({"id": "sv1"})

    error, balance = asyncio.run(scenario())
    assert error.status_code == 422
    assert balance["smart_venue"] == 13


def test_entry_and_exit_reject_stale_expected_values(monkeypatch):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.prodotti.insert_many([
            {"codice": "IN", "giacenza_negozio": 10},
            {"codice": "OUT", "giacenza_negozio": 10},
        ])
        await database.smart_venue.insert_many([
            {"id": "sv-in", "codice": "IN", "smart_venue": 7},
            {"id": "sv-out", "codice": "OUT", "smart_venue": 13},
        ])
        with pytest.raises(HTTPException) as entry_error:
            await server.confirm_smart_venue_entry(
                "sv-in", entry(code="IN", balance=6)
            )
        with pytest.raises(HTTPException) as exit_error:
            await server.confirm_smart_venue_exit(
                "sv-out", exit_operation(code="OUT", difference=2)
            )
        return entry_error.value, exit_error.value

    entry_error, exit_error = asyncio.run(scenario())
    assert entry_error.status_code == 409
    assert exit_error.status_code == 409


def test_reconciliation_diagnostic_is_read_only_and_reports_totals(monkeypatch):
    database = isolated_db(monkeypatch)

    async def scenario():
        await database.prodotti.insert_many([
            {"codice": "OK", "giacenza_negozio": 5},
            {"codice": "OUT", "giacenza_negozio": 5},
            {"codice": "IN", "giacenza_negozio": 8},
            {"codice": "VEND", "giacenza_negozio": 2},
            {"codice": "PEND", "giacenza_negozio": 7},
        ])
        await database.vending.insert_one({"id": "v1", "codice": "VEND", "giacenza": 3})
        await database.smart_venue.insert_many([
            {"id": "ok", "codice": "OK", "smart_venue": 5},
            {"id": "out", "codice": "OUT", "smart_venue": 7},
            {"id": "in", "codice": "IN", "smart_venue": 5},
            {"id": "vend", "codice": "VEND", "smart_venue": 2},
            {"id": "pend", "codice": "PEND", "smart_venue": 5},
        ])
        await database.smart_venue_movimenti.insert_one({
            "id": "load", "codice": "PEND", "tipo": "CARICO_IN_ENTRATA",
            "stato": "PENDENTE", "quantita": 2,
        })
        before = {
            "smart": [dict(row) for row in database.smart_venue.documents],
            "products": [dict(row) for row in database.prodotti.documents],
            "movements": [dict(row) for row in database.smart_venue_movimenti.documents],
        }
        result = await server.smart_venue_reconciliation_diagnostic()
        after = {
            "smart": [dict(row) for row in database.smart_venue.documents],
            "products": [dict(row) for row in database.prodotti.documents],
            "movements": [dict(row) for row in database.smart_venue_movimenti.documents],
        }
        return result, before, after

    result, before, after = asyncio.run(scenario())
    assert result["prodotti_totali"] == 5
    assert result["allineati"] == 1
    assert result["uscite_da_confermare"] == 1
    assert result["somma_uscite"] == 2
    assert result["entrate_da_verificare"] == 1
    assert result["somma_entrate"] == 3
    assert result["anomalie_vending"] == 1
    assert result["carichi_da_inserire"] == 1
    assert result["quantita_carichi_pendenti"] == 2
    assert before == after

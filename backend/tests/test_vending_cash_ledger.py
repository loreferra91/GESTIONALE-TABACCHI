import asyncio
import io
import os

from mongomock_motor import AsyncMongoMockClient
from starlette.datastructures import UploadFile

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "gestionale_test")

from backend import server


def fresh_db(monkeypatch):
    database = AsyncMongoMockClient()["vending_cash_test"]
    monkeypatch.setattr(server, "db", database)
    return database


def test_legacy_withdrawal_total_initializes_current_vending_cash(monkeypatch):
    database = fresh_db(monkeypatch)
    asyncio.run(database.prelievi_vending.insert_one({"id": "legacy", "importo": 237.55}))

    assert asyncio.run(server._vending_cash_balance()) == 237.55


def test_cash_csv_increments_vending_cash_but_cards_do_not(monkeypatch):
    database = fresh_db(monkeypatch)
    asyncio.run(database.prelievi_vending.insert_one({"id": "legacy", "importo": 237.55}))
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "04/10/2026 10:00;PRODOTTO CASH;5,8;1-A01;1001;Sigarette;Contanti\n"
        "04/10/2026 10:01;PRODOTTO CARTA;6,0;1-A02;1002;Sigarette;Carte\n"
    )
    upload = UploadFile(filename="vendite.csv", file=io.BytesIO(raw.encode()))

    result = asyncio.run(server.import_csv_vending(upload))

    assert result["contanti_aggiunti_giacenza"] == 5.8
    assert asyncio.run(server._vending_cash_balance()) == 243.35


def test_registering_and_deleting_withdrawal_updates_vending_cash(monkeypatch):
    database = fresh_db(monkeypatch)
    asyncio.run(database.prelievi_vending.insert_one({"id": "legacy", "importo": 237.55}))
    asyncio.run(server._vending_cash_balance())

    movement = asyncio.run(server.add_prelievo_vending(server.PrelievoVendingIn(
        data="2026-10-04", importo=100, descrizione="Test", operatore="Codex"
    )))
    assert asyncio.run(server._vending_cash_balance()) == 137.55

    asyncio.run(server.del_prelievo_vending(movement["id"]))
    assert asyncio.run(server._vending_cash_balance()) == 237.55


def test_receipts_increase_derived_withdrawal_without_changing_combined_balance():
    result = server._calculate_dashboard_balances(
        [(3445.30, "Contanti"), (2445, "Carte")],
        saldo_cassa=-664.72,
        giacenza_vending=237.55,
        totale_scontrini=100,
    )

    assert result["prelievoVending"] == 3307.75
    assert result["scontriniVending"] == 100
    assert result["saldoCassaNegozioEVending"] == 2543.03

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


def test_csv_import_can_be_undone_with_stock_column_and_cash_restored(monkeypatch):
    database = fresh_db(monkeypatch)
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "04/10/2026 10:00;PRODOTTO CASH;5,8;1-B02;1001;Sigarette;Contanti\n"
    )

    async def scenario():
        await database.prelievi_vending.insert_one({"id": "legacy", "importo": 100})
        await database.prodotti.insert_one({
            "id": "product-1",
            "codice": "AMMS1001",
            "descrizione": "PRODOTTO CASH",
            "giacenza_vending": 10,
            "venduti_vending": 0,
        })
        await database.vending.insert_one({"id": "column-1", "colonna": "B02", "codice": "AMMS1001", "giacenza": 5})
        upload = UploadFile(filename="vendite.csv", file=io.BytesIO(raw.encode()))
        imported = await server.import_csv_vending(upload)
        product_after_import = await database.prodotti.find_one({"id": "product-1"}, {"_id": 0})
        column_after_import = await database.vending.find_one({"id": "column-1"}, {"_id": 0})
        cash_after_import = await server._vending_cash_balance()
        latest = await server.ultimo_csv_vending()
        undone = await server.annulla_csv_vending(imported["batch_id"])
        product_after_undo = await database.prodotti.find_one({"id": "product-1"}, {"_id": 0})
        column_after_undo = await database.vending.find_one({"id": "column-1"}, {"_id": 0})
        cash_after_undo = await server._vending_cash_balance()
        remaining = await database.vendite.find({"batch_id": imported["batch_id"]}).to_list(10)
        return (
            imported, product_after_import, column_after_import, cash_after_import, latest,
            undone, product_after_undo, column_after_undo, cash_after_undo, remaining,
        )

    (
        imported, product_after_import, column_after_import, cash_after_import, latest,
        undone, product_after_undo, column_after_undo, cash_after_undo, remaining,
    ) = asyncio.run(scenario())

    assert imported["inseriti"] == 1
    assert imported["batch_id"]
    assert product_after_import["giacenza_vending"] == 4
    assert product_after_import["venduti_vending"] == 1
    assert column_after_import["giacenza"] == 4
    assert cash_after_import == 105.8
    assert latest["id"] == imported["batch_id"]
    assert undone["rimossi"] == 1
    assert undone["contanti_rimossi_giacenza"] == 5.8
    assert product_after_undo["giacenza_vending"] == 5
    assert product_after_undo["venduti_vending"] == 0
    assert column_after_undo["giacenza"] == 5
    assert cash_after_undo == 100
    assert remaining == []


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


def test_updating_opening_cash_adjusts_current_balance_by_the_delta(monkeypatch):
    database = fresh_db(monkeypatch)

    async def scenario():
        await database.parametri.insert_one({
            "nome": "GIACENZA_INIZIALE_CONTANTI_VENDING",
            "valore": 50,
        })
        await database.cassa_vending_stato.insert_one({
            "id": server.VENDING_CASH_STATE_ID,
            "giacenza": 200,
        })
        updated = await server.update_parametro(
            "GIACENZA_INIZIALE_CONTANTI_VENDING",
            server.ParametroIn(valore=125),
        )
        return updated, await server._vending_cash_balance()

    updated, balance = asyncio.run(scenario())

    assert updated["valore"] == 125
    assert balance == 275.0


def test_receipts_are_separate_from_cash_withdrawal_and_combined_balance():
    result = server._calculate_dashboard_balances(
        [(3445.30, "Contanti"), (2445, "Carte")],
        saldo_cassa=-664.72,
        giacenza_vending=237.55,
        totale_scontrini=100,
    )

    assert result["prelievoVending"] == 3207.75
    assert result["scontriniVending"] == 100
    assert result["saldoCassaNegozioEVending"] == 2543.03

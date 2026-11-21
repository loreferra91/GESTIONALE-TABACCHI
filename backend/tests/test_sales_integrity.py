import asyncio
import io

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from starlette.datastructures import UploadFile

from backend import server


def test_manual_sale_idempotency_decrements_stock_once(monkeypatch):
    database = AsyncMongoMockClient()["manual_sale_idempotency"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "id": "p1", "codice": "P1", "descrizione": "Prodotto",
            "giacenza_negozio": 10, "venduti_negozio": 0,
        })
        payload = server.VenditaIn(
            data="2026-10-18", codice="P1", quantita=2, importo=12,
        )
        first = await server.add_vendita(payload, "same-request")
        second = await server.add_vendita(payload, "same-request")
        product = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        count = await database.vendite.count_documents({})
        return first, second, product, count

    first, second, product, count = asyncio.run(scenario())
    assert first["id"] == second["id"]
    assert product["giacenza_negozio"] == 8
    assert product["venduti_negozio"] == 2
    assert count == 1


def test_manual_sale_rejects_insufficient_stock_without_writes(monkeypatch):
    database = AsyncMongoMockClient()["manual_sale_insufficient"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "id": "p1", "codice": "P1", "descrizione": "Prodotto",
            "giacenza_negozio": 1, "venduti_negozio": 0,
        })
        with pytest.raises(HTTPException) as caught:
            await server.add_vendita(server.VenditaIn(
                data="2026-10-18", codice="P1", quantita=2, importo=12,
            ))
        product = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        return caught.value, product, await database.vendite.count_documents({})

    error, product, count = asyncio.run(scenario())
    assert error.status_code == 409
    assert product["giacenza_negozio"] == 1
    assert product["venduti_negozio"] == 0
    assert count == 0


def test_pos_batch_is_atomic_and_idempotent(monkeypatch):
    database = AsyncMongoMockClient()["pos_batch_atomicity"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_many([
            {"id": "p1", "codice": "P1", "descrizione": "Uno", "prezzo": 2.50, "giacenza_negozio": 3, "venduti_negozio": 0},
            {"id": "p2", "codice": "P2", "descrizione": "Due", "prezzo": 4.00, "giacenza_negozio": 0, "venduti_negozio": 0},
        ])
        body = server.CassaRapidaIn(
            data="2026-10-18T16:00:00Z", pagamento="CONTANTI",
            idempotency_key="pos-request-001",
            righe=[
                {"codice": "P1", "quantita": 1, "prezzo": 2.50},
                {"codice": "P2", "quantita": 1, "prezzo": 4.00},
            ],
        )
        with pytest.raises(RuntimeError, match="Giacenza insufficiente"):
            await server.vendita_cassa_rapida(body)
        failed = {
            "product": await database.prodotti.find_one({"codice": "P1"}, {"_id": 0}),
            "sales": await database.vendite.count_documents({}),
            "cash": await database.cassa.count_documents({}),
        }
        await database.prodotti.update_one({"codice": "P2"}, {"$set": {"giacenza_negozio": 1}})
        first = await server.vendita_cassa_rapida(body)
        second = await server.vendita_cassa_rapida(body)
        return failed, first, second, await database.prodotti.find_one({"codice": "P1"}, {"_id": 0}), await database.vendite.count_documents({}), await database.cassa.count_documents({})

    failed, first, second, product, sales, cash = asyncio.run(scenario())
    assert failed["product"]["giacenza_negozio"] == 3
    assert failed["sales"] == failed["cash"] == 0
    assert first["id"] == second["id"]
    assert first["totale"] == 6.5
    assert product["giacenza_negozio"] == 2
    assert product["venduti_negozio"] == 1
    assert sales == 2
    assert cash == 1


def test_pos_batch_rejects_idempotency_key_reuse_with_different_payload(monkeypatch):
    database = AsyncMongoMockClient()["pos_batch_fingerprint"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "id": "p1", "codice": "P1", "descrizione": "Uno", "prezzo": 2,
            "giacenza_negozio": 5, "venduti_negozio": 0,
        })
        first = server.CassaRapidaIn(
            data="2026-10-18T16:00:00Z", idempotency_key="pos-fingerprint-001",
            righe=[{"codice": "P1", "quantita": 1, "prezzo": 2}],
        )
        changed = server.CassaRapidaIn(
            data="2026-10-18T16:00:00Z", idempotency_key="pos-fingerprint-001",
            righe=[{"codice": "P1", "quantita": 2, "prezzo": 2}],
        )
        await server.vendita_cassa_rapida(first)
        with pytest.raises(HTTPException) as caught:
            await server.vendita_cassa_rapida(changed)
        product = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        return caught.value, product, await database.vendite.count_documents({})

    error, product, count = asyncio.run(scenario())
    assert error.status_code == 409
    assert product["giacenza_negozio"] == 4
    assert count == 1


def test_pos_electronic_balance_and_cancellation_are_consistent(monkeypatch):
    database = AsyncMongoMockClient()["pos_cancel"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "id": "p1", "codice": "P1", "descrizione": "Uno", "prezzo": 3,
            "giacenza_negozio": 5, "venduti_negozio": 0,
        })
        batch = await server.vendita_cassa_rapida(server.CassaRapidaIn(
            data="2026-10-19T10:00:00Z", pagamento="POS",
            idempotency_key="pos-electronic-001",
            righe=[{"codice": "P1", "quantita": 2, "prezzo": 3}],
        ))
        before = await server.list_cassa()
        cancelled = await server.annulla_vendita_cassa_rapida(batch["id"])
        after = await server.list_cassa()
        product = await database.prodotti.find_one({"codice": "P1"}, {"_id": 0})
        return batch, before, cancelled, after, product

    batch, before, cancelled, after, product = asyncio.run(scenario())
    assert batch["pagamento"] == "POS"
    assert before["saldo_contanti"] == 0
    assert before["saldo_elettronico"] == before["pagamenti"]["POS"] == 6
    assert cancelled["righe_annullate"] == 1
    assert after["saldo_elettronico"] == 0
    assert product["giacenza_negozio"] == 5
    assert product["venduti_negozio"] == 0


def test_manual_sale_rejects_ambiguous_idempotency_key(monkeypatch):
    database = AsyncMongoMockClient()["manual_sale_bad_idempotency"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "id": "p1", "codice": "P1", "descrizione": "Prodotto",
            "giacenza_negozio": 10, "venduti_negozio": 0,
        })
        with pytest.raises(HTTPException) as caught:
            await server.add_vendita(server.VenditaIn(
                data="2026-10-18", codice="P1", quantita=1, importo=5,
            ), "short")
        return caught.value, await database.vendite.count_documents({})

    error, count = asyncio.run(scenario())
    assert error.status_code == 422
    assert count == 0


def test_generic_delete_refuses_imported_sale(monkeypatch):
    database = AsyncMongoMockClient()["source_aware_delete"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.vendite.insert_one({
            "id": "csv-sale", "sorgente": "CSV_VENDING", "batch_id": "batch-1",
        })
        with pytest.raises(HTTPException) as caught:
            await server.del_vendita("csv-sale")
        return caught.value, await database.vendite.count_documents({})

    error, count = asyncio.run(scenario())
    assert error.status_code == 409
    assert count == 1


def test_csv_missing_column_is_rejected_without_accounting(monkeypatch):
    database = AsyncMongoMockClient()["csv_missing_column"]
    monkeypatch.setattr(server, "db", database)
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "18/10/2026 10:00;PRODOTTO;5,8;1-A01;1001;Sigarette;Contanti\n"
    )

    async def scenario():
        await database.prodotti.insert_one({
            "id": "p1", "codice": "AMMS1001", "descrizione": "PRODOTTO",
            "giacenza_vending": 0, "venduti_vending": 0,
        })
        with pytest.raises(HTTPException) as caught:
            await server.import_csv_vending(UploadFile(
                filename="missing.csv", file=io.BytesIO(raw.encode())
            ))
        return (
            caught.value,
            await database.vendite.count_documents({}),
            await database.vending_accounting_events.count_documents({}),
        )

    error, sales, events = asyncio.run(scenario())
    assert error.status_code == 409
    assert "A01" in str(error.detail)
    assert sales == 0
    assert events == 0


def test_csv_runtime_mismatch_rolls_back_entire_batch(monkeypatch):
    database = AsyncMongoMockClient()["csv_runtime_atomicity"]
    monkeypatch.setattr(server, "db", database)
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "18/10/2026 10:00;PRIMO;5,0;1-A01;1001;Sigarette;Contanti\n"
        "18/10/2026 10:01;SECONDO;6,0;1-A02;1002;Sigarette;Contanti\n"
    )

    async def scenario():
        for code, name in (("AMMS1001", "PRIMO"), ("AMMS1002", "SECONDO")):
            await database.prodotti.insert_one({
                "id": code, "codice": code, "descrizione": name,
                "giacenza_vending": 5, "venduti_vending": 0,
            })
        await database.vending.insert_many([
            {"id": "c1", "colonna": "A01", "codice": "AMMS1001", "giacenza": 5,
             "giacenza_aggiornata_il": "2026-10-18T07:00:00+00:00"},
            {"id": "c2", "colonna": "A02", "codice": "AMMS9999", "giacenza": 5,
             "giacenza_aggiornata_il": "2026-10-18T07:00:00+00:00"},
        ])
        with pytest.raises(HTTPException) as caught:
            await server.import_csv_vending(UploadFile(
                filename="mismatch.csv", file=io.BytesIO(raw.encode())
            ))
        products = await database.prodotti.find({}, {"_id": 0}).sort("codice", 1).to_list(None)
        columns = await database.vending.find({}, {"_id": 0}).sort("colonna", 1).to_list(None)
        return caught.value, products, columns, await database.vendite.count_documents({})

    error, products, columns, sales = asyncio.run(scenario())
    assert error.status_code == 409
    assert [item["venduti_vending"] for item in products] == [0, 0]
    assert [item["giacenza"] for item in columns] == [5, 5]
    assert sales == 0


def test_csv_accounting_failure_compensates_stock_sales_and_cash(monkeypatch):
    database = AsyncMongoMockClient()["csv_finalization_atomicity"]
    monkeypatch.setattr(server, "db", database)
    raw = (
        "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
        "18/10/2026 10:00;PRODOTTO;5,0;1-A01;1001;Sigarette;Contanti\n"
    )

    async def fail_accounting(*_args, **_kwargs):
        raise RuntimeError("accounting unavailable")

    monkeypatch.setattr(server, "_store_vending_accounting_snapshot", fail_accounting)

    async def scenario():
        await database.prodotti.insert_one({
            "id": "p1", "codice": "AMMS1001", "descrizione": "PRODOTTO",
            "giacenza_vending": 5, "venduti_vending": 0,
        })
        await database.vending.insert_one({
            "id": "c1", "colonna": "A01", "codice": "AMMS1001", "giacenza": 5,
            "giacenza_aggiornata_il": "2026-10-18T07:00:00+00:00",
        })
        with pytest.raises(HTTPException) as caught:
            await server.import_csv_vending(UploadFile(
                filename="failure.csv", file=io.BytesIO(raw.encode())
            ))
        product = await database.prodotti.find_one({"id": "p1"}, {"_id": 0})
        column = await database.vending.find_one({"id": "c1"}, {"_id": 0})
        return (
            caught.value, product, column,
            await database.vendite.count_documents({}),
            await server._vending_cash_balance(),
        )

    error, product, column, sales, cash = asyncio.run(scenario())
    assert error.status_code == 500
    assert product["venduti_vending"] == 0
    assert product["giacenza_vending"] == 5
    assert column["giacenza"] == 5
    assert sales == 0
    assert cash == 0


def test_manual_sale_rejects_key_reuse_with_different_payload(monkeypatch):
    database = AsyncMongoMockClient()["manual_sale_fingerprint_guard"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1", "descrizione": "Prodotto",
            "giacenza_negozio": 10, "venduti_negozio": 0,
        })
        body = server.VenditaIn(data="2026-11-19", codice="P1", quantita=2, importo=10)
        first = await server.add_vendita(body, "same-manual-req-001")
        assert (await server.add_vendita(body, "same-manual-req-001"))["id"] == first["id"]
        changed = server.VenditaIn(data="2026-11-19", codice="P1", quantita=3, importo=15)
        with pytest.raises(HTTPException) as error:
            await server.add_vendita(changed, "same-manual-req-001")
        assert error.value.status_code == 409
        product = await database.prodotti.find_one({"codice": "P1"})
        assert product["giacenza_negozio"] == 8
        assert product["venduti_negozio"] == 2
        assert await database.vendite.count_documents({}) == 1

    asyncio.run(scenario())


def test_manual_legacy_key_replay_checks_original_business_fields(monkeypatch):
    database = AsyncMongoMockClient()["manual_legacy_key_guard"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.vendite.insert_one({
            "id": "existing-sale", "sorgente": "MANUALE", "idempotency_key": "legacy-manual-req",
            "data": "2026-11-19", "codice": "P1", "quantita": 2,
            "importo": 10, "canale": "NEGOZIO", "pagamento": "CONTANTI",
        })
        body = server.VenditaIn(data="2026-11-19", codice="P1", quantita=2, importo=10)
        assert (await server.add_vendita(body, "legacy-manual-req"))["id"] == "existing-sale"
        with pytest.raises(HTTPException) as error:
            await server.add_vendita(
                server.VenditaIn(data="2026-11-19", codice="P1", quantita=3, importo=15),
                "legacy-manual-req",
            )
        assert error.value.status_code == 409
        assert await database.vendite.count_documents({}) == 1

    asyncio.run(scenario())


def test_manual_undo_preserves_original_sale_in_audit(monkeypatch):
    from types import SimpleNamespace

    database = AsyncMongoMockClient()["manual_undo_append_only_audit"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1", "descrizione": "Prodotto",
            "giacenza_negozio": 10, "venduti_negozio": 0,
        })
        body = server.VenditaIn(
            data="2026-11-19", codice="P1", quantita=3, importo=15,
        )
        sale = await server.add_vendita(body, "cert-manual-undo-key-001")
        request = SimpleNamespace(state=SimpleNamespace(authenticated_user="cert-operator"))
        result = await server.annulla_vendita_manuale(sale["id"], request)
        assert result["rimossi"] == 1
        product = await database.prodotti.find_one({"codice": "P1"})
        assert product["giacenza_negozio"] == 10
        assert product["venduti_negozio"] == 0
        assert await database.vendite.count_documents({}) == 0
        audit = await database.audit_events.find_one({
            "azione": "annulla-vendita-manuale", "entita_id": sale["id"]
        }, {"_id": 0})
        assert audit["operatore"] == "cert-operator"
        assert audit["prima"]["id"] == sale["id"]
        assert audit["prima"]["quantita"] == 3
        assert audit["idempotency_key"] == "cert-manual-undo-key-001"
        with pytest.raises(HTTPException) as replay_error:
            await server.add_vendita(body, "cert-manual-undo-key-001")
        assert replay_error.value.status_code == 409
        with pytest.raises(HTTPException) as error:
            await server.annulla_vendita_manuale(sale["id"])
        assert error.value.status_code == 404
        assert await database.audit_events.count_documents({
            "azione": "annulla-vendita-manuale"
        }) == 1

    asyncio.run(scenario())


def test_manual_undo_blocks_missing_sold_counter_without_stock_change(monkeypatch):
    database = AsyncMongoMockClient()["manual_undo_counter_guard"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1", "descrizione": "Prodotto",
            "giacenza_negozio": 10, "venduti_negozio": 0,
        })
        sale = await server.add_vendita(server.VenditaIn(
            data="2026-11-19", codice="P1", quantita=3, importo=15,
        ))
        await database.prodotti.update_one(
            {"codice": "P1"}, {"$set": {"venduti_negozio": 0}}
        )
        with pytest.raises(HTTPException) as error:
            await server.annulla_vendita_manuale(sale["id"])
        assert error.value.status_code == 409
        product = await database.prodotti.find_one({"codice": "P1"})
        assert product["giacenza_negozio"] == 7
        assert product["venduti_negozio"] == 0
        assert await database.vendite.count_documents({}) == 1
        assert await database.audit_events.count_documents({}) == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_quantity", [True, 1.5, "1,5", "1.1", -1, 0, "1e2"])
def test_pos_rejects_fractional_or_invalid_quantities(bad_quantity, monkeypatch):
    database = AsyncMongoMockClient()["pos_strict_quantity"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1", "descrizione": "Prova",
            "prezzo": 5, "giacenza_negozio": 10, "venduti_negozio": 0,
        })
        with pytest.raises(HTTPException) as exc:
            await server.vendita_cassa_rapida(server.CassaRapidaIn(
                data="2026-11-19T10:00:00Z", idempotency_key="cert-invalid-pos-quantity",
                righe=[{"codice": "P1", "quantita": bad_quantity, "prezzo": 5}],
            ))
        assert exc.value.status_code == 422
        product = await database.prodotti.find_one({"codice": "P1"})
        assert product["giacenza_negozio"] == 10
        assert product["venduti_negozio"] == 0
        assert await database.vendite.count_documents({}) == 0
        assert await database.cassa.count_documents({}) == 0

    asyncio.run(scenario())


def test_pos_undo_keeps_immutable_cash_and_sales_snapshot_and_revokes_key(monkeypatch):
    database = AsyncMongoMockClient()["pos_undo_audit"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1", "descrizione": "Prova",
            "prezzo": 5, "giacenza_negozio": 10, "venduti_negozio": 0,
        })
        body = server.CassaRapidaIn(
            data="2026-11-19T10:00:00Z", idempotency_key="cert-audited-pos-001",
            righe=[
                {"codice": "P1", "quantita": 2, "prezzo": 5},
                {"codice": "P1", "quantita": 1, "prezzo": 5},
            ],
        )
        batch = await server.vendita_cassa_rapida(body)
        result = await server.annulla_vendita_cassa_rapida(batch["id"])
        assert result["righe_annullate"] == 2
        snapshot = await database.audit_events.find_one({
            "azione": "annulla-vendita-cassa-rapida", "batch_id": batch["id"]
        }, {"_id": 0})
        assert snapshot["prima"]["batch"]["id"] == batch["id"]
        assert len(snapshot["prima"]["vendite"]) == 2
        assert snapshot["prima"]["movimento_cassa"]["importo"] == 15
        assert snapshot["idempotency_key"] == "cert-audited-pos-001"
        saved_batch = await database.cassa_rapida_batches.find_one({"id": batch["id"]})
        assert saved_batch["status"] == "annullata"
        assert saved_batch["undo_audit_version"] == 1
        product = await database.prodotti.find_one({"codice": "P1"})
        assert product["giacenza_negozio"] == 10
        assert product["venduti_negozio"] == 0
        assert await database.cassa.count_documents({}) == 0
        assert await database.vendite.count_documents({}) == 0
        with pytest.raises(HTTPException) as replay:
            await server.vendita_cassa_rapida(body)
        assert replay.value.status_code == 409
        with pytest.raises(HTTPException) as repeated:
            await server.annulla_vendita_cassa_rapida(batch["id"])
        assert repeated.value.status_code in (404, 409)

    asyncio.run(scenario())


@pytest.mark.parametrize("corruption", ["missing_cash", "missing_line", "sold_counter", "wrong_amount"])
def test_pos_undo_refuses_inconsistent_ledgers_without_changes(monkeypatch, corruption):
    database = AsyncMongoMockClient()[f"pos_inconsistency_{corruption}"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1", "descrizione": "Prova",
            "prezzo": 5, "giacenza_negozio": 10, "venduti_negozio": 0,
        })
        batch = await server.vendita_cassa_rapida(server.CassaRapidaIn(
            data="2026-11-19T10:00:00Z", idempotency_key="cert-pos-corrupt-001",
            righe=[{"codice": "P1", "quantita": 3, "prezzo": 5}],
        ))
        if corruption == "missing_cash":
            await database.cassa.delete_many({})
        elif corruption == "missing_line":
            await database.vendite.delete_many({})
        elif corruption == "sold_counter":
            await database.prodotti.update_one(
                {"codice": "P1"}, {"$set": {"venduti_negozio": 0}}
            )
        else:
            await database.cassa.update_one(
                {"batch_id": batch["id"]}, {"$set": {"importo": 11}}
            )
        before = {
            name: await database[name].find({}, {"_id": 0}).to_list(None)
            for name in ("prodotti", "vendite", "cassa")
        }
        with pytest.raises(HTTPException) as exc:
            await server.annulla_vendita_cassa_rapida(batch["id"])
        assert exc.value.status_code == 409
        after = {
            name: await database[name].find({}, {"_id": 0}).to_list(None)
            for name in ("prodotti", "vendite", "cassa")
        }
        assert before == after
        assert await database.audit_events.count_documents({}) == 0
        saved = await database.cassa_rapida_batches.find_one({"id": batch["id"]})
        assert saved["status"] == "active"

    asyncio.run(scenario())

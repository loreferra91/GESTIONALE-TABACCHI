import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bson import BSON
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient
from openpyxl import Workbook
from pydantic import ValidationError

from backend import server
from backend.money import money, money_decimal, money_product, money_sum, nonnegative_money


@pytest.mark.parametrize("raw,expected", [
    ("1.005", 1.01), ("2.675", 2.68), ("-1.005", -1.01),
    ("0,105", 0.11), (0.1 + 0.2, 0.3), ("-0.004", 0.0),
    ("999999999999.99", 999999999999.99),
])
def test_half_up_and_historical_numbers(raw, expected):
    assert money(raw) == expected
    assert money_decimal(money(raw)) == money_decimal(raw)


@pytest.mark.parametrize("raw", [None, True, False, "NaN", "Infinity", "-Infinity", "1e9999", "1000000000000", "abc", {}, []])
def test_invalid_money_is_rejected(raw):
    with pytest.raises(ValueError):
        money(raw)


def test_negative_subcent_is_not_rounded_into_a_valid_price():
    with pytest.raises(ValueError):
        nonnegative_money("-0.001")


def test_large_exact_sum_and_numeric_serialization():
    assert money_sum(["0.10"] * 30001) == 3000.1
    assert money_product("1.005", 3) == 3.03
    document = server.VenditaIn(data="2026-10-18", codice="P1", importo="2.675").model_dump()
    assert document["importo"] == 2.68
    assert isinstance(document["importo"], float)
    assert BSON(BSON.encode(document)).decode()["importo"] == 2.68
    assert json.loads(json.dumps(document))["importo"] == 2.68


@pytest.mark.parametrize("model,extra,field", [
    (server.VenditaIn, {"codice": "P1", "data": "2026-10-18"}, "importo"),
    (server.ProdottoIn, {"codice": "P1", "descrizione": "P"}, "prezzo"),
    (server.OrdineIn, {"codice": "P1", "data": "2026-10-18", "quantita": 1, "idempotency_key": "money-boundary-order"}, "prezzo"),
    (server.VersamentoIn, {"data": "2026-10-18"}, "importo"),
    (server.PrelievoVendingIn, {"data": "2026-10-18"}, "importo"),
    (server.ScontrinoVendingIn, {"data": "2026-10-18"}, "importo"),
])
def test_all_write_boundaries_validate_and_normalize(model, extra, field):
    assert getattr(model(**extra, **{field: "1.005"}), field) == 1.01
    for value in ("NaN", "Infinity", True, "-0.001"):
        with pytest.raises(ValidationError):
            model(**extra, **{field: value})


@pytest.fixture
def database(monkeypatch):
    database = AsyncMongoMockClient()["phase6"]
    monkeypatch.setattr(server, "db", database)
    return database


def test_totals_are_not_limited_by_display_page(database):
    async def scenario():
        await database.versamenti.insert_many([{"importo": 0.105, "data": "2026-10-18"} for _ in range(25001)])
        return await server._versamenti_summary(limit=1)
    result = asyncio.run(scenario())
    assert len(result["movimenti"]) == 1
    assert result["totale"] == 2750.11


def test_cash_cas_retries_a_concurrent_write(database, monkeypatch):
    async def scenario():
        collection = database.cassa_vending_stato
        monkeypatch.setattr(server, "db", SimpleNamespace(cassa_vending_stato=collection))
        await collection.insert_one({"id": "saldo", "giacenza": 0.1})
        original = collection.update_one
        first = True

        async def concurrent_update(query, update, **kwargs):
            nonlocal first
            if first:
                first = False
                await original({"id": "saldo"}, {"$set": {"giacenza": 0.2}})
            return await original(query, update, **kwargs)

        monkeypatch.setattr(collection, "update_one", concurrent_update)
        value = await server._adjust_vending_cash_balance(0.1)
        return value, await database.cassa_vending_stato.find_one({"id": "saldo"})
    value, document = asyncio.run(scenario())
    assert value == document["giacenza"] == 0.3


def test_pivot_rounds_discounted_lines_and_sums_displayed_values(database):
    async def scenario():
        await database.parametri.insert_one({"nome": "AGGIO_PCT", "valore": 0.1})
        await database.prodotti.insert_many([
            {"codice": code, "prezzo": 0.05, "acquistati": 1, "giacenza_negozio": 1,
             "presente_ultimo_import": code == "A"}
            for code in ("A", "B")
        ])
        return await server.pivot()
    result = asyncio.run(scenario())
    assert [row["tot_acquistato"] for row in result["righe"]] == [0.05, 0.05]
    assert {row["codice"] for row in result["righe"]} == {"A", "B"}
    assert result["kpi"]["valore_acquistato"] == 0.1
    assert result["kpi"]["valore_giacenza"] == 0.1
    json.dumps(result, allow_nan=False)


def test_excel_import_price_and_total_use_same_normalization(database):
    wb = Workbook()
    ws = wb.active
    ws.append(["header"])
    ws.append(["header"])
    row = [None] * 18
    row[0], row[1], row[3], row[7] = "P1", "Prodotto", 3, 1.005
    ws.append(row)

    async def scenario():
        report = await server._import_prodotti(ws)
        return report, await database.prodotti.find_one({"codice": "P1"})
    report, product = asyncio.run(scenario())
    assert product["prezzo"] == 1.01
    assert report["valore_venduto_negozio_excel"] == 3.03


def test_csv_fractional_cent_preserves_legacy_idempotency(database):
    raw = "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n18/10/2026 10:00;P;0,015;A01;1001;Sigarette;Contanti\n"
    row = server._parse_csv_vending(raw)["righe"][0]
    assert row["prezzo"] == 0.02
    legacy = {key: value for key, value in row.items() if key != "_identity_price"}
    legacy["prezzo"] = 0.015
    server._assign_csv_vending_event_keys([legacy])
    server._assign_csv_vending_event_keys([row])
    assert row["csv_event_key"] == legacy["csv_event_key"]
    assert row["csv_accounting_key"] == legacy["csv_accounting_key"]

    async def scenario():
        await database.vendite.insert_one({
            **legacy, "importo": 0.015, "sorgente": "CSV_VENDING",
        })
        return await server._exclude_imported_csv_vending_rows([row])
    new, duplicates = asyncio.run(scenario())
    assert new == []
    assert duplicates == 1


def test_reconciliation_detects_real_errors_and_does_not_write(database):
    async def scenario():
        await database.prodotti.insert_one({"codice": "P", "prezzo": 1.005, "giacenza_negozio": -1, "giacenza_vending": 4})
        await database.vending.insert_many([
            {"codice": "P", "giacenza": 2}, {"codice": "MISSING", "giacenza": 1},
        ])
        await database.vendite.insert_one({"id": "s", "sorgente": "CSV_VENDING", "csv_event_key": "key", "importo": 1.01, "pagamento": "Carte"})
        await database.vending_accounting_events.insert_one({"id": "e", "csv_event_key": "key", "importo": 1, "pagamento": "Contanti"})
        names = await database.list_collection_names()
        before = {name: await database[name].find({}).to_list(None) for name in names}
        result = await server.accounting_reconciliation()
        after = {name: await database[name].find({}).to_list(None) for name in names}
        assert before == after
        assert await database.vending_accounting_state.count_documents({}) == 0
        return result
    result = asyncio.run(scenario())
    assert result["conteggi"] == {"FRAZIONI_CENTESIMO": 1, "GIACENZA_INVALIDA": 1, "COLONNA_ORFANA": 1, "GIACENZA_VENDING": 1, "IMPORTO_PAGAMENTO_CSV": 1}
    assert result["vendite_csv_confrontate"] == 1


def test_reconciliation_does_not_infer_unproven_history_relationships(database):
    async def scenario():
        await database.prodotti.insert_one({"codice": "P", "prezzo": 0.1 + 0.2, "acquistati": 100, "venduti_negozio": 999, "giacenza_negozio": 7, "giacenza_vending": 2})
        await database.vending.insert_one({"codice": "P", "giacenza": 2})
        # Baseline sale without matching event; accounting-only event without sale.
        await database.vendite.insert_one({"sorgente": "CSV_VENDING", "importo": 5, "csv_event_key": "old"})
        await database.vending_accounting_events.insert_one({"importo": 8, "csv_event_key": "accounting-only"})
        await database.cassa_vending_stato.insert_one({"id": "saldo", "giacenza": -100})
        await database.rettifiche_venduti.insert_one({"campo": "venduti_negozio"})
        return await server.accounting_reconciliation()
    result = asyncio.run(scenario())
    assert result["totale_segnalazioni"] == 0
    assert result["vendite_csv_non_confrontabili"] == 1


def test_reconciliation_counts_all_findings_and_caps_only_details(database):
    async def scenario():
        await database.versamenti.insert_many([{"importo": "NaN"} for _ in range(115)])
        return await server.accounting_reconciliation()
    result = asyncio.run(scenario())
    assert result["totale_segnalazioni"] == result["documenti_esaminati"]["versamenti"] == 115
    assert len(result["dettagli"]) == 100
    assert result["dettagli_omessi"] == 15
    json.dumps(result, allow_nan=False)


def test_reconciliation_skips_ambiguous_csv_events(database):
    async def scenario():
        await database.vending_accounting_events.insert_many([
            {"csv_accounting_key": "k", "importo": value} for value in (1, 2)
        ])
        await database.vendite.insert_one({"sorgente": "CSV_VENDING", "csv_accounting_key": "k", "importo": 3})
        return await server.accounting_reconciliation()
    result = asyncio.run(scenario())
    assert result["conteggi"] == {"IDENTITA_CSV_DUPLICATA": 1}
    assert result["vendite_csv_confrontate"] == 0
    assert result["vendite_csv_non_confrontabili"] == 1


def test_reconciliation_date_range_is_validated_and_applied(database):
    async def scenario():
        await database.versamenti.insert_many([
            {"id": "old", "data": "2026-10-01", "importo": 1},
            {"id": "new", "data": "2026-10-18", "importo": 2},
        ])
        result = await server.accounting_reconciliation(date_from="2026-10-18", date_to="2026-10-18")
        with pytest.raises(Exception):
            await server.accounting_reconciliation(date_from="18/10/2026")
        return result
    result = asyncio.run(scenario())
    assert result["intervallo"] == {"da": "2026-10-18", "a": "2026-10-18"}
    assert result["documenti_esaminati"].get("versamenti") == 1


def test_reconciliation_case_workflow_is_separate_stable_and_audited(database):
    async def scenario():
        await database.versamenti.insert_one({"id": "bad-amount", "data": "2026-10-18", "importo": "NaN"})
        first = await server.accounting_reconciliation()
        finding = first["dettagli"][0]
        saved = await server.update_reconciliation_case(
            finding["fingerprint"],
            server.ReconciliationCaseIn(
                stato="in_verifica",
                nota="Controllare il documento originale",
                tipo=finding["tipo"],
                raccolta=finding["raccolta"],
                riferimento=finding["riferimento"],
            ),
            SimpleNamespace(state=SimpleNamespace(authenticated_user="lorenzo")),
        )
        second = await server.accounting_reconciliation()
        source = await database.versamenti.find_one({"id": "bad-amount"}, {"_id": 0})
        audit = await database.audit_events.find_one(
            {"entita": "SEGNALAZIONE_RICONCILIAZIONE"}, {"_id": 0}
        )
        return first, finding, saved, second, source, audit

    first, finding, saved, second, source, audit = asyncio.run(scenario())
    assert first["conteggi_stato"] == {"aperta": 1}
    assert len(finding["fingerprint"]) == 64
    assert saved["stato"] == "in_verifica"
    assert saved["operatore"] == "lorenzo"
    assert second["dettagli"][0]["fingerprint"] == finding["fingerprint"]
    assert second["dettagli"][0]["nota"] == "Controllare il documento originale"
    assert second["conteggi_stato"] == {"in_verifica": 1}
    assert second["totale_da_gestire"] == 1
    assert source["importo"] == "NaN"
    assert audit["entita_id"] == finding["fingerprint"]
    assert audit["dopo"]["stato"] == "in_verifica"


def test_parameter_change_is_audited(database):
    async def scenario():
        await database.parametri.insert_one({"nome": "AGGIO_PCT", "valore": 0.1})
        await server.update_parametro("AGGIO_PCT", server.ParametroIn(valore=0.2))
        return await database.audit_events.find_one({"entita": "PARAMETRO"}, {"_id": 0})
    audit = asyncio.run(scenario())
    assert audit["entita_id"] == "AGGIO_PCT"
    assert audit["prima"]["valore"] == 0.1
    assert audit["dopo"]["valore"] == 0.2


def test_http_money_validation_and_protected_reconciliation(database, monkeypatch):
    monkeypatch.delenv("APP_USERNAME", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "test")
    client = TestClient(server.app)
    for bad in ("NaN", "Infinity", True, "-0.001"):
        response = client.post("/api/versamenti", json={"data": "2026-10-18", "importo": bad})
        assert response.status_code == 422
    assert client.post("/api/versamenti", json={"data": "2026-10-18", "importo": "1.005"}).json()["importo"] == 1.01
    assert client.put("/api/parametri/AGGIO_PCT", json={"valore": 0.1}).status_code == 200
    assert client.put("/api/parametri/AGGIO_PCT", json={"valore": 1.5}).status_code == 422
    assert {item["nome"] for item in client.get("/api/parametri").json()} == {"AGGIO_PCT"}
    assert client.get("/api/import/history", params={"limit": 1}).status_code == 200
    assert client.get("/api/data-status").status_code == 200
    assert client.get("/api/global-search", params={"q": "x"}).json() == {"items": []}
    assert client.get("/api/anomalie").status_code == 200
    assert client.get("/api/contabilita/riconciliazione").status_code == 200
    fingerprint = "c" * 64
    workflow_response = client.put(
        f"/api/contabilita/riconciliazione/segnalazioni/{fingerprint}",
        json={"stato": "in_verifica", "nota": "Verifica route estratta"},
    )
    assert workflow_response.status_code == 200
    assert workflow_response.json()["fingerprint"] == fingerprint
    audit_response = client.get(
        "/api/audit/eventi",
        params={"entita": "SEGNALAZIONE_RICONCILIAZIONE", "entita_id": fingerprint},
    )
    assert audit_response.status_code == 200
    assert [item["entita_id"] for item in audit_response.json()] == [fingerprint]
    assert client.put(
        "/api/contabilita/riconciliazione/segnalazioni/non-valido",
        json={"stato": "aperta"},
    ).status_code == 422
    monkeypatch.setenv("APP_USERNAME", "operator")
    monkeypatch.setenv("APP_PASSWORD", "test-only")
    assert client.get("/api/contabilita/riconciliazione").status_code == 401


def test_accounting_writes_are_idempotent(database):
    async def scenario():
        versamento = server.VersamentoIn(data="2026-10-18", importo="10.00", idempotency_key="versamento-001")
        first_v = await server.add_versamento(versamento)
        second_v = await server.add_versamento(versamento)
        scontrino = server.ScontrinoVendingIn(data="2026-10-18", importo="3.00", idempotency_key="scontrino-001")
        first_s = await server.add_scontrino_vending(scontrino)
        second_s = await server.add_scontrino_vending(scontrino)
        cassa = server.MovimentoIn(data="2026-10-18", tipo="ENTRATA", importo="5.00", idempotency_key="cassa-key-001")
        first_c = await server.add_cassa(cassa)
        second_c = await server.add_cassa(cassa)
        return first_v, second_v, first_s, second_s, first_c, second_c

    first_v, second_v, first_s, second_s, first_c, second_c = asyncio.run(scenario())
    assert first_v["id"] == second_v["id"]
    assert first_s["id"] == second_s["id"]
    assert first_c["id"] == second_c["id"]
    assert asyncio.run(database.versamenti.count_documents({})) == 1
    assert asyncio.run(database.scontrini_vending.count_documents({})) == 1
    assert asyncio.run(database.cassa.count_documents({})) == 1
    audit = asyncio.run(database.audit_events.find({}, {"_id": 0}).to_list(None))
    assert {row["entita"] for row in audit} == {"VERSAMENTO", "SCONTRINO_VENDING", "MOVIMENTO_CASSA"}
    assert len(audit) == 3


def test_reconciliation_detects_bulk_ledger_gaps_read_only(database):
    async def scenario():
        await database.vendite_bulk_imports.insert_many([
            {"id": "B1", "status": "active", "inseriti": 2},
            {"id": "B3", "status": "annulled", "inseriti": 2, "undo_audit_version": 1},
        ])
        await database.vendite.insert_many([
            {"id": "S1", "sorgente": "BULK", "batch_id": "B1",
             "data": "2026-11-19", "quantita": 1, "importo": 5},
            {"id": "S2", "sorgente": "BULK", "batch_id": "B2",
             "data": "2026-11-19", "quantita": 1, "importo": 5},
        ])
        await database.audit_events.insert_one({
            "id": "A1", "azione": "annulla-vendita-bulk", "batch_id": "B3",
        })
        before = {
            name: await database[name].find({}).to_list(None)
            for name in await database.list_collection_names()
        }
        result = await server.accounting_reconciliation()
        after = {
            name: await database[name].find({}).to_list(None)
            for name in await database.list_collection_names()
        }
        assert before == after
        assert result["conteggi"] == {
            "BULK_BATCH_INCOERENTE": 1,
            "BULK_BATCH_ORFANO": 1,
            "BULK_STORNO_AUDIT_INCOMPLETO": 1,
        }
        filtered = await server.accounting_reconciliation(
            date_from="2026-11-19", date_to="2026-11-19",
        )
        assert not any(key.startswith("BULK_") for key in filtered["conteggi"])
    asyncio.run(scenario())


def test_reconciliation_rejects_impossible_iso_calendar_dates(database):
    async def scenario():
        for field, value in (
            ("date_from", "2026-02-30"),
            ("date_to", "2026-13-01"),
        ):
            with pytest.raises(Exception) as exc:
                await server.accounting_reconciliation(**{field: value})
            assert getattr(exc.value, "status_code", None) == 422
    asyncio.run(scenario())


def test_reconciliation_matches_native_pos_sales_and_cash_without_writing(database):
    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1", "prezzo": 5,
            "giacenza_negozio": 10, "giacenza_vending": 0, "venduti_negozio": 0,
        })
        batch = await server.vendita_cassa_rapida(server.CassaRapidaIn(
            data="2026-11-19T10:00:00Z", pagamento="POS",
            idempotency_key="cert-recon-pos-key-001",
            righe=[{"codice": "P1", "quantita": 2, "prezzo": 5}],
        ))
        before = {
            name: await database[name].find({}).to_list(None)
            for name in await database.list_collection_names()
        }
        report = await server.accounting_reconciliation()
        after = {
            name: await database[name].find({}).to_list(None)
            for name in await database.list_collection_names()
        }
        assert report["conteggi"] == {}
        assert before == after
        undone = await server.annulla_vendita_cassa_rapida(batch["id"])
        assert undone["righe_annullate"] == 1
        after_undo = await server.accounting_reconciliation()
        assert after_undo["conteggi"] == {}
        filtered = await server.accounting_reconciliation(
            date_from="2026-11-19", date_to="2026-11-19"
        )
        assert not any(key.startswith("POS_") for key in filtered["conteggi"])

    asyncio.run(scenario())


def test_reconciliation_detects_native_pos_cash_gap_and_storno_audit_gap(database):
    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1", "prezzo": 5,
            "giacenza_negozio": 10, "giacenza_vending": 0, "venduti_negozio": 0,
        })
        batch = await server.vendita_cassa_rapida(server.CassaRapidaIn(
            data="2026-11-19T10:00:00Z", pagamento="CONTANTI",
            idempotency_key="cert-recon-pos-key-002",
            righe=[{"codice": "P1", "quantita": 1, "prezzo": 5}],
        ))
        await database.cassa.delete_one({"batch_id": batch["id"]})
        broken_cash = await server.accounting_reconciliation()
        assert broken_cash["conteggi"]["POS_CASSA_INCOERENTE"] == 1
        await database.cassa.insert_one({
            "id": batch["movimento_cassa_id"], "batch_id": batch["id"],
            "tipo": "ENTRATA", "importo": 5, "pagamento": "CONTANTI",
            "data": "2026-11-19",
        })
        await server.annulla_vendita_cassa_rapida(batch["id"])
        await database.audit_events.delete_one({
            "azione": "annulla-vendita-cassa-rapida", "batch_id": batch["id"],
        })
        broken_audit = await server.accounting_reconciliation()
        assert broken_audit["conteggi"] == {"POS_AUDIT_STORNO_INCOERENTE": 1}

    asyncio.run(scenario())


def test_reconciliation_identifies_orphaned_pos_sales_without_guessing_cash(database):
    async def scenario():
        await database.vendite.insert_one({
            "id": "cert-orphan-pos-sale", "sorgente": "POS",
            "batch_id": "MISSING", "data": "2026-11-19",
            "codice": "P", "quantita": 2, "importo": 10,
            "pagamento": "CONTANTI",
        })
        report = await server.accounting_reconciliation()
        assert report["conteggi"] == {"POS_VENDITE_ORFANE": 1}
        assert await database.vendite.count_documents({}) == 1
        assert await database.cassa.count_documents({}) == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "collection,creator,model,remover,audit_entity",
    [
        ("cassa", "add_cassa", "MovimentoIn", "del_cassa", "MOVIMENTO_CASSA"),
        ("versamenti", "add_versamento", "VersamentoIn", "del_versamento", "VERSAMENTO"),
        ("scontrini_vending", "add_scontrino_vending", "ScontrinoVendingIn",
         "del_scontrino_vending", "SCONTRINO_VENDING"),
    ],
)
def test_cash_deletions_keep_exactly_one_audit_snapshot(
    database, collection, creator, model, remover, audit_entity,
):
    async def scenario():
        body = getattr(server, model)(**{
            "data": "2026-11-19", "importo": "12.30",
            "idempotency_key": f"cert-delete-{collection}-001",
            **({"tipo": "ENTRATA"} if collection == "cassa" else {}),
        })
        saved = await getattr(server, creator)(body)
        request = SimpleNamespace(
            state=SimpleNamespace(authenticated_user="test-operator")
        )
        assert (await getattr(server, remover)(saved["id"], request))["ok"] is True
        assert (await getattr(server, remover)(saved["id"], request))["ok"] is True
        assert await database[collection].count_documents({}) == 0
        events = await database.audit_events.find({
            "entita": audit_entity, "azione": "ELIMINATO"
        }, {"_id": 0}).to_list(None)
        assert len(events) == 1
        assert events[0]["operatore"] == "test-operator"
        assert events[0]["prima"]["id"] == saved["id"]
        assert events[0]["prima"]["importo"] == 12.3
        assert events[0]["dopo"] is None

    asyncio.run(scenario())


def test_pos_cash_cannot_be_deleted_without_cancelling_sale_batch(database):
    async def scenario():
        await database.prodotti.insert_one({
            "codice": "P1", "prezzo": 5, "giacenza_negozio": 9,
            "venduti_negozio": 0,
        })
        batch = await server.vendita_cassa_rapida(server.CassaRapidaIn(
            data="2026-11-19T10:00:00Z", pagamento="CONTANTI",
            idempotency_key="cert-protected-pos-cash-001",
            righe=[{"codice": "P1", "quantita": 2, "prezzo": 5}],
        ))
        with pytest.raises(HTTPException) as error:
            await server.del_cassa(batch["movimento_cassa_id"])
        assert error.value.status_code == 409
        assert await database.cassa.count_documents({}) == 1
        assert await database.vendite.count_documents({}) == 1
        assert (await database.prodotti.find_one({"codice": "P1"}))["giacenza_negozio"] == 7
        assert (await server.annulla_vendita_cassa_rapida(batch["id"]))["ok"] is True

    asyncio.run(scenario())


def test_cash_deletion_compensates_when_audit_insert_fails(database, monkeypatch):
    async def scenario():
        await database.cassa.insert_one({
            "id": "movement-to-keep", "tipo": "ENTRATA", "importo": 12.3,
            "pagamento": "CONTANTI", "data": "2026-11-19",
        })
        async def reject_audit(*args, **kwargs):
            raise RuntimeError("synthetic-audit-error")
        monkeypatch.setattr(database.audit_events, "insert_one", reject_audit)
        with pytest.raises(RuntimeError, match="synthetic-audit-error"):
            await server.del_cassa("movement-to-keep")
        assert await database.cassa.count_documents({"id": "movement-to-keep"}) == 1

    asyncio.run(scenario())

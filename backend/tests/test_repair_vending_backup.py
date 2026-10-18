import copy

from backend.repair_vending_backup import rebuild_backup


def test_rebuild_backup_deduplicates_overlapping_exports_and_preserves_operational_data(tmp_path):
    header = "Data;Nome prodotto;Prezzo;Colonna;Codice AAMS;Categoria;Pagamento\n"
    first = tmp_path / "first.csv"
    first.write_text(
        header
        + "08/10/2026 09:12;B;6,0;1-C03;1002;Sigarette;Contanti\n"
        + "07/10/2026 23:58;A;5,8;1-B02;1001;Sigarette;Carte\n",
        encoding="utf-8",
    )
    second = tmp_path / "second.csv"
    second.write_text(
        header
        + "09-10-2026 10:00:05;C;4,5;1-D04;1003;Sigarette;PagoBancomat\n"
        + "08-10-2026 09:12:37;B;6,0;1-C03;1002;Sigarette;Contanti\n"
        + "07-10-2026 23:58:16;A;5,8;1-B02;1001;Sigarette;Carte\n",
        encoding="utf-8",
    )
    backup = {
        "format": "gestionale-tabacchi-backup",
        "version": 1,
        "counts": {},
        "collections": {
            "prodotti": [{"id": "p1", "venduti_vending": 7}],
            "vending": [{"id": "v1", "giacenza": 3}],
            "vendite": [{"id": "sale1", "importo": 6.0}],
            "cassa_vending_stato": [{"id": "saldo", "giacenza": 10.0}],
            "vendite_bulk_imports": [{
                "id": "batch1", "sorgente": "CSV_VENDING", "status": "active"
            }],
            "vending_accounting_snapshots": [{"id": "old", "status": "active"}],
            "vending_accounting_state": [{"id": "ledger"}],
            "vending_accounting_events": [{"id": "broken"}],
        },
    }
    protected_before = {
        name: copy.deepcopy(backup["collections"][name])
        for name in ("prodotti", "vending", "vendite", "cassa_vending_stato")
    }

    repaired, summary = rebuild_backup(backup, [first, second])

    assert summary["events"] == 3
    assert summary["payment_amounts"] == {
        "CARTE": 5.8,
        "CONTANTI": 6.0,
        "PAGOBANCOMAT": 4.5,
    }
    assert summary["total"] == 16.3
    assert len(repaired["collections"]["vending_accounting_events"]) == 3
    assert len({
        event["csv_accounting_key"]
        for event in repaired["collections"]["vending_accounting_events"]
    }) == 3
    assert repaired["collections"]["vending_accounting_state"][0]["accounting_identity_version"] == 2
    assert repaired["collections"]["vendite_bulk_imports"][0]["status"] == "finalized"
    for name, expected in protected_before.items():
        assert repaired["collections"][name] == expected

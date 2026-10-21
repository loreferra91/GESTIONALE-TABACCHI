import sys
from types import SimpleNamespace

from backend.adm_router import (
    _adm_product_update_operations,
    _exact_duplicate_listino_ids,
    _fit_listino_update,
    build_fit_preview,
    build_hybrid_tariff_snapshot_sync,
)
from backend.fit_tariff import (
    extract_fit_tariff_info,
    normalize_fit_code,
    parse_fit_money,
    parse_fit_xls_bytes,
)


def test_extract_fit_tariff_info_selects_newest_complete_workbook():
    html = """
      <a href="/index.php/download?url=/images/XLS/20260901/Completo_20260901.xls">XLS</a>
      <a href="/index.php/download?url=/images/XLS/20261014/Completo_20261014.xls">XLS</a>
      <a href="/index.php/download?url=/images/XLS/20261014/Modificato_20261014.xls">XLS</a>
    """

    result = extract_fit_tariff_info(html)

    assert result["effective_date"] == "2026-10-14"
    assert result["download_url"].endswith("/images/XLS/20261014/Completo_20261014.xls")


def test_fit_scalar_normalization_preserves_codes_and_decimal_prices():
    assert normalize_fit_code(338.0) == "338"
    assert normalize_fit_code("NEW 021846") == "21846"
    assert parse_fit_money(5.8) == 5.8
    assert parse_fit_money("5,80") == 5.8


def test_parse_fit_xls_uses_detected_headers_and_effective_filename_date(monkeypatch):
    header = [
        "Categoria", "Codice", "Denominazione commerciale",
        "Prezzo per KG (€)", "Prezzo per confezione (€)", "Tipo di confezione",
    ]
    rows = [
        [None, "I prezzi sono in vigore dal 14 OTTOBRE 2026", None, None, None, None],
        ["Elenco aggiornato al 13/10/2026", None, None, None, None, None],
        header,
    ]
    rows.extend([
        ["Sigarette", float(index), f"PRODOTTO {index}", 290.0, 5.8, "Confezione da 20 pezzi"]
        for index in range(1, 101)
    ])

    class FakeSheet:
        nrows = len(rows)

        @staticmethod
        def row_values(index):
            return rows[index]

    class FakeWorkbook:
        @staticmethod
        def sheet_names():
            return ["NomeFoglio"]

        @staticmethod
        def sheet_by_index(_index):
            return FakeSheet()

        @staticmethod
        def release_resources():
            return None

    monkeypatch.setitem(
        sys.modules,
        "xlrd",
        SimpleNamespace(open_workbook=lambda **_kwargs: FakeWorkbook()),
    )
    result = parse_fit_xls_bytes(b"fake", {
        "download_url": "https://example.test/Completo_20261014.xls",
        "effective_date": "2026-10-14",
        "sha256": "abc",
    })

    assert result["row_count"] == 100
    assert result["effective_date"] == "2026-10-14"
    assert result["updated_date"] == "2026-10-13"
    assert result["rows"][0]["codice"] == "AMMS1"
    assert result["rows"][0]["prezzo"] == 5.8
    assert result["categories"] == {"SIGARETTE": 100}


def test_fit_preview_updates_only_unique_exact_adm_links():
    rows = [
        {"adm_codice": "1", "codice": "AMMS1", "descrizione": "Uno", "prezzo": 6.0, "categoria_adm": "SIGARETTE"},
        {"adm_codice": "2", "codice": "AMMS2", "descrizione": "Due", "prezzo": 5.0, "categoria_adm": "SIGARETTE"},
    ]
    products = [
        {"id": "p1", "codice": "AMMS1", "descrizione": "Uno", "prezzo": 5.5},
        {"id": "p2", "codice": "AMMS2", "descrizione": "Due A", "prezzo": 5.0},
        {"id": "p3", "codice": "AMMS2", "descrizione": "Due B", "prezzo": 5.0},
        {"id": "local", "codice": "10", "descrizione": "Accessorio", "prezzo": 1.0},
    ]

    result = build_fit_preview(rows, products, [])

    assert result["prodotti_collegati"] == 1
    assert result["prezzi_modificati"] == 1
    assert result["collegamenti_ambigui"] == 2
    assert result["variazioni"][0]["prezzo_nuovo"] == 6.0


def test_fit_preview_does_not_treat_internal_numeric_codes_as_adm_links():
    rows = [{
        "adm_codice": "10", "codice": "AMMS10", "descrizione": "Tabacco",
        "prezzo": 6.0, "categoria_adm": "SIGARETTE",
    }]
    products = [{
        "id": "accessory", "codice": "10", "descrizione": "Accendini",
        "prezzo": 1.0, "categoria_adm": "ACCESSORI",
    }]

    result = build_fit_preview(rows, products, [])

    assert result["prodotti_collegati"] == 0
    assert result["prezzi_modificati"] == 0


def test_legacy_adm_product_update_does_not_touch_same_numbered_accessory():
    """Regression test: an accessory coded e.g. "938" must not be mistaken
    for the ADM tariff product that happens to share the numeric code 938
    (only the explicit AMMS938 local code may be updated).
    """
    rows = [{
        "adm_codice": "938", "codice": "AMMS938", "descrizione": "REX BRAVO (TABACCO ADM)",
        "confezione": "", "prezzo": 8.9, "categoria_adm": "ALTRI TABACCHI DA FUMO",
        "fonte": "ADM", "adm_pdf_url": "https://example.test/altri-tabacchi.pdf",
        "adm_aggiornato_il": None,
    }]
    product_codes = {"938"}  # Local accessory "REX BRAVO CARTINE CORTE" uses the bare code.

    operations = _adm_product_update_operations(rows, product_codes)

    assert operations == []


def test_legacy_adm_product_update_matches_explicit_amms_code():
    rows = [{
        "adm_codice": "938", "codice": "AMMS938", "descrizione": "REX BRAVO (TABACCO ADM)",
        "confezione": "", "prezzo": 8.9, "categoria_adm": "ALTRI TABACCHI DA FUMO",
        "fonte": "ADM", "adm_pdf_url": "https://example.test/altri-tabacchi.pdf",
        "adm_aggiornato_il": None,
    }]
    product_codes = {"AMMS938"}

    operations = _adm_product_update_operations(rows, product_codes)

    assert len(operations) == 1
    assert operations[0]._filter == {"codice": "AMMS938"}


def test_fit_preview_reports_every_listino_field_change():
    rows = [{
        "adm_codice": "1", "codice": "AMMS1", "descrizione": "Nuovo nome",
        "prezzo": 6.0, "confezione": "Conf. da 20", "categoria_adm": "SIGARETTE",
        "fonte": "FIT",
    }, {
        "adm_codice": "2", "codice": "AMMS2", "descrizione": "Nuovo articolo",
        "prezzo": 7.0, "confezione": "Conf. da 10", "categoria_adm": "SIGARI",
        "fonte": "FIT",
    }]
    existing = [{
        "codice": "AMMS1", "descrizione": "Vecchio nome", "prezzo": 5.5,
        "confezione": "Conf. da 20", "categoria_adm": "", "fonte": "",
    }]

    result = build_fit_preview(rows, [], existing)

    assert result["listino_nuovi"] == 1
    assert result["listino_aggiornati"] == 1
    assert result["listino_codici_adm_compilati"] == 1
    assert result["listino_categorie_compilate"] == 1
    assert result["listino_fonti_compilate"] == 1
    updated = next(item for item in result["listino_variazioni"] if item["stato"] == "AGGIORNATO")
    assert {item["campo"] for item in updated["campi"]} == {
        "adm_codice", "descrizione", "categoria_adm", "fonte", "prezzo",
    }


def test_fit_listino_update_has_no_mongo_path_conflicts():
    item = {
        "adm_codice": "1", "codice": "AMMS1", "descrizione": "Prodotto",
        "prezzo": 6.0, "confezione": "Conf. da 20", "categoria_adm": "SIGARETTE",
        "fonte": "FIT",
    }

    update = _fit_listino_update(item, "hash", "2026-10-21T10:00:00+00:00")

    assert not (set(update["$set"]) & set(update.get("$setOnInsert", {})))
    assert update["$set"]["adm_codice"] == "1"


def test_hybrid_snapshot_replaces_equal_or_newer_adm_categories():
    fit = {
        "effective_date": "2026-10-14",
        "rows": [
            {"adm_codice": "1", "descrizione": "Vecchio", "prezzo": 5, "categoria_adm": "SIGARI", "fonte": "FIT"},
            {"adm_codice": "2", "descrizione": "Resta", "prezzo": 6, "categoria_adm": "SIGARETTE", "fonte": "FIT"},
        ],
    }
    categories = [
        {"categoria": "SIGARI", "aggiornato_il": "14/10/2026", "url": "https://example.test/sigari.pdf"},
        {"categoria": "SIGARETTE", "aggiornato_il": "13/10/2026", "url": "https://example.test/sigarette.pdf"},
    ]

    result = build_hybrid_tariff_snapshot_sync(
        lambda: fit,
        lambda: categories,
        lambda category: [{
            "adm_codice": "3", "descrizione": "Nuovo", "prezzo": 7,
            "categoria_adm": category["categoria"], "fonte": "ADM",
        }],
    )

    assert {item["adm_codice"] for item in result["rows"]} == {"2", "3"}
    assert [item["categoria"] for item in result["adm_overlays"]] == ["SIGARI"]


def test_fast_fit_snapshot_skips_pdf_parsing():
    fit = {
        "effective_date": "2026-10-14",
        "rows": [{
            "adm_codice": "1", "descrizione": "Prodotto", "prezzo": 5,
            "categoria_adm": "SIGARI", "fonte": "FIT",
        }],
    }
    categories = [{
        "categoria": "SIGARI", "aggiornato_il": "19/10/2026",
        "url": "https://example.test/sigari.pdf",
    }]

    result = build_hybrid_tariff_snapshot_sync(
        lambda: fit,
        lambda: categories,
        lambda _category: (_ for _ in ()).throw(AssertionError("PDF parser called")),
        include_adm_overlays=False,
    )

    assert result["rows"] == fit["rows"]
    assert result["adm_overlays"] == []
    assert "SIGARI" in result["warnings"][0]


def test_exact_duplicate_listino_rows_are_safe_to_remove():
    duplicate_ids, conflicts = _exact_duplicate_listino_ids({
        "21645": [
            {"_id": "old", "codice": "AMMS21645", "descrizione": "VIRTO", "prezzo": 5.5,
             "confezione": "Conf. da 20", "categoria_adm": "", "fonte": ""},
            {"_id": "copy", "codice": "AMMS21645", "descrizione": "VIRTO", "prezzo": 5.5,
             "confezione": "Conf. da 20", "categoria_adm": "", "fonte": ""},
        ],
    })

    assert duplicate_ids == ["copy"]
    assert conflicts == []


def test_non_identical_duplicate_listino_rows_require_manual_review():
    duplicate_ids, conflicts = _exact_duplicate_listino_ids({
        "21645": [
            {"_id": "old", "codice": "AMMS21645", "descrizione": "VIRTO", "prezzo": 5.5,
             "confezione": "Conf. da 20", "categoria_adm": "", "fonte": ""},
            {"_id": "different", "codice": "AMMS21645", "descrizione": "VIRTO", "prezzo": 6,
             "confezione": "Conf. da 20", "categoria_adm": "", "fonte": ""},
        ],
    })

    assert duplicate_ids == []
    assert conflicts == ["21645"]

import asyncio
from types import SimpleNamespace

from mongomock_motor import AsyncMongoMockClient

import backend.server as server


def test_numeric_adm_alias_matches_equivalent_description():
    aliases = server._adm_alias_index([
        ("AMMS96", "CHESTERFIELD ORIGINAL KS *AST 20"),
    ])

    assert server._canonical_product_code(
        "96", "CHESTERFIELD ORIGINAL (BOX RED)", aliases
    ) == "AMMS96"


def test_numeric_accessory_is_not_confused_with_same_numbered_adm_product():
    aliases = server._adm_alias_index([
        ("AMMS913", "DIANA SSL BLU *AST 20"),
    ])

    assert server._canonical_product_code(
        "913", "RIZLA SILVER KINGSIZE SLIM", aliases
    ) == "913"


def test_common_blue_blu_spelling_variant_is_matched():
    aliases = server._adm_alias_index([
        ("AMMS201", "CHESTERFIELD BLU KS *AST 20"),
    ])

    assert server._canonical_product_code(
        "201", "CHESTERFIELD BLUE (BOX)", aliases
    ) == "AMMS201"


def test_same_code_and_brand_matches_legacy_commercial_name():
    aliases = server._adm_alias_index([
        ("AMMS395", "MARLBORO KS *AST 20"),
    ])

    assert server._canonical_product_code(
        "395", "MARLBORO RED KS (BOX)", aliases
    ) == "AMMS395"


def test_amms_codes_are_normalized_consistently():
    assert server._canonical_product_code("amms0096", "anything", {}) == "AMMS96"


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def iter_rows(self, **_kwargs):
        return iter(self.rows)


class _Collection:
    def __init__(self):
        self.operations = []
        self.deleted = []

    async def bulk_write(self, operations, ordered):
        self.operations.extend(operations)
        return SimpleNamespace(upserted_count=1, matched_count=0)

    async def update_many(self, *args):
        return SimpleNamespace(modified_count=0)

    async def delete_many(self, query):
        self.deleted.append(query)


class _Db(SimpleNamespace):
    def __getitem__(self, name):
        return getattr(self, name)


def _product_row(code, description, acquired, sold_shop, remaining, vending_stock, sold_vending):
    row = [None] * 18
    row[0] = code
    row[1] = description
    row[2] = acquired
    row[3] = sold_shop
    row[6] = remaining
    row[7] = 5.8
    row[13] = vending_stock
    row[17] = sold_vending
    return tuple(row)


def test_import_prefers_canonical_snapshot_over_stale_alias(monkeypatch):
    products = _Collection()
    fake_db = _Db(
        prodotti=products,
        vendite=_Collection(),
        db_storico_vend=_Collection(),
        storico_ordini=_Collection(),
        vending=_Collection(),
        ordini_fornitore_righe=_Collection(),
    )
    monkeypatch.setattr(server, "db", fake_db)

    async def no_categories():
        return 0

    monkeypatch.setattr(server, "_apply_electronic_inhalation_categories", no_categories)
    aliases = server._adm_alias_index([("AMMS96", "CHESTERFIELD ORIGINAL KS *AST 20")])
    rows = _Rows([
        _product_row("96", "CHESTERFIELD ORIGINAL (BOX RED)", 2, 3, 7, 1, 1),
        _product_row("AMMS96", "CHESTERFIELD ORIGINAL KS *AST20", 5, 7, 12, 2, 2),
    ])

    result = asyncio.run(server._import_prodotti(rows, aliases))

    assert result["alias_unificati"] == 1
    assert len(products.operations) == 1
    merged = products.operations[0]._doc["$set"]
    insert_defaults = products.operations[0]._doc["$setOnInsert"]
    assert merged["codice"] == "AMMS96"
    assert merged["acquistati"] == 5
    assert merged["venduti_negozio"] == 7
    assert merged["venduti_vending"] == 2
    assert merged["giacenza_vending"] == 2
    assert merged["giacenza_negozio"] == 8
    assert insert_defaults["id"]
    assert insert_defaults["created_at"]
    assert products.deleted == [{"codice": {"$in": ["96"]}}]


def test_ensure_product_ids_repairs_only_legacy_products(monkeypatch):
    database = AsyncMongoMockClient()["product_identity_test"]
    monkeypatch.setattr(server, "db", database)

    async def scenario():
        await database.prodotti.insert_many([
            {"codice": "LEGACY-MISSING", "descrizione": "Senza ID"},
            {"codice": "LEGACY-EMPTY", "descrizione": "ID vuoto", "id": ""},
            {"codice": "CURRENT", "descrizione": "Con ID", "id": "keep-me"},
        ])

        changed = await server.ensure_product_ids()
        products = await database.prodotti.find({}, {"_id": 0}).to_list(10)
        changed_again = await server.ensure_product_ids()
        return changed, changed_again, {product["codice"]: product for product in products}

    changed, changed_again, products = asyncio.run(scenario())

    assert changed == 2
    assert changed_again == 0
    assert products["LEGACY-MISSING"]["id"]
    assert products["LEGACY-EMPTY"]["id"]
    assert products["LEGACY-MISSING"]["id"] != products["LEGACY-EMPTY"]["id"]
    assert products["CURRENT"]["id"] == "keep-me"

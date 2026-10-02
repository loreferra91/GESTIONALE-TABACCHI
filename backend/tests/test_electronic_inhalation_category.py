import pytest

import backend.server as server


@pytest.mark.parametrize(
    "desc,code,expected",
    [
        ("ELFBAR EB1000 PINK LEMONADE", "10013", server.ELECTRONIC_INHALATION_CATEGORY),
        ("ELFLIQ EB6000 - SHAKING COLA", "10011", server.ELECTRONIC_INHALATION_CATEGORY),
        ("LOST MARY TP800 LEMON LIME 20 mg/ml", "10010", server.ELECTRONIC_INHALATION_CATEGORY),
        ("KIWI GO strawberry ice", "931", server.ELECTRONIC_INHALATION_CATEGORY),
        ("ELFA TURBP POD RICAR", "919", server.ELECTRONIC_INHALATION_CATEGORY),
        ("TEREA AMBER", "AMMS20146", "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE"),
        ("ACCENDINI", "10", "ACCESSORI"),
        ("MARLBORO GOLD", "AMMS123", "SIGARETTE"),
    ],
)
def test_cat_from_desc_electronic_brands(desc, code, expected):
    assert server._cat_from_desc(desc, code) == expected


def test_lotto_for_electronic_inhalation():
    params = {
        "LOTTO_SIGARETTE": 10,
        "LOTTO_INALAZIONE_SENZA_COMBUSTIONE": 10,
        "LOTTO_ELETTRONICHE": 5,
        "LOTTO_ACCESSORI": 1,
    }
    assert server.lotto_for(server.ELECTRONIC_INHALATION_CATEGORY, params) == 5
    assert server.lotto_for("PRODOTTI DA INALAZIONE SENZA COMBUSTIONE", params) == 10
    assert server.lotto_for("ACCESSORI", params) == 1


def test_lotto_for_inhalation_without_combustion_has_independent_default():
    assert server.lotto_for("PRODOTTI DA INALAZIONE SENZA COMBUSTIONE", {}) == 10


def test_every_adm_category_has_a_dedicated_lot_parameter():
    adm_categories = set(server.ADM_CATEGORY_ALIASES)
    configured_categories = set(server.LOTTO_PARAM_BY_CATEGORY)
    assert adm_categories <= configured_categories

    lot_params = {
        server.LOTTO_PARAM_BY_CATEGORY[category][0]
        for category in adm_categories
    }
    assert len(lot_params) == len(adm_categories)
    assert lot_params <= set(server.DEFAULT_PARAMS)
    assert lot_params <= set(server.PARAM_BOUNDS)


@pytest.mark.parametrize(
    "category,param_name,lot",
    [
        ("SIGARETTE", "LOTTO_SIGARETTE", 10),
        ("SIGARI", "LOTTO_SIGARI", 2),
        ("SIGARETTI", "LOTTO_SIGARETTI", 3),
        ("FIUTO E MASTICO", "LOTTO_FIUTO_E_MASTICO", 4),
        ("TRINCIATI PER SIGARETTA", "LOTTO_TRINCIATI_PER_SIGARETTA", 5),
        ("ALTRI TABACCHI DA FUMO", "LOTTO_ALTRI_TABACCHI_DA_FUMO", 6),
        (
            "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE",
            "LOTTO_INALAZIONE_SENZA_COMBUSTIONE",
            7,
        ),
    ],
)
def test_lotto_for_each_adm_category(category, param_name, lot):
    assert server.lotto_for(category, {param_name: lot}) == lot

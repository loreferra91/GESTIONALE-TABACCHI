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
    params = {"LOTTO_SIGARETTE": 10, "LOTTO_ELETTRONICHE": 5, "LOTTO_ACCESSORI": 1}
    assert server.lotto_for(server.ELECTRONIC_INHALATION_CATEGORY, params) == 5
    assert server.lotto_for("PRODOTTI DA INALAZIONE SENZA COMBUSTIONE", params) == 5
    assert server.lotto_for("ACCESSORI", params) == 1

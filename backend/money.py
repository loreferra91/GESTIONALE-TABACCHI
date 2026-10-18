"""EUR boundaries: Decimal arithmetic, numeric JSON/BSON, no data migration."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Annotated

from pydantic import BeforeValidator

CENT = Decimal("0.01")
# Keep cents representable after conversion to the existing BSON double format.
MAX_AMOUNT = Decimal("999999999999.99")


def money_decimal(value, *, nonnegative=False):
    if isinstance(value, bool) or value is None:
        raise ValueError("Importo monetario non valido")
    try:
        amount = Decimal(str(value).strip().replace(",", "."))
        if not amount.is_finite() or abs(amount) > MAX_AMOUNT:
            raise ValueError("Importo non finito o fuori limite")
        if nonnegative and amount < 0:
            raise ValueError("L'importo non può essere negativo")
        result = amount.quantize(CENT, rounding=ROUND_HALF_UP)
        return result if result else Decimal("0.00")
    except InvalidOperation as exc:
        raise ValueError("Importo monetario non valido") from exc


def money(value):
    return float(money_decimal(value))


def nonnegative_money(value):
    return float(money_decimal(value, nonnegative=True))


def money_sum(values):
    """Normalize each historical monetary entry, then sum exactly."""
    return money(sum((money_decimal(value) for value in values), Decimal(0)))


def money_product(price, quantity):
    return money(money_decimal(price) * Decimal(str(quantity)))


Money = Annotated[float, BeforeValidator(money)]
NonNegativeMoney = Annotated[float, BeforeValidator(nonnegative_money)]

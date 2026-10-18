"""Read-only product ranking and inventory pivot routes."""
import asyncio
from decimal import Decimal

from fastapi import APIRouter


def create_inventory_analytics_router(
    *,
    get_database,
    cap_limit,
    get_params,
    vending_stock_by_code,
    apply_vending_stock,
    money,
    money_decimal,
):
    router = APIRouter()

    @router.get("/prodotti/top")
    async def prodotti_top(limit: int = 40):
        products = await get_database().prodotti.find({}, {"_id": 0}).to_list(5000)
        for product in products:
            product["venduti_totale"] = (
                (product.get("venduti_negozio") or 0)
                + (product.get("venduti_vending") or 0)
            )
        products.sort(key=lambda item: -item["venduti_totale"])
        return products[:cap_limit(limit)]

    @router.get("/pivot")
    async def pivot():
        database = get_database()
        products, params, vending_totals = await asyncio.gather(
            database.prodotti.find(
                {"presente_ultimo_import": {"$ne": False}}, {"_id": 0}
            ).to_list(5000),
            get_params(),
            vending_stock_by_code(),
        )
        apply_vending_stock(products, vending_totals)
        divisor = params.get("VENDITE_GIORNALIERE_MESE", 6.5)
        aggio = params.get("AGGIO_PCT", 0.10)
        cost_factor = max(Decimal(0), Decimal(1) - Decimal(str(aggio)))
        rows = []
        total_purchased = total_sold = total_stock = stock_units = 0
        stationary = negatives = reorder = slow = 0
        for product in products:
            sold_shop = product.get("venduti_negozio", 0) or 0
            sold_vending = product.get("venduti_vending", 0) or 0
            sold_total = sold_shop + sold_vending
            stock_shop = product.get("giacenza_negozio", 0) or 0
            stock_vending = product.get("giacenza_vending", 0) or 0
            stock_total = stock_shop + stock_vending
            price = money_decimal(product.get("prezzo", 0) or 0)
            purchase_price = price * cost_factor
            purchased = product.get("acquistati", 0) or 0
            # Round each line so KPI totals equal the displayed line totals.
            purchased_value = money_decimal(purchased * purchase_price)
            sold_value = money_decimal(sold_total * price)
            stock_value = money_decimal(stock_total * purchase_price)
            monthly_sales = sold_total / max(1, divisor) if divisor else 0
            disposal_months = (
                round(stock_total / monthly_sales, 2) if monthly_sales > 0 else 0
            )
            status = "OK"
            action = "OK"
            if stock_total <= 0:
                status = "ESAURITO"
                action = "RIORDINO"
            elif sold_total == 0 and purchased > 0:
                status = "FERMO"
                action = "REVISIONE"
                stationary += 1
            elif disposal_months > 6:
                status = "LENTO"
                action = "REVISIONE"
                slow += 1
            if stock_total < 0:
                negatives += 1
            if status == "ESAURITO":
                reorder += 1
            total_purchased += purchased_value
            total_sold += sold_value
            total_stock += stock_value
            stock_units += stock_total
            rows.append({
                "codice": product["codice"],
                "descrizione": product.get("descrizione", ""),
                "categoria": product.get("categoria", ""),
                "acq": purchased,
                "vend_negozio": sold_shop,
                "vend_vending": sold_vending,
                "vend_totale": sold_total,
                "giac_negozio": stock_shop,
                "giac_vending": stock_vending,
                "giac_totale": stock_total,
                "prezzo": money(price),
                "tot_acquistato": money(purchased_value),
                "tot_venduto": money(sold_value),
                "tot_giacenza": money(stock_value),
                "vendite_mese": round(monthly_sales, 2),
                "mesi_smaltimento": disposal_months,
                "stato": status,
                "azione": action,
            })
        rows.sort(key=lambda item: -item["tot_giacenza"])
        return {
            "kpi": {
                "valore_acquistato": money(total_purchased),
                "valore_venduto": money(total_sold),
                "valore_giacenza": money(total_stock),
                "margine_lordo": money(total_sold - total_purchased),
                "aggio_pct": round(aggio, 4),
                "pezzi_magazzino": stock_units,
                "prodotti_totali": len(rows),
                "prodotti_fermi": stationary,
                "giacenza_negativa": negatives,
                "da_riordinare": reorder,
                "lenti_oltre_6_mesi": slow,
            },
            "righe": rows,
        }

    return router, prodotti_top, pivot

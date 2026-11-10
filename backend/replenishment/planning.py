"""Daily availability and periodic-review order quantities."""
from __future__ import annotations

import math
from datetime import date, timedelta

try:
    from ..auto_order_planning import commercial_day, rounded_order_quantity
except ImportError:  # direct ASGI module import from backend/
    from auto_order_planning import commercial_day, rounded_order_quantity


def plan_inventory(*, today: date, shop_stock: int, vending_stock: int,
                   demand_shop: float, demand_vending: float, receipts: list[dict],
                   minimum_shop: int, lead_days: int, review_days: int,
                   lot: int, safety_factor: float = 1.15) -> dict:
    if lot <= 0 or int(lot) != lot or lead_days < 0 or review_days < 0:
        raise ValueError("Lotto o calendario di riordino non valido")
    if shop_stock < 0 or vending_stock < 0 or minimum_shop < 0:
        raise ValueError("Giacenza o minimo negativo")
    if demand_shop < 0 or demand_vending < 0 or safety_factor < 1:
        raise ValueError("Domanda o sicurezza non valida")
    protection = lead_days + review_days if lead_days else max(1, review_days)
    supplier_day = today + timedelta(days=lead_days)
    arrival_by_day = {}
    relevant_receipts = 0
    next_delivery = None
    for receipt in receipts:
        day = commercial_day(receipt.get("data"))
        quantity = int(receipt.get("quantita") or 0)
        if day is None or day <= today or quantity <= 0:
            continue
        arrival_by_day[day] = arrival_by_day.get(day, 0) + quantity
        if day <= today + timedelta(days=protection):
            relevant_receipts += quantity
        if next_delivery is None or day < next_delivery:
            next_delivery = day

    total_demand = demand_shop + demand_vending
    safety = math.ceil(total_demand * protection * (safety_factor - 1)) if total_demand else 0
    reorder_point = math.ceil(total_demand * lead_days) + safety
    order_up_to = math.ceil(total_demand * protection) + safety
    # Vending can be replenished only by an internal movement from shop; reserve
    # enough shop stock for shop consumption and the configured physical floor.
    vending_gap = max(0, math.ceil(demand_vending * protection) - vending_stock)
    transferable = max(0, shop_stock - math.ceil(demand_shop * protection) - minimum_shop)
    transfer = min(vending_gap, transferable)
    shop_requirement = max(0, math.ceil(demand_shop * protection) + minimum_shop + transfer
                           - shop_stock - relevant_receipts)
    total_requirement = max(0, order_up_to + minimum_shop - shop_stock - vending_stock
                            - relevant_receipts)
    net = max(shop_requirement, total_requirement)
    quantity = rounded_order_quantity(net, int(lot))

    shop = float(shop_stock)
    vending = float(vending_stock)
    risk_shop = risk_vending = None
    at_delivery = None
    for offset in range(protection + 1):
        day = today + timedelta(days=offset)
        if offset:
            shop += arrival_by_day.get(day, 0)
        if offset == lead_days:
            at_delivery = round(shop + vending, 3)
        if offset == 0 and transfer:
            shop -= transfer
            vending += transfer
        shop -= demand_shop
        vending -= demand_vending
        if shop < 0 and risk_shop is None:
            risk_shop = day
        if vending < 0 and risk_vending is None:
            risk_vending = day
    first_risk = min((day for day in (risk_shop, risk_vending) if day), default=None)
    return {
        "lead_time": lead_days, "review_interval": review_days,
        "protection_period": protection, "supplier_delivery_date": supplier_day.isoformat() if lead_days else None,
        "next_delivery_date": next_delivery.isoformat() if next_delivery else None,
        "safety_stock": safety, "reorder_point": reorder_point, "order_up_to_level": order_up_to,
        "inventory_position": shop_stock + vending_stock + relevant_receipts,
        "relevant_receipts": relevant_receipts, "net_requirement": net,
        "order_quantity": quantity, "planned_vending_transfer": transfer,
        "vending_refill_unmet": max(0, vending_gap - transfer),
        "predicted_stockout_date": first_risk.isoformat() if first_risk else None,
        "shop_stockout_date": risk_shop.isoformat() if risk_shop else None,
        "vending_stockout_date": risk_vending.isoformat() if risk_vending else None,
        "stockout_before_supplier_delivery": bool(first_risk and first_risk < supplier_day) if lead_days else None,
        "expected_inventory_at_delivery": at_delivery,
        "expected_inventory_at_horizon": round(shop + vending, 3),
    }

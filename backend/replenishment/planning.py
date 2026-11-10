"""Deterministic daily inventory simulation for the read-only Auto-Order planner.

The physical stock is a live snapshot: today's registered sales have already
reduced it. Full-day forecast consumption therefore begins *tomorrow*. The
model does not claim to estimate unobserved remaining demand today.

Internal vending refills may be recommended repeatedly after dated deliveries;
they are not persisted and never create inventory.
"""
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
                   lot: int, safety_factor: float = 1.15,
                   vending_capacity: int | None = None) -> dict:
    """Plan a periodic-review order and simulate physical stock by commercial day.

    A dated receipt enters shop stock at the beginning of its scheduled day;
    its hour is unknown. Consumption begins on the next full day after the
    snapshot. A vending capacity must be supplied from the actual columns;
    None means unknown, not unlimited physical capacity.
    """
    if not isinstance(lot, int) or isinstance(lot, bool) or lot <= 0:
        raise ValueError("Lotto non valido")
    if lead_days < 0 or review_days < 0 or int(lead_days) != lead_days or int(review_days) != review_days:
        raise ValueError("Calendario di riordino non valido")
    if min(shop_stock, vending_stock, minimum_shop) < 0:
        raise ValueError("Giacenza o minimo negativo")
    if demand_shop < 0 or demand_vending < 0 or not math.isfinite(demand_shop + demand_vending):
        raise ValueError("Domanda non valida")
    if not math.isfinite(safety_factor) or safety_factor < 1:
        raise ValueError("Sicurezza non valida")
    if vending_capacity is not None:
        if not isinstance(vending_capacity, int) or vending_capacity < vending_stock:
            raise ValueError("Capacità vending incoerente con la giacenza")

    protection = lead_days + review_days if lead_days else max(1, review_days)
    supplier_day = today + timedelta(days=lead_days)
    arrival_by_day: dict[date, int] = {}
    relevant_receipts = 0
    next_delivery = None
    for receipt in receipts:
        day = commercial_day(receipt.get("data"))
        amount = int(receipt.get("quantita") or 0)
        if day is None or day <= today or amount <= 0:
            continue
        arrival_by_day[day] = arrival_by_day.get(day, 0) + amount
        if day <= today + timedelta(days=protection):
            relevant_receipts += amount
        if next_delivery is None or day < next_delivery:
            next_delivery = day

    total_demand = demand_shop + demand_vending
    safety = math.ceil(total_demand * protection * (safety_factor - 1)) if total_demand else 0
    reorder_point = math.ceil(total_demand * lead_days) + safety
    order_up_to = math.ceil(total_demand * protection) + safety

    shop = float(shop_stock)
    vending = float(vending_stock)
    risk_shop = risk_vending = None
    at_delivery = None
    transfer_schedule: list[dict] = []
    total_transfer = 0

    # This is a simulated recommendation, not a recorded stock movement.
    for offset in range(protection + 1):
        day = today + timedelta(days=offset)
        if offset:
            shop += arrival_by_day.get(day, 0)
        if lead_days and offset == lead_days:
            at_delivery = round(shop + vending, 3)

        remaining_demand_days = protection - offset + (1 if offset > 0 else 0)
        if demand_vending > 0 and remaining_demand_days > 0:
            required_vending = max(0, math.ceil(demand_vending * remaining_demand_days) - math.floor(vending))
            next_receipt_offsets = [
                (arrival - today).days for arrival in arrival_by_day
                if offset < (arrival - today).days <= protection
            ]
            if next_receipt_offsets:
                # Before the next receipt we must reserve shop's consumption.
                next_receipt_offset = min(next_receipt_offsets)
                shop_days_to_protect = max(0, next_receipt_offset - offset - (1 if offset == 0 else 0))
            else:
                shop_days_to_protect = remaining_demand_days
            transferable = max(0, math.floor(shop - minimum_shop -
                                              math.ceil(demand_shop * shop_days_to_protect)))
            headroom = max(0, vending_capacity - math.ceil(vending)) if vending_capacity is not None else required_vending
            transfer = min(required_vending, transferable, headroom)
            if transfer:
                shop -= transfer
                vending += transfer
                total_transfer += transfer
                transfer_schedule.append({"data": day.isoformat(), "quantita": transfer})

        # Day zero is a live, already-decremented stock snapshot.
        if offset:
            shop -= demand_shop
            vending -= demand_vending
            if shop < 0 and risk_shop is None:
                risk_shop = day
            if vending < 0 and risk_vending is None:
                risk_vending = day
            # Lost demand is not a physical negative balance or an obligation
            # to replenish past consumption. Record the stockout and continue
            # projecting from zero rather than carrying fictitious backorders.
            shop = max(0.0, shop)
            vending = max(0.0, vending)

    # Planned internal transfers have zero effect on total inventory, but
    # require sufficient shop stock when the operation is actually performed.
    shop_requirement = max(0, math.ceil(demand_shop * protection) + minimum_shop
                           + total_transfer - shop_stock - relevant_receipts)
    total_requirement = max(0, order_up_to + minimum_shop - shop_stock - vending_stock
                            - relevant_receipts)
    net = max(shop_requirement, total_requirement)
    quantity = rounded_order_quantity(net, lot)
    first_risk = min((d for d in (risk_shop, risk_vending) if d), default=None)
    vending_gap = max(0, math.ceil(demand_vending * protection) - vending_stock)
    return {
        "lead_time": lead_days, "review_interval": review_days,
        "protection_period": protection,
        "supplier_delivery_date": supplier_day.isoformat() if lead_days else None,
        "next_delivery_date": next_delivery.isoformat() if next_delivery else None,
        "safety_stock": safety, "reorder_point": reorder_point,
        "order_up_to_level": order_up_to,
        "inventory_position": shop_stock + vending_stock + relevant_receipts,
        "relevant_receipts": relevant_receipts, "net_requirement": net,
        "order_quantity": quantity, "planned_vending_transfer": total_transfer,
        "vending_transfer_schedule": transfer_schedule,
        "vending_capacity_known": vending_capacity is not None,
        "vending_refill_unmet": max(0, vending_gap - total_transfer),
        "predicted_stockout_date": first_risk.isoformat() if first_risk else None,
        "shop_stockout_date": risk_shop.isoformat() if risk_shop else None,
        "vending_stockout_date": risk_vending.isoformat() if risk_vending else None,
        "stockout_before_supplier_delivery": bool(first_risk and first_risk < supplier_day) if lead_days else None,
        "expected_inventory_at_delivery": at_delivery,
        "expected_inventory_at_horizon": round(shop + vending, 3),
        "intraday_demand_not_modelled": bool(total_demand),
        "forecast_first_full_day": (today + timedelta(days=1)).isoformat(),
    }

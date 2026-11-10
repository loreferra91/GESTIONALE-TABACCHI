"""Pure one-day demand models. Zero-sales days are part of each window."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

try:
    from ..auto_order_planning import commercial_day
except ImportError:  # direct ASGI module import from backend/
    from auto_order_planning import commercial_day


class NativeSalesAccumulator:
    """Incremental 90-day index: O(products*days + unique explicit IDs) memory.

    Stream the Mongo cursor without allocating a raw list of all transactions.
    Explicit IDs remain in memory for cross-batch deduplication. Never infer
    duplicates from matching product/time/amount alone.
    """

    def __init__(self, end: date, days: int = 90):
        if days < 1:
            raise ValueError("days must be positive")
        self.end = end
        self.days = days
        self.start = end - timedelta(days=days - 1)
        self.first_observed: date | None = None
        self.totals = defaultdict(lambda: {"NEGOZIO": [0] * days, "VENDING": [0] * days})
        self.evidence = defaultdict(lambda: {
            "issues": set(), "sale_days": set(), "sources": set(),
            "vending_sources_by_day": defaultdict(set),
        })
        self.seen: dict[tuple, tuple] = {}

    def add(self, doc: dict[str, Any]) -> None:
        code = str(doc.get("codice") or "").strip()
        day = commercial_day(doc.get("data"))
        if not code or day is None or not self.start <= day <= self.end:
            return
        if self.first_observed is None or day < self.first_observed:
            self.first_observed = day
        info = self.evidence[code]
        source = str(doc.get("sorgente") or "").upper()
        channel = str(doc.get("canale") or "NEGOZIO").upper()
        event_key = str(doc.get("csv_event_key") or "") if source == "CSV_VENDING" else ""
        if source == "CSV_VENDING" and not event_key:
            info["issues"].add("CSV_SENZA_EVENT_KEY")
        native_id = str(doc.get("id") or "")
        if native_id:
            identity = ("NATIVE_ID", native_id)
            fingerprint = (code, day, channel, doc.get("quantita"))
            if identity in self.seen:
                info["issues"].add(
                    "ID_VENDITA_DUPLICATO" if self.seen[identity] == fingerprint
                    else "ID_VENDITA_CONFLITTUALE"
                )
                return
            self.seen[identity] = fingerprint
        if event_key:
            identity = (source, event_key)
            fingerprint = (code, day, channel, doc.get("quantita"))
            if identity in self.seen:
                info["issues"].add(
                    "CSV_EVENT_KEY_CONFLITTUALE" if self.seen[identity] != fingerprint
                    else "CSV_EVENT_KEY_DUPLICATO"
                )
                return
            self.seen[identity] = fingerprint
        try:
            quantity = int(doc.get("quantita") or 0)
        except (ValueError, TypeError):
            info["issues"].add("QUANTITA_NON_VALIDA")
            return
        if quantity < 0:
            info["issues"].add("RETTIFICA_NEGATIVA")
            return
        if channel not in ("NEGOZIO", "VENDING"):
            info["issues"].add("CANALE_NON_RICONOSCIUTO")
            return
        self.totals[code][channel][(day - self.start).days] += quantity
        if quantity:
            info["sale_days"].add(day.isoformat())
        info["sources"].add(source or "NON_SPECIFICATA")
        if channel == "VENDING":
            info["vending_sources_by_day"][day].add("CSV" if source == "CSV_VENDING" else "APP")

    def finish(self):
        for info in self.evidence.values():
            if any(len(sources) > 1 for sources in info["vending_sources_by_day"].values()):
                info["issues"].add("VENDING_APP_CSV_POTENZIALMENTE_SOVRAPPOSTE")
        return self.totals, {
            code: {"issues": sorted(info["issues"]),
                   "sale_days": len(info["sale_days"]),
                   "sources": sorted(info["sources"])}
            for code, info in self.evidence.items()
        }


def native_daily_series(documents: Iterable[dict[str, Any]], end: date, days: int = 90):
    """Return daily channel series and quality evidence, deduping explicit keys."""
    collector = NativeSalesAccumulator(end, days)
    for document in documents:
        collector.add(document)
    return collector.finish()


def baseline(history: list[int], short: int = 10, long: int = 30, weight: float = .7) -> float:
    if not history:
        return 0.0
    short_window = history[-short:]
    long_window = history[-long:]
    return max(0.0, weight * sum(short_window) / short + (1 - weight) * sum(long_window) / long)


def moving_average(history: list[int], window: int = 14) -> float:
    return sum(history[-window:]) / window if history else 0.0


def exponential_smoothing(history: list[int], alpha: float = .3) -> float:
    if not history:
        return 0.0
    estimate = float(history[0])
    for amount in history[1:]:
        estimate = alpha * amount + (1 - alpha) * estimate
    return max(0.0, estimate)


def croston_sba(history: list[int], alpha: float = .2) -> float:
    """Bias-corrected intermittent estimate; never fabricates demand from zeros."""
    nonzero = [(index, value) for index, value in enumerate(history) if value > 0]
    if not nonzero:
        return 0.0
    size = float(nonzero[0][1])
    interval = float(nonzero[0][0] + 1)
    previous = nonzero[0][0]
    for index, value in nonzero[1:]:
        size += alpha * (value - size)
        interval += alpha * ((index - previous) - interval)
        previous = index
    return max(0.0, (1 - alpha / 2) * size / max(1.0, interval))


MODELS = {
    "BASELINE 10/30": baseline,
    "MEDIA MOBILE 14": moving_average,
    "SMOOTHING ESPONENZIALE": exponential_smoothing,
    "CROSTON SBA": croston_sba,
}

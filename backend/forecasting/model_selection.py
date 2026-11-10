"""Rolling-origin evaluation; every prediction sees only preceding days."""
from __future__ import annotations

from .demand_models import MODELS, baseline


def rotation_class(history: list[int], created_recently: bool = False) -> str:
    recent = history[-30:]
    total = sum(recent)
    if total <= 0:
        return "NUOVO PRODOTTO" if created_recently else "SENZA DOMANDA OSSERVATA"
    if total <= 2:
        return "SLOW MOVER"
    active = sum(value > 0 for value in recent)
    if active < 4:
        return "DOMANDA INTERMITTENTE"
    if total >= 8:
        return "FAST MOVER"
    return "NORMAL MOVER"


def rolling_backtest(history: list[int], *, min_train: int = 30, origins: int = 28,
                     short: int = 10, long: int = 30, weight: float = .7) -> dict:
    end = len(history)
    first = max(min_train, end - origins)
    if end - first < 14:
        return {}
    actual = history[first:]
    denominator = sum(actual)
    result = {}
    for name, model in MODELS.items():
        predicted = [(baseline(history[:index], short, long, weight) if name == "BASELINE 10/30"
                      else model(history[:index])) for index in range(first, end)]
        errors = [forecast - observed for forecast, observed in zip(predicted, actual)]
        result[name] = {
            "mae": round(sum(abs(error) for error in errors) / len(errors), 4),
            "wape": round(sum(abs(error) for error in errors) / denominator, 4) if denominator else None,
            "bias": round(sum(errors) / len(errors), 4),
            "underestimate_rate": round(sum(error < 0 for error in errors) / len(errors), 4),
            "origins": len(errors),
        }
    return result


def choose_forecast(history: list[int], *, short: int = 10, long: int = 30,
                    weight: float = .7, created_recently: bool = False) -> dict:
    category = rotation_class(history, created_recently)
    base = baseline(history, short, long, weight)
    if sum(history) <= 0:
        return {"daily": 0.0, "model": "STORICO INSUFFICIENTE", "class": category,
                "quality": "NESSUNA DOMANDA OSSERVATA", "backtest": {}}
    if len(history) < max(long, 60):
        return {"daily": base, "model": "BASELINE 10/30", "class": category,
                "quality": "STORICO INSUFFICIENTE", "backtest": {}}
    scores = rolling_backtest(history, short=short, long=long, weight=weight)
    base_score = scores.get("BASELINE 10/30")
    if not base_score or sum(history[-28:]) < 10:
        return {"daily": base, "model": "BASELINE 10/30", "class": category,
                "quality": "DOMANDA RARA O VALUTAZIONE LIMITATA", "backtest": scores}
    alternatives = [(name, score) for name, score in scores.items() if name != "BASELINE 10/30"
                    and score["mae"] <= base_score["mae"] * .90
                    and abs(score["bias"]) <= abs(base_score["bias"]) + .05
                    and score["underestimate_rate"] <= base_score["underestimate_rate"] + .05]
    if not alternatives:
        return {"daily": base, "model": "BASELINE 10/30", "class": category,
                "quality": "VALIDATA SU STORICO", "backtest": scores}
    name, _ = min(alternatives, key=lambda item: (item[1]["mae"], item[0]))
    return {"daily": MODELS[name](history), "model": name, "class": category,
            "quality": "PREVISIONE ADATTIVA", "backtest": scores}

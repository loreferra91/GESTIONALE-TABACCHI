"""Parameter routes, bounds validation, and parameter lookup helpers."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class ParametroIn(BaseModel):
    valore: float


PARAM_BOUNDS = {
    # nome: (min_incl, max_excl, descrizione)
    "AGGIO_PCT": (0.0, 1.0, "0 ≤ AGGIO_PCT < 1"),
    "SOGLIA_ALLERT_PCT": (0.0, 1.0, "0 ≤ SOGLIA_ALLERT_PCT < 1"),
    "GIORNI_COPERTURA_MIN": (1.0, 365.0, "1 ≤ giorni ≤ 365"),
    "GIORNI_COPERTURA_TARGET": (1.0, 365.0, "1 ≤ giorni ≤ 365"),
    "AUTO_ORDER_FINESTRA_GG": (3.0, 31.0, "3 ≤ giorni ≤ 30"),
    "AUTO_ORDER_FINESTRA_BREVE_GG": (3.0, 31.0, "3 ≤ giorni ≤ 30"),
    "AUTO_ORDER_FINESTRA_LUNGA_GG": (7.0, 91.0, "7 ≤ giorni ≤ 90"),
    "AUTO_ORDER_PESO_BREVE": (0.0, 1.01, "0 ≤ peso ≤ 1"),
    "AUTO_ORDER_MIN_VENDUTO_BREVE": (0.0, 10000.0, "0 ≤ soglia"),
    "AUTO_ORDER_MIN_VENDUTO_LUNGO": (0.0, 10000.0, "0 ≤ soglia"),
    "AUTO_ORDER_FATTORE_SICUREZZA": (1.0, 5.0, "1 ≤ fattore < 5"),
    "PERIODO_VENDUTI_GG": (1.0, 3650.0, "1 ≤ giorni ≤ 3650"),
    "SLOW_TARGET_FACTOR": (0.0, 5.0, "0 ≤ SLOW_TARGET_FACTOR ≤ 5"),
    "FATT_SETTIMANALE": (0.0, 10.0, "0 ≤ FATT_SETTIMANALE ≤ 10"),
    "GIORNI_STORICO_VEND": (1.0, 365.0, "1 ≤ giorni ≤ 365"),
    "GIORNI_SETTIMANA": (1.0, 31.0, "1 ≤ giorni ≤ 31"),
    "VENDITE_GIORNALIERE_MESE": (1.0, 100.0, "1 ≤ divisore ≤ 100"),
    "FAST_VENDUTO30_MIN": (0.0, 10000.0, "0 ≤ soglia"),
    "SLOW_VENDUTO30_MAX": (0.0, 10000.0, "0 ≤ soglia"),
    "SCORTA_MINIMA_NEGOZIO_VENDING": (0.0, 10000.0, "0 ≤ scorta minima"),
    "GIACENZA_INIZIALE_CONTANTI_VENDING": (0.0, 1000000.0, "0 ≤ giacenza iniziale < 1.000.000"),
    "LOTTO_SIGARETTE": (1.0, 10000.0, "1 ≤ lotto"),
    "LOTTO_SIGARI": (1.0, 10000.0, "1 ≤ lotto"),
    "LOTTO_SIGARETTI": (1.0, 10000.0, "1 ≤ lotto"),
    "LOTTO_FIUTO_E_MASTICO": (1.0, 10000.0, "1 ≤ lotto"),
    "LOTTO_TRINCIATI_PER_SIGARETTA": (1.0, 10000.0, "1 ≤ lotto"),
    "LOTTO_ALTRI_TABACCHI_DA_FUMO": (1.0, 10000.0, "1 ≤ lotto"),
    "LOTTO_INALAZIONE_SENZA_COMBUSTIONE": (1.0, 10000.0, "1 ≤ lotto"),
    "LOTTO_ELETTRONICHE": (1.0, 10000.0, "1 ≤ lotto"),
    "LOTTO_ACCESSORI": (1.0, 10000.0, "1 ≤ lotto"),
}


def create_parameters_router(
    *,
    get_database,
    make_parameter,
    normalize_nonnegative_money,
    sum_money,
    adjust_cash_balance,
    record_audit_event,
):
    router = APIRouter()

    async def get_params():
        docs = await get_database().parametri.find({}, {"_id": 0}).to_list(1000)
        return {document["nome"]: float(document["valore"]) for document in docs}

    @router.get("/parametri")
    async def list_parametri():
        return await get_database().parametri.find(
            {}, {"_id": 0}
        ).sort("nome", 1).to_list(200)

    @router.put("/parametri/{nome}")
    async def update_parametro(nome: str, body: ParametroIn):
        try:
            value = (
                normalize_nonnegative_money(body.valore)
                if nome == "GIACENZA_INIZIALE_CONTANTI_VENDING"
                else body.valore
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

        bounds = PARAM_BOUNDS.get(nome)
        if bounds is not None:
            lower, upper, message = bounds
            if not (lower <= value < upper):
                raise HTTPException(
                    422,
                    f"{nome} fuori range consentito ({message}). Ricevuto: {value}",
                )
        if nome == "SCORTA_MINIMA_NEGOZIO_VENDING" and not float(body.valore).is_integer():
            raise HTTPException(422, "SCORTA_MINIMA_NEGOZIO_VENDING deve essere un numero intero")

        database = get_database()
        previous = await database.parametri.find_one(
            {"nome": nome}, {"_id": 0, "valore": 1}
        )
        previous_value = float((previous or {}).get("valore") or 0)
        result = await database.parametri.update_one(
            {"nome": nome}, {"$set": {"valore": value}}
        )
        if result.matched_count == 0:
            await database.parametri.insert_one(make_parameter(nome, value))

        if nome == "GIACENZA_INIZIALE_CONTANTI_VENDING":
            delta = sum_money([value, -previous_value])
            if delta:
                await adjust_cash_balance(delta)

        document = await database.parametri.find_one({"nome": nome}, {"_id": 0})
        if previous_value != float(value):
            await record_audit_event(
                "MODIFICATO",
                "PARAMETRO",
                nome,
                prima={"valore": previous_value},
                dopo={"valore": float(value)},
            )
        return document

    return router, get_params, list_parametri, update_parametro

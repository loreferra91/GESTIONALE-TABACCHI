"""Cash, deposit, receipt, and vending-withdrawal routes."""
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError
import re

if __package__:
    from .money import Money, NonNegativeMoney, money, money_decimal, money_sum
else:
    from money import Money, NonNegativeMoney, money, money_decimal, money_sum


VENDING_CASH_STATE_ID = "saldo"
ELECTRONIC_PAYMENT_METHODS = {"POS", "SATISPAY", "ALTRO"}


def cash_payment_method(document):
    """Classify current and legacy cash-register movements."""
    explicit = str(document.get("pagamento") or "").strip().upper()
    if explicit:
        return explicit
    description = str(document.get("descrizione") or "").upper()
    legacy = re.search(r"·\s*(CONTANTI|POS|SATISPAY|ALTRO)\s*\)", description)
    return legacy.group(1) if legacy else "CONTANTI"


class MovimentoCassa(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: str
    tipo: str
    importo: Money
    descrizione: str = ""
    operatore: str = ""
    pagamento: str = "CONTANTI"
    batch_id: str | None = None


class MovimentoIn(BaseModel):
    data: str
    tipo: str
    importo: Money
    descrizione: str | None = ""
    operatore: str | None = ""
    pagamento: str = "CONTANTI"
    idempotency_key: str | None = None


class Versamento(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: str
    importo: NonNegativeMoney
    descrizione: str = ""
    operatore: str = ""


class VersamentoIn(BaseModel):
    data: str
    importo: NonNegativeMoney
    descrizione: str | None = ""
    operatore: str | None = ""
    idempotency_key: str | None = None


class PrelievoVending(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: str
    importo: NonNegativeMoney
    descrizione: str = ""
    operatore: str = ""


class PrelievoVendingIn(BaseModel):
    data: str
    importo: NonNegativeMoney
    descrizione: str | None = ""
    operatore: str | None = ""
    idempotency_key: str | None = None


class ScontrinoVending(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: str
    importo: NonNegativeMoney
    descrizione: str = ""
    operatore: str = ""


class ScontrinoVendingIn(BaseModel):
    data: str
    importo: NonNegativeMoney
    descrizione: str | None = ""
    operatore: str | None = ""
    idempotency_key: str | None = None


def create_cash_movements_router(*, get_database, record_audit_event, cap_limit):
    router = APIRouter()

    def session_kwargs(session):
        return {"session": session} if session is not None else {}

    async def vending_cash_balance(session=None):
        """Return cash in the vending machine, initializing legacy state once."""
        database = get_database()
        kwargs = session_kwargs(session)
        state = await database.cassa_vending_stato.find_one(
            {"id": VENDING_CASH_STATE_ID}, {"_id": 0}, **kwargs
        )
        if state:
            return money(state.get("giacenza") or 0)

        legacy_docs = await database.prelievi_vending.find(
            {}, {"_id": 0, "importo": 1}, **kwargs
        ).to_list(None)
        legacy_balance = money_sum(document.get("importo") or 0 for document in legacy_docs)
        now = datetime.now(timezone.utc).isoformat()
        await database.cassa_vending_stato.update_one(
            {"id": VENDING_CASH_STATE_ID},
            {"$setOnInsert": {
                "id": VENDING_CASH_STATE_ID,
                "giacenza": legacy_balance,
                "initialized_at": now,
                "updated_at": now,
            }},
            upsert=True,
            **kwargs,
        )
        state = await database.cassa_vending_stato.find_one(
            {"id": VENDING_CASH_STATE_ID}, {"_id": 0}, **kwargs
        )
        return money((state or {}).get("giacenza") or 0)

    async def adjust_vending_cash_balance(
        amount: float,
        session=None,
        *,
        require_nonnegative: bool = False,
    ):
        database = get_database()
        kwargs = session_kwargs(session)
        delta = money_decimal(amount)
        await vending_cash_balance(session)
        for _ in range(20):
            state = await database.cassa_vending_stato.find_one(
                {"id": VENDING_CASH_STATE_ID}, **kwargs
            )
            previous = state.get("giacenza")
            updated = money(money_decimal(previous or 0) + delta)
            if require_nonnegative and updated < 0:
                raise HTTPException(
                    409,
                    f"Saldo vending insufficiente: disponibili {money(previous or 0):.2f} €",
                )
            result = await database.cassa_vending_stato.update_one(
                {"id": VENDING_CASH_STATE_ID, "giacenza": previous},
                {"$set": {
                    "giacenza": updated,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
                **kwargs,
            )
            if result.matched_count:
                return updated
        raise HTTPException(409, "Cassa vending modificata contemporaneamente: riprovare")

    async def collection_money_total(collection):
        total = Decimal(0)
        async for document in collection.find({}, {"_id": 0, "importo": 1}):
            total += money_decimal(document.get("importo") or 0)
        return money(total)

    async def movement_summary(collection, limit):
        capped_limit = cap_limit(limit)
        documents = await collection.find(
            {}, {"_id": 0}
        ).sort("data", -1).limit(capped_limit).to_list(capped_limit)
        return {"movimenti": documents, "totale": await collection_money_total(collection)}

    async def versamenti_summary(limit: int = 500):
        return await movement_summary(get_database().versamenti, limit)

    @router.get("/versamenti")
    async def list_versamenti(limit: int = 500):
        return await versamenti_summary(limit)

    @router.post("/versamenti")
    async def add_versamento(movement: VersamentoIn):
        if movement.importo <= 0:
            raise HTTPException(422, "L'importo del versamento deve essere positivo")
        database = get_database()
        key = str(movement.idempotency_key or "").strip() or None
        if key and not 10 <= len(key) <= 160:
            raise HTTPException(422, "idempotency_key non valido")
        if key:
            existing = await database.versamenti.find_one({"idempotency_key": key}, {"_id": 0})
            if existing:
                return existing
        document = Versamento(**{
            key_: value for key_, value in movement.model_dump().items()
            if key_ != "idempotency_key"
        }).model_dump()
        if key:
            document["idempotency_key"] = key
        response = dict(document)
        try:
            await database.versamenti.insert_one(document)
        except DuplicateKeyError:
            return await database.versamenti.find_one({"idempotency_key": key}, {"_id": 0})
        await record_audit_event(
            "CREATO", "VERSAMENTO", document["id"], dopo=response, idempotency_key=key
        )
        return response

    @router.delete("/versamenti/{m_id}")
    async def del_versamento(m_id: str):
        database = get_database()
        existing = await database.versamenti.find_one({"id": m_id}, {"_id": 0})
        await database.versamenti.delete_one({"id": m_id})
        if existing:
            await record_audit_event("ELIMINATO", "VERSAMENTO", m_id, prima=existing)
        return {"ok": True}

    async def prelievi_vending_summary(limit: int = 500):
        return await movement_summary(get_database().prelievi_vending, limit)

    @router.get("/prelievi-vending")
    async def list_prelievi_vending(limit: int = 500):
        return await prelievi_vending_summary(limit)

    @router.post("/prelievi-vending")
    async def add_prelievo_vending(movement: PrelievoVendingIn):
        if movement.importo <= 0:
            raise HTTPException(422, "L'importo del prelievo deve essere positivo")
        database = get_database()
        key = str(movement.idempotency_key or "").strip() or None
        if key and not 10 <= len(key) <= 160:
            raise HTTPException(422, "idempotency_key non valido")
        if key:
            existing = await database.prelievi_vending.find_one({"idempotency_key": key}, {"_id": 0})
            if existing:
                return existing
        await vending_cash_balance()
        document = PrelievoVending(**{
            key_: value for key_, value in movement.model_dump().items()
            if key_ != "idempotency_key"
        }).model_dump()
        if key:
            document["idempotency_key"] = key
        response = dict(document)
        try:
            await database.prelievi_vending.insert_one(document)
        except DuplicateKeyError:
            existing = await database.prelievi_vending.find_one(
                {"idempotency_key": key}, {"_id": 0}
            )
            if existing:
                return existing
            raise HTTPException(409, "Prelievo già registrato")
        try:
            await adjust_vending_cash_balance(
                -movement.importo,
                require_nonnegative=True,
            )
        except Exception:
            await database.prelievi_vending.delete_one({"id": document["id"]})
            raise
        await record_audit_event(
            "CREATO", "PRELIEVO_VENDING", document["id"],
            dopo=response, idempotency_key=key,
        )
        return response

    @router.delete("/prelievi-vending/{m_id}")
    async def del_prelievo_vending(m_id: str):
        database = get_database()
        await vending_cash_balance()
        existing = await database.prelievi_vending.find_one({"id": m_id}, {"_id": 0})
        await database.prelievi_vending.delete_one({"id": m_id})
        if existing:
            await adjust_vending_cash_balance(float(existing.get("importo") or 0))
            await record_audit_event(
                "ELIMINATO", "PRELIEVO_VENDING", m_id, prima=existing
            )
        return {"ok": True}

    async def scontrini_vending_summary(limit: int = 500):
        return await movement_summary(get_database().scontrini_vending, limit)

    @router.get("/scontrini-vending")
    async def list_scontrini_vending(limit: int = 500):
        return await scontrini_vending_summary(limit)

    @router.post("/scontrini-vending")
    async def add_scontrino_vending(movement: ScontrinoVendingIn):
        if movement.importo <= 0:
            raise HTTPException(422, "L'importo dello scontrino deve essere positivo")
        database = get_database()
        key = str(movement.idempotency_key or "").strip() or None
        if key and not 10 <= len(key) <= 160:
            raise HTTPException(422, "idempotency_key non valido")
        if key:
            existing = await database.scontrini_vending.find_one({"idempotency_key": key}, {"_id": 0})
            if existing:
                return existing
        document = ScontrinoVending(**{
            key_: value for key_, value in movement.model_dump().items()
            if key_ != "idempotency_key"
        }).model_dump()
        if key:
            document["idempotency_key"] = key
        response = dict(document)
        try:
            await database.scontrini_vending.insert_one(document)
        except DuplicateKeyError:
            return await database.scontrini_vending.find_one({"idempotency_key": key}, {"_id": 0})
        await record_audit_event(
            "CREATO", "SCONTRINO_VENDING", document["id"],
            dopo=response, idempotency_key=key,
        )
        return response

    @router.delete("/scontrini-vending/{m_id}")
    async def del_scontrino_vending(m_id: str):
        database = get_database()
        existing = await database.scontrini_vending.find_one({"id": m_id}, {"_id": 0})
        await database.scontrini_vending.delete_one({"id": m_id})
        if existing:
            await record_audit_event(
                "ELIMINATO", "SCONTRINO_VENDING", m_id, prima=existing
            )
        return {"ok": True}

    @router.get("/cassa")
    async def list_cassa(limit: int = 500):
        database = get_database()
        capped_limit = cap_limit(limit)
        documents = await database.cassa.find(
            {}, {"_id": 0}
        ).sort("data", -1).limit(capped_limit).to_list(capped_limit)
        balances = {"CONTANTI": Decimal(0), "POS": Decimal(0), "SATISPAY": Decimal(0), "ALTRO": Decimal(0)}
        async for document in database.cassa.find({}, {"_id": 0}):
            payment = cash_payment_method(document)
            bucket = payment if payment in balances else "ALTRO"
            signed = money_decimal(document["importo"])
            if document["tipo"] not in ("ENTRATA", "SALDO_INIZIALE"):
                signed = -signed
            balances[bucket] += signed
        cash_balance = money(balances["CONTANTI"])
        electronic_balance = money(sum(balances[key] for key in ELECTRONIC_PAYMENT_METHODS))
        return {
            "movimenti": documents,
            "saldo": cash_balance,
            "saldo_contanti": cash_balance,
            "saldo_elettronico": electronic_balance,
            "saldo_totale": money(balances["CONTANTI"] + sum(balances[key] for key in ELECTRONIC_PAYMENT_METHODS)),
            "pagamenti": {key: money(value) for key, value in balances.items()},
        }

    @router.post("/cassa")
    async def add_cassa(movement: MovimentoIn):
        if movement.tipo not in {"ENTRATA", "USCITA", "SALDO_INIZIALE"}:
            raise HTTPException(422, "Tipo movimento cassa non valido")
        if movement.tipo != "SALDO_INIZIALE" and movement.importo <= 0:
            raise HTTPException(422, "Entrate e uscite richiedono un importo positivo")
        payment = str(movement.pagamento or "CONTANTI").strip().upper()
        if payment not in {"CONTANTI", *ELECTRONIC_PAYMENT_METHODS}:
            raise HTTPException(422, "Metodo di pagamento non valido")
        database = get_database()
        key = str(movement.idempotency_key or "").strip() or None
        if key and not 10 <= len(key) <= 160:
            raise HTTPException(422, "idempotency_key non valido")
        if key:
            existing = await database.cassa.find_one({"idempotency_key": key}, {"_id": 0})
            if existing:
                return existing
        document = MovimentoCassa(**{
            key_: value for key_, value in movement.model_dump().items()
            if key_ != "idempotency_key"
        }).model_dump()
        document["pagamento"] = payment
        if key:
            document["idempotency_key"] = key
        response = dict(document)
        try:
            await database.cassa.insert_one(document)
        except DuplicateKeyError:
            return await database.cassa.find_one({"idempotency_key": key}, {"_id": 0})
        await record_audit_event(
            "CREATO", "MOVIMENTO_CASSA", document["id"],
            dopo=response, idempotency_key=key,
        )
        return response

    @router.delete("/cassa/{m_id}")
    async def del_cassa(m_id: str):
        database = get_database()
        existing = await database.cassa.find_one({"id": m_id}, {"_id": 0})
        await database.cassa.delete_one({"id": m_id})
        if existing:
            await record_audit_event(
                "ELIMINATO", "MOVIMENTO_CASSA", m_id, prima=existing
            )
        return {"ok": True}

    return {
        "router": router,
        "vending_cash_balance": vending_cash_balance,
        "adjust_vending_cash_balance": adjust_vending_cash_balance,
        "collection_money_total": collection_money_total,
        "versamenti_summary": versamenti_summary,
        "list_versamenti": list_versamenti,
        "add_versamento": add_versamento,
        "del_versamento": del_versamento,
        "prelievi_vending_summary": prelievi_vending_summary,
        "list_prelievi_vending": list_prelievi_vending,
        "add_prelievo_vending": add_prelievo_vending,
        "del_prelievo_vending": del_prelievo_vending,
        "scontrini_vending_summary": scontrini_vending_summary,
        "list_scontrini_vending": list_scontrini_vending,
        "add_scontrino_vending": add_scontrino_vending,
        "del_scontrino_vending": del_scontrino_vending,
        "list_cassa": list_cassa,
        "add_cassa": add_cassa,
        "del_cassa": del_cassa,
    }

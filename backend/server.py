from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
from pymongo.errors import OperationFailure
import os, json, logging, uuid, re, asyncio, math, unicodedata, hashlib
from contextlib import asynccontextmanager
from pathlib import Path
import base64
import secrets
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

if __package__:
    from .adm_router import (
        ADM_CATEGORY_ALIASES, ADM_PREZZI_URL, ListinoItem,
        _adm_product_update_operations, adm_local_code, adm_numeric_code,
        create_adm_router, fetch_adm_categories_sync, parse_adm_pdf_sync,
        parse_italian_money,
    )
    from .fit_tariff import fetch_and_parse_fit_tariff_sync
    from .audit_router import create_audit_router
    from .agent_integration_router import create_agent_integration_router
    from .auto_order_router import (
        AutoOrderConfermaIn, AutoOrderRigaIn, auto_order_snapshot_key,
        create_auto_order_router,
    )
    from .backup_router import (
        BACKUP_COLLECTIONS, BACKUP_FILE_FORMAT, BACKUP_FILE_VERSION,
        BACKUP_PARTIAL_RETENTION_LIMIT, BACKUP_RETENTION_LIMIT,
        BACKUP_RETENTION_POLICY_ID, DAILY_BACKUP_REASON,
        EXCEL_IMPORT_BACKUP_COLLECTIONS, OPTIONAL_BACKUP_COLLECTIONS,
        create_backup_router,
    )
    from .cash_movements_router import (
        MovimentoCassa, MovimentoIn, PrelievoVending, PrelievoVendingIn,
        ScontrinoVending, ScontrinoVendingIn, VENDING_CASH_STATE_ID,
        Versamento, VersamentoIn, create_cash_movements_router,
    )
    from .dashboard_router import (
        create_dashboard_router,
        is_cash_payment as _is_cash_payment,
        is_sale_after_excel_import as _is_sale_after_excel_import,
        latest_sale_date as _latest_sale_date,
        parse_sale_date as _parse_sale_date,
        parse_utc_datetime as _parse_utc_datetime,
        vending_payment_bucket as _vending_payment_bucket,
    )
    from .csv_vending import (
        create_csv_vending_parser,
        csv_vending_datetime as _csv_vending_datetime,
        filter_new_csv_vending_rows as _filter_new_csv_vending_rows,
        latest_historical_vending_datetime as _latest_historical_vending_datetime,
        local_sale_datetime as _local_sale_datetime,
        normalize_vending_column as _normalize_vending_column,
        parse_sale_datetime as _parse_sale_datetime,
    )
    from .csv_vending_accounting_router import (
        VENDING_ACCOUNTING_STATE_ID, create_csv_vending_accounting_router,
    )
    from .csv_vending_sales_router import create_csv_vending_sales_router
    from .excel_import_router import (
        ELECTRONIC_INHALATION_CATEGORY,
        category_from_description,
        create_excel_import_router,
        electronic_inhalation_filter,
        is_electronic_inhalation,
    )
    from .import_history_router import (
        create_import_history_router,
        import_includes_accounting as _import_includes_accounting,
    )
    from .inventory_analytics_router import create_inventory_analytics_router
    from .index_migrations import migrate_unique_business_indexes
    from .money import Money, money, money_decimal, money_sum, money_product, nonnegative_money
    from .operations_router import create_operations_router
    from .orders_router import (
        SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED,
        SMART_VENUE_AUTO_ORDER_REPAIR_ID,
        BulkOrdineIn, OrdineIn, OrdineStorico, create_orders_router,
        receipt_quantities_by_code as _receipt_quantities_by_code,
    )
    from .parameters_router import PARAM_BOUNDS, ParametroIn, create_parameters_router
    from .products_router import (
        PRODUCT_CATEGORIES, Prodotto, ProdottoIn, VendutiNegozioIn,
        create_products_router,
    )
    from .real_stock_router import (
        GiacenzaRealeIn, create_real_stock_router,
        plan_vending_inventory as _plan_vending_inventory,
    )
    from .reconciliation_router import ReconciliationCaseIn, create_reconciliation_router
    from .sales_router import (
        BulkVenditaIn, CassaRapidaIn, VenditaGiornaliera, VenditaIn,
        create_sales_router,
    )
    from .smart_venue_router import (
        SmartVenueBarcodeIn, SmartVenueBulkIn, SmartVenueEntryIn,
        SmartVenueExitIn, SmartVenueProductIn,
        create_smart_venue_router,
    )
    from .vending_router import VendingColonna, VendingCreateIn, create_vending_router
    from .vending_column_migrations import migrate_vending_column_zero_padding
else:
    from adm_router import (
        ADM_CATEGORY_ALIASES, ADM_PREZZI_URL, ListinoItem,
        _adm_product_update_operations, adm_local_code, adm_numeric_code,
        create_adm_router, fetch_adm_categories_sync, parse_adm_pdf_sync,
        parse_italian_money,
    )
    from fit_tariff import fetch_and_parse_fit_tariff_sync
    from audit_router import create_audit_router
    from agent_integration_router import create_agent_integration_router
    from auto_order_router import (
        AutoOrderConfermaIn, AutoOrderRigaIn, auto_order_snapshot_key,
        create_auto_order_router,
    )
    from backup_router import (
        BACKUP_COLLECTIONS, BACKUP_FILE_FORMAT, BACKUP_FILE_VERSION,
        BACKUP_PARTIAL_RETENTION_LIMIT, BACKUP_RETENTION_LIMIT,
        BACKUP_RETENTION_POLICY_ID, DAILY_BACKUP_REASON,
        EXCEL_IMPORT_BACKUP_COLLECTIONS, OPTIONAL_BACKUP_COLLECTIONS,
        create_backup_router,
    )
    from cash_movements_router import (
        MovimentoCassa, MovimentoIn, PrelievoVending, PrelievoVendingIn,
        ScontrinoVending, ScontrinoVendingIn, VENDING_CASH_STATE_ID,
        Versamento, VersamentoIn, create_cash_movements_router,
    )
    from dashboard_router import (
        create_dashboard_router,
        is_cash_payment as _is_cash_payment,
        is_sale_after_excel_import as _is_sale_after_excel_import,
        latest_sale_date as _latest_sale_date,
        parse_sale_date as _parse_sale_date,
        parse_utc_datetime as _parse_utc_datetime,
        vending_payment_bucket as _vending_payment_bucket,
    )
    from csv_vending import (
        create_csv_vending_parser,
        csv_vending_datetime as _csv_vending_datetime,
        filter_new_csv_vending_rows as _filter_new_csv_vending_rows,
        latest_historical_vending_datetime as _latest_historical_vending_datetime,
        local_sale_datetime as _local_sale_datetime,
        normalize_vending_column as _normalize_vending_column,
        parse_sale_datetime as _parse_sale_datetime,
    )
    from csv_vending_accounting_router import (
        VENDING_ACCOUNTING_STATE_ID, create_csv_vending_accounting_router,
    )
    from csv_vending_sales_router import create_csv_vending_sales_router
    from excel_import_router import (
        ELECTRONIC_INHALATION_CATEGORY,
        category_from_description,
        create_excel_import_router,
        electronic_inhalation_filter,
        is_electronic_inhalation,
    )
    from import_history_router import (
        create_import_history_router,
        import_includes_accounting as _import_includes_accounting,
    )
    from inventory_analytics_router import create_inventory_analytics_router
    from index_migrations import migrate_unique_business_indexes
    from money import Money, money, money_decimal, money_sum, money_product, nonnegative_money
    from operations_router import create_operations_router
    from orders_router import (
        SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED,
        SMART_VENUE_AUTO_ORDER_REPAIR_ID,
        BulkOrdineIn, OrdineIn, OrdineStorico, create_orders_router,
        receipt_quantities_by_code as _receipt_quantities_by_code,
    )
    from parameters_router import PARAM_BOUNDS, ParametroIn, create_parameters_router
    from products_router import (
        PRODUCT_CATEGORIES, Prodotto, ProdottoIn, VendutiNegozioIn,
        create_products_router,
    )
    from real_stock_router import (
        GiacenzaRealeIn, create_real_stock_router,
        plan_vending_inventory as _plan_vending_inventory,
    )
    from reconciliation_router import ReconciliationCaseIn, create_reconciliation_router
    from sales_router import (
        BulkVenditaIn, CassaRapidaIn, VenditaGiornaliera, VenditaIn,
        create_sales_router,
    )
    from smart_venue_router import (
        SmartVenueBarcodeIn, SmartVenueBulkIn, SmartVenueEntryIn,
        SmartVenueExitIn, SmartVenueProductIn,
        create_smart_venue_router,
    )
    from vending_router import VendingColonna, VendingCreateIn, create_vending_router
    from vending_column_migrations import migrate_vending_column_zero_padding

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]


LOCAL_ENVIRONMENTS = frozenset({"development", "dev", "local", "test", "testing"})


def _environment() -> str:
    return str(os.environ.get("ENVIRONMENT") or "").strip().lower()


def _is_local_environment() -> bool:
    return _environment() in LOCAL_ENVIRONMENTS


def _is_production() -> bool:
    return _environment() in {"production", "prod"}


def _requires_secure_runtime() -> bool:
    """Un ambiente non dichiarato localmente deve essere trattato come distribuito."""
    return not _is_local_environment()


def _validate_runtime_security() -> None:
    """Fuori dall'ambiente locale credenziali e CORS espliciti sono obbligatori."""
    if not _requires_secure_runtime():
        return
    missing = [name for name in ("APP_USERNAME", "APP_PASSWORD") if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Configurazione non locale incompleta: manca {', '.join(missing)}")
    origins = [item.strip() for item in os.environ.get("CORS_ORIGINS", "").split(",") if item.strip()]
    if not origins or "*" in origins:
        raise RuntimeError("CORS_ORIGINS deve contenere solo origini esplicite fuori dall'ambiente locale")
    agent_enabled = str(os.environ.get("AGENT_INTEGRATION_ENABLED") or "").lower() in {
        "1", "true", "yes", "on",
    }
    if agent_enabled and len(os.environ.get("AGENT_INTEGRATION_TOKEN") or "") < 32:
        raise RuntimeError("AGENT_INTEGRATION_TOKEN deve contenere almeno 32 caratteri")


async def _verify_transaction_capability() -> Dict[str, Any]:
    """Read Mongo topology and fail closed in production if transactions are unavailable."""
    hello = await client.admin.command("hello")
    topology = (
        "sharded" if hello.get("msg") == "isdbgrid"
        else "replica_set" if hello.get("setName")
        else "standalone"
    )
    capable = bool(
        hello.get("logicalSessionTimeoutMinutes") is not None
        and topology in {"replica_set", "sharded"}
    )
    result = {"topology": topology, "transactions": capable}
    if _requires_secure_runtime() and not capable:
        raise RuntimeError(
            "MongoDB non supporta transazioni multi-documento fuori dall'ambiente locale: "
            f"topologia rilevata {topology}"
        )
    return result


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # on_start è risolto quando il lifespan viene eseguito, dopo il caricamento
    # completo del modulo.
    _validate_runtime_security()
    transaction_capability = await _verify_transaction_capability()
    logging.info("MongoDB transaction capability: %s", transaction_capability)
    await on_start()
    try:
        yield
    finally:
        client.close()


app = FastAPI(title="God Services Gestionale Tabacchi", lifespan=lifespan)
api = APIRouter(prefix="/api")
AGENT_SERVICE_PATHS = frozenset({
    "/api/agent/v1/status",
    "/api/agent/v1/inventory",
    "/api/agent/v1/vending",
    "/api/agent/v1/orders",
})
audit_router, _record_audit_event, list_audit_events = create_audit_router(
    get_database=lambda: db,
)


@app.middleware("http")
async def optional_basic_auth(request: Request, call_next):
    """Protegge il deployment; fuori dall'ambiente locale le credenziali sono obbligatorie."""
    username = os.environ.get("APP_USERNAME")
    password = os.environ.get("APP_PASSWORD")
    credentials_configured = bool(username and password)

    request_path = request.scope["path"]
    is_agent_service = request.method == "GET" and request_path in AGENT_SERVICE_PATHS
    is_health_check = (request.method == "GET" and request_path == "/api/") or (
        request.method == "HEAD" and request_path == "/"
    )

    if is_health_check or is_agent_service:
        request.state.authenticated_user = "operatore-locale"
        return await call_next(request)

    if not credentials_configured:
        if _requires_secure_runtime():
            return PlainTextResponse(
                "Autenticazione non configurata",
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        request.state.authenticated_user = "operatore-locale"
        return await call_next(request)

    authorization = request.headers.get("Authorization", "")

    try:
        scheme, encoded = authorization.split(" ", 1)
        decoded = base64.b64decode(encoded).decode("utf-8")
        supplied_username, supplied_password = decoded.split(":", 1)

        authenticated = (
            scheme.lower() == "basic"
            and secrets.compare_digest(
                supplied_username.encode("utf-8"), username.encode("utf-8")
            )
            and secrets.compare_digest(
                supplied_password.encode("utf-8"), password.encode("utf-8")
            )
        )
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        authenticated = False

    if not authenticated:
        return PlainTextResponse(
            "Autenticazione richiesta",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Gestionale"'},
        )

    request.state.authenticated_user = supplied_username
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    )
    response.headers.setdefault(
        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
    )
    if request.scope["path"] in AGENT_SERVICE_PATHS:
        response.headers.setdefault("Cache-Control", "no-store")
    return response


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _allowed_mutation_origins() -> set:
    return {
        item.strip().rstrip("/")
        for item in os.environ.get("CORS_ORIGINS", "").split(",")
        if item.strip() and item.strip() != "*"
    }


@app.middleware("http")
async def reject_cross_site_mutations(request: Request, call_next):
    """Rifiuta richieste mutanti cross-site; i client senza Origin restano compatibili."""
    if request.method in _SAFE_METHODS:
        return await call_next(request)

    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin:
        if (request.headers.get("sec-fetch-site") or "").strip().lower() == "cross-site":
            return PlainTextResponse("Origine non consentita", status_code=403)
        return await call_next(request)

    if origin in _allowed_mutation_origins():
        return await call_next(request)

    host = (request.headers.get("host") or "").strip().lower()
    if host and urlsplit(origin).netloc.lower() == host:
        return await call_next(request)

    return PlainTextResponse("Origine non consentita", status_code=403)


class Parametro(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nome: str
    valore: float
    descrizione: str = ""


# ------------------------- Seed -------------------------
DEFAULT_PARAMS = {
    "LOTTO_SIGARETTE": {"valore": 10, "descrizione": "Lotto standard per SIGARETTE"},
    "LOTTO_SIGARI": {"valore": 1, "descrizione": "Lotto standard per SIGARI"},
    "LOTTO_SIGARETTI": {"valore": 1, "descrizione": "Lotto standard per SIGARETTI"},
    "LOTTO_FIUTO_E_MASTICO": {"valore": 1, "descrizione": "Lotto standard per FIUTO E MASTICO"},
    "LOTTO_TRINCIATI_PER_SIGARETTA": {"valore": 1, "descrizione": "Lotto standard per TRINCIATI PER SIGARETTA"},
    "LOTTO_ALTRI_TABACCHI_DA_FUMO": {"valore": 1, "descrizione": "Lotto standard per ALTRI TABACCHI DA FUMO"},
    "LOTTO_INALAZIONE_SENZA_COMBUSTIONE": {"valore": 10, "descrizione": "Lotto standard per PRODOTTI DA INALAZIONE SENZA COMBUSTIONE"},
    "LOTTO_ELETTRONICHE": {"valore": 5, "descrizione": "Lotto standard per SIGARETTE ELETTRONICHE"},
    "LOTTO_ACCESSORI": {"valore": 1, "descrizione": "Lotto standard per ACCESSORI"},
    "GIORNI_STORICO_VEND": {"valore": 30, "descrizione": "Finestra storico vendite (giorni)"},
    "GIORNI_SETTIMANA": {"valore": 7, "descrizione": "Costante giorni settimana"},
    "VENDITE_GIORNALIERE_MESE": {"valore": 6.5, "descrizione": "Divisore vendite medie giornaliere/mese"},
    "SOGLIA_ALLERT_PCT": {"valore": 0.35, "descrizione": "Soglia % giacenza per allert riordino"},
    "FAST_VENDUTO30_MIN": {"valore": 8, "descrizione": "Venduto min 30gg per FAST MOVER"},
    "SLOW_VENDUTO30_MAX": {"valore": 2, "descrizione": "Venduto max 30gg per SLOW MOVER"},
    "SLOW_TARGET_FACTOR": {"valore": 0.6, "descrizione": "Fattore target settimanale slow mover"},
    "FATT_SETTIMANALE": {"valore": 1.15, "descrizione": "Fattore fabbisogno settimanale"},
    "GIORNI_COPERTURA_MIN": {"valore": 7, "descrizione": "Riordina se lo stock negozio copre meno di N giorni di vendite"},
    "GIORNI_COPERTURA_TARGET": {"valore": 14, "descrizione": "Copertura target (giorni) dopo il riordino"},
    "AUTO_ORDER_FINESTRA_GG": {"valore": 10, "descrizione": "Giorni recenti usati da Auto-Order per calcolare la domanda reale"},
    "AUTO_ORDER_FINESTRA_BREVE_GG": {"valore": 10, "descrizione": "Finestra breve Auto-Order (giorni)"},
    "AUTO_ORDER_FINESTRA_LUNGA_GG": {"valore": 30, "descrizione": "Finestra lunga Auto-Order (giorni)"},
    "AUTO_ORDER_PESO_BREVE": {"valore": 0.70, "descrizione": "Peso della domanda recente; il resto pesa sulla finestra lunga"},
    "AUTO_ORDER_MIN_VENDUTO_BREVE": {"valore": 2, "descrizione": "Vendite minime nella finestra breve per il riordino automatico"},
    "AUTO_ORDER_MIN_VENDUTO_LUNGO": {"valore": 4, "descrizione": "Vendite minime nella finestra lunga per il riordino automatico"},
    "AUTO_ORDER_FATTORE_SICUREZZA": {"valore": 1.15, "descrizione": "Margine di sicurezza applicato alla scorta obiettivo"},
    "PERIODO_VENDUTI_GG": {"valore": 90, "descrizione": "Periodo informativo del totale venduto importato (non usato da Auto-Order)"},
    "AGGIO_PCT": {"valore": 0.10, "descrizione": "Aggio tabaccaio (10% default): costo acquisto = prezzo × (1 - AGGIO_PCT)"},
    "SCORTA_MINIMA_NEGOZIO_VENDING": {
        "valore": 2,
        "descrizione": "Pezzi da lasciare sempre disponibili in negozio durante la ricarica vending",
    },
    "GIACENZA_INIZIALE_CONTANTI_VENDING": {
        "valore": 0,
        "descrizione": "Contanti presenti nella vending all'apertura della contabilità",
    },
}

VENDING_CASH_RECONCILIATION_ID = "vending-cash-accounting-balance-v1"


LOTTO_PARAM_BY_CATEGORY = {
    "SIGARETTE": ("LOTTO_SIGARETTE", 10),
    "SIGARI": ("LOTTO_SIGARI", 1),
    "SIGARETTI": ("LOTTO_SIGARETTI", 1),
    "FIUTO E MASTICO": ("LOTTO_FIUTO_E_MASTICO", 1),
    "TRINCIATI PER SIGARETTA": ("LOTTO_TRINCIATI_PER_SIGARETTA", 1),
    "ALTRI TABACCHI DA FUMO": ("LOTTO_ALTRI_TABACCHI_DA_FUMO", 1),
    "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE": ("LOTTO_INALAZIONE_SENZA_COMBUSTIONE", 10),
    "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE ELETTRONICA": ("LOTTO_ELETTRONICHE", 5),
    "ACCESSORI": ("LOTTO_ACCESSORI", 1),
}


async def seed_if_empty():
    seed_path = ROOT_DIR / "seed" / "seed_data.json"
    if not seed_path.exists():
        logging.warning(
            "Seed dataset unavailable at %s; empty databases will not be bootstrapped",
            seed_path,
        )
        return
    count = await db.prodotti.count_documents({})
    if count > 0:
        return
    with open(seed_path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("prodotti"):
        docs = [Prodotto(**p).model_dump() for p in data["prodotti"]]
        await db.prodotti.insert_many(docs)
    if data.get("listino_adm"):
        docs = [ListinoItem(**p).model_dump() for p in data["listino_adm"]]
        # chunked insert
        for i in range(0, len(docs), 1000):
            await db.listino_adm.insert_many(docs[i:i+1000])
    if data.get("vending"):
        docs = [VendingColonna(**p).model_dump() for p in data["vending"]]
        await db.vending.insert_many(docs)
    if data.get("storico_ordini"):
        docs = [OrdineStorico(**p).model_dump() for p in data["storico_ordini"]]
        for i in range(0, len(docs), 1000):
            await db.storico_ordini.insert_many(docs[i:i+1000])
    # Parametri
    seed_params = {**DEFAULT_PARAMS, **{k: {"valore": v["valore"] if isinstance(v, dict) else v, "descrizione": v.get("descrizione","") if isinstance(v, dict) else ""} for k, v in (data.get("parametri") or {}).items()}}
    param_docs = [Parametro(nome=k, valore=float(v["valore"]) if v["valore"] is not None else 0, descrizione=v.get("descrizione","")).model_dump() for k, v in seed_params.items()]
    await db.parametri.insert_many(param_docs)


async def ensure_product_ids() -> int:
    """Assegna un identificativo ai prodotti legacy creati dagli import Excel."""
    missing_id = {
        "$or": [
            {"id": {"$exists": False}},
            {"id": None},
            {"id": ""},
        ]
    }
    products = await db.prodotti.find(missing_id, {"_id": 1}).to_list(MAX_LIMIT)
    if not products:
        return 0
    operations = [
        UpdateOne(
            {"_id": product["_id"], **missing_id},
            {"$set": {"id": str(uuid.uuid4())}},
        )
        for product in products
    ]
    result = await _bulk_update(db.prodotti, operations)
    return result["modificati"]


def _validate_required_index_results(results: List[Dict[str, Any]]) -> None:
    blocked = [
        f"{item.get('collection')}.{item.get('field')}"
        for item in results if item.get("status") == "duplicates"
    ]
    if blocked and _is_production():
        raise RuntimeError(
            "Indici univoci essenziali non creati per duplicati preesistenti: "
            + ", ".join(blocked)
        )


async def on_start():
    try:
        await seed_if_empty()
        # ensure parametri exists if empty
        if await db.parametri.count_documents({}) == 0:
            docs = [Parametro(nome=k, valore=float(v["valore"]), descrizione=v["descrizione"]).model_dump() for k, v in DEFAULT_PARAMS.items()]
            await db.parametri.insert_many(docs)
        else:
            # aggiungi eventuali nuovi parametri di default mancanti (upgrade idempotente)
            existing = {p["nome"] async for p in db.parametri.find({}, {"_id": 0, "nome": 1})}
            missing = [Parametro(nome=k, valore=float(v["valore"]), descrizione=v["descrizione"]).model_dump()
                       for k, v in DEFAULT_PARAMS.items() if k not in existing]
            if missing:
                await db.parametri.insert_many(missing)
        unique_index_results = await migrate_unique_business_indexes(db)
        logging.info("Business-key index migration: %s", unique_index_results)
        _validate_required_index_results(unique_index_results)
        # Gli import fanno upsert su queste chiavi: senza indici Atlas deve
        # scandire le collezioni per ogni riga, anche quando usiamo bulk_write.
        await asyncio.gather(
            db.listino_adm.create_index("codice"),
            db.vendite.create_index("data"),
            db.vendite.create_index("codice"),
            db.vendite.create_index([("data", 1), ("codice", 1)]),
            db.vendite.create_index("csv_event_key", unique=True, sparse=True),
            db.vendite.create_index("idempotency_key", unique=True, sparse=True),
            db.cassa_rapida_batches.create_index("idempotency_key", unique=True, sparse=True),
            db.cassa_rapida_batches.create_index("created_at"),
            db.db_storico_vend.create_index([("data", 1), ("codice", 1)]),
            db.storico_ordini.create_index("codice"),
            db.storico_ordini.create_index("batch_id"),
            db.storico_ordini.create_index("smart_venue_stato"),
            db.storico_ordini.create_index("idempotency_key", unique=True, sparse=True),
            db.ordini_fornitore.create_index("batch_key", unique=True),
            db.ordini_fornitore_righe.create_index("batch_key"),
            db.ricezioni_merce.create_index("idempotency_key", unique=True),
            db.ricezioni_merce.create_index("created_at"),
            db.smart_venue_movimenti.create_index("idempotency_key", unique=True),
            db.smart_venue_movimenti.create_index([("codice", 1), ("stato", 1), ("created_at", -1)]),
            db.smart_venue_movimenti.create_index("ordine_id"),
            db.rettifiche_inventario.create_index([("codice", 1), ("created_at", -1)]),
            db.rettifiche_venduti.create_index([("codice", 1), ("created_at", -1)]),
            db.reconciliation_cases.create_index("fingerprint", unique=True),
            db.reconciliation_cases.create_index([("stato", 1), ("updated_at", -1)]),
            db.audit_events.create_index([("created_at", -1)]),
            db.audit_events.create_index([("entita", 1), ("entita_id", 1), ("created_at", -1)]),
            db.backup_snapshots.create_index("created_at"),
            db.backup_snapshot_items.create_index([("snapshot_id", 1), ("collection", 1)]),
            db.backup_snapshot_items.create_index([("snapshot_id", 1), ("collection", 1), ("position", 1)]),
            db.import_history.create_index("created_at"),
            db.import_history.create_index("id"),
            db.prodotti.create_index("ultimo_import_id"),
            db.versamenti.create_index("data"),
            db.prelievi_vending.create_index("data"),
            db.scontrini_vending.create_index("data"),
            db.cassa_vending_stato.create_index("id", unique=True),
            db.versamenti.create_index("idempotency_key", unique=True, sparse=True),
            db.prelievi_vending.create_index("idempotency_key", unique=True, sparse=True),
            db.scontrini_vending.create_index("idempotency_key", unique=True, sparse=True),
            db.cassa.create_index("idempotency_key", unique=True, sparse=True),
            db.vending_accounting_state.create_index("id", unique=True),
            db.vending_accounting_events.create_index("csv_event_key", unique=True),
            db.vending_accounting_events.create_index("csv_accounting_key", unique=True, sparse=True),
            db.vending_accounting_events.create_index("data"),
            db.vending_accounting_events.create_index("source_batch_id"),
            db.vending_operations.create_index("idempotency_key", unique=True),
            db.vending_operations.create_index("created_at"),
            db.listino_adm.create_index("adm_codice"),
            db.listino_adm.create_index("categoria_adm"),
            db.adm_sync.create_index("created_at"),
            db.app_migrations.create_index("id", unique=True),
            db.anomalie_ignorate.create_index("key", unique=True),
        )
        product_ids_created = await ensure_product_ids()
        if product_ids_created:
            logging.info("Assigned IDs to %s legacy products", product_ids_created)
        aliases_merged = await reconcile_adm_product_aliases()
        if aliases_merged:
            logging.info("Unified %s verified ADM product aliases", aliases_merged)
        products_restored = await restore_canonical_product_snapshots()
        if products_restored:
            logging.info("Restored %s canonical ADM product snapshots", products_restored)
        products_reconciled = await reconcile_all_vending_stock()
        if products_reconciled:
            logging.info("Reconciled %s product vending stock caches", products_reconciled)
        vending_column_migration = await migrate_vending_column_zero_padding(
            db, create_backup_snapshot
        )
        logging.info("Vending column zero-padding migration: %s", vending_column_migration)
        vending_cash_reconciliation = await reconcile_vending_cash_state()
        logging.info("Vending cash state reconciliation: %s", vending_cash_reconciliation)
        smart_venue_repair = await repair_smart_venue_auto_order_2026_10_12()
        if smart_venue_repair.get("righe_riparate"):
            logging.info(
                "Repaired Smart Venue for %s receipt rows (%s pieces)",
                smart_venue_repair["righe_riparate"],
                smart_venue_repair["pezzi_ripristinati"],
            )
    except Exception as e:
        logging.exception("seed failed: %s", e)
        if _is_production():
            raise


# ------------------------- Helpers -------------------------
def strip_id(d):
    if d and "_id" in d:
        d.pop("_id", None)
    return d


def _request_operator(request: Optional[Request]) -> str:
    """Restituisce l'utente Basic Auth senza accettare identità dal client."""
    if request is None:
        return "operatore-locale"
    return str(getattr(request.state, "authenticated_user", "operatore-locale") or "operatore-locale")


def _session_kwargs(session) -> Dict[str, Any]:
    return {"session": session} if session is not None else {}


async def _run_transactionally(operation):
    """Esegue un'operazione multi-documento in transazione quando Mongo lo supporta.

    I test in memoria e le installazioni Mongo standalone non espongono
    transazioni. In quei soli casi l'operazione usa i propri CAS e rollback
    compensativi; su replica set/Atlas viene invece garantito il commit atomico.
    """
    database_client = getattr(db, "client", None)
    if database_client is None or "mongomock" in type(database_client).__module__.lower():
        return await operation(None)

    start_session = getattr(database_client, "start_session", None)
    if start_session is None:
        if _requires_secure_runtime():
            raise RuntimeError("MongoDB non espone sessioni transazionali")
        return await operation(None)

    try:
        session = await start_session()
    except (AttributeError, NotImplementedError):
        if _requires_secure_runtime():
            raise RuntimeError("MongoDB non supporta sessioni transazionali") from None
        return await operation(None)

    try:
        with_transaction = getattr(session, "with_transaction", None)
        if with_transaction is None:
            if _requires_secure_runtime():
                raise RuntimeError("MongoDB non supporta transazioni multi-documento")
            return await operation(None)

        # Motor/PyMongo retries the whole callback for transient transaction
        # errors and retries uncertain commits. Callers that expose retries to
        # clients remain responsible for their business-level idempotency key.
        return await with_transaction(operation)
    except OperationFailure as exc:
        message = str(exc).lower()
        if exc.code in {20, 303} or "transaction numbers are only allowed" in message:
            if _requires_secure_runtime():
                raise RuntimeError(
                    "MongoDB ha rifiutato una transazione multi-documento fuori dall'ambiente locale"
                ) from exc
            return await operation(None)
        has_error_label = getattr(exc, "has_error_label", None)
        transient = bool(
            callable(has_error_label)
            and has_error_label("TransientTransactionError")
        )
        commit_unknown = bool(
            callable(has_error_label)
            and has_error_label("UnknownTransactionCommitResult")
        )
        if transient or commit_unknown:
            logging.warning(
                "MongoDB transaction outcome unresolved after driver retries (code=%s, commit_unknown=%s)",
                exc.code,
                commit_unknown,
            )
            detail = (
                "Esito del commit non confermato: verificare lo stato prima di riprovare"
                if commit_unknown
                else (
                    "Conflitto transazionale temporaneo: riprovare usando la stessa "
                    "chiave idempotente, quando prevista"
                )
            )
            raise HTTPException(
                status_code=503,
                detail=detail,
                headers=None if commit_unknown else {"Retry-After": "1"},
            ) from exc
        raise
    finally:
        end_session = getattr(session, "end_session", None)
        if end_session is not None:
            await end_session()


def lotto_for(categoria: str, params: Dict[str, float]) -> int:
    param_name, default = LOTTO_PARAM_BY_CATEGORY.get(
        categoria,
        LOTTO_PARAM_BY_CATEGORY["ACCESSORI"],
    )
    return int(params.get(param_name, default))


def _param(params: Dict[str, float], name: str, default: float) -> float:
    """Use the default only for a missing/None value; numeric zero is valid."""
    value = params.get(name)
    return default if value is None else value


def _auto_order_snapshot_key(ao: Dict[str, Any]) -> str:
    return auto_order_snapshot_key(ao)


def physical_shop_stock_from_excel(
    aggregate_remaining: Any, vending_stock: Any, vending_sold: Any
) -> int:
    """Convert Excel RIMANENZE to physical, freely available shop stock.

    RIMANENZE is aggregate and still includes units moved through/to vending.
    Normalize it once on import. Thereafter ``giacenza_negozio`` is authoritative
    physical free stock and Auto-Order must not subtract vending a second time.
    Negative results are retained so Auto-Order can report an ANOMALIA.
    """
    return int(aggregate_remaining or 0) - int(vending_stock or 0) - int(vending_sold or 0)


# ------------------------- Root / Health -------------------------
@api.get("/")
async def root():
    if _requires_secure_runtime():
        try:
            await asyncio.wait_for(client.admin.command("ping"), timeout=2)
        except Exception:
            logging.exception("Health check MongoDB non riuscito")
            return JSONResponse(
                {
                    "app": "God Services Gestionale Tabacchi",
                    "status": "error",
                    "database": "unavailable",
                },
                status_code=503,
            )
    return {
        "app": "God Services Gestionale Tabacchi",
        "status": "ok",
        "database": "ok",
    }


# ------------------------- Prodotti -------------------------
MAX_LIMIT = 5000
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


def _q_regex(q: str) -> Dict[str, Any]:
    """Safe regex from user input (escaped, bounded)."""
    return {"$regex": re.escape(q[:200]), "$options": "i"}


def _cap(limit: int) -> int:
    return max(1, min(int(limit or 0), MAX_LIMIT))


def _product_code_text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value or "").strip().upper()


def _description_tokens(value: Any) -> List[str]:
    text = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = text.encode("ascii", "ignore").decode().upper()
    token_aliases = {
        "BLUE": "BLU",
    }
    return [token_aliases.get(token, token) for token in re.findall(r"[A-Z0-9]+", ascii_text)]


def _product_identity_tokens(value: Any) -> tuple:
    """Return stable product-name tokens without packaging-only wording."""
    ignored = {
        "AST", "ASTUCCIO", "ASTUCCI", "CONF", "CONFEZIONE", "DA",
        "NUOVA", "NUOVE", "NUOVO", "NUOVI", "PEZZI", "PEZZO", "PZ",
    }
    source_tokens = _description_tokens(value)
    has_packaging = any(
        token in ignored or re.fullmatch(r"(?:(?:AST|PZ)\d+|\d+(?:AST|PZ))", token)
        for token in source_tokens
    )
    tokens = []
    for token in source_tokens:
        if token in ignored or re.fullmatch(r"(?:(?:AST|PZ)\d+|\d+(?:AST|PZ))", token):
            continue
        if token == "20" and has_packaging:
            continue
        tokens.append(token)
    return tuple(tokens)


def _same_adm_product(description: Any, adm_description: Any) -> bool:
    """Match name variants without confusing numeric accessory codes with ADM codes."""
    product_tokens = _description_tokens(description)
    adm_tokens = _description_tokens(adm_description)
    if not product_tokens or not adm_tokens:
        return False
    if product_tokens == adm_tokens:
        return True
    # Il numero ADM identico è già un segnale forte: lo stesso marchio consente
    # di riconoscere anche vecchi nomi commerciali molto diversi (BOX/KS/AST).
    if len(product_tokens[0]) >= 4 and product_tokens[0] == adm_tokens[0]:
        return True
    common = set(product_tokens) & set(adm_tokens)
    same_prefix = len(product_tokens) >= 2 and len(adm_tokens) >= 2 and product_tokens[:2] == adm_tokens[:2]
    coverage = len(common) / max(1, min(len(set(product_tokens)), len(set(adm_tokens))))
    return len(common) >= 2 and (same_prefix or coverage >= 0.6)


def _adm_alias_index(rows) -> Dict[str, List[Dict[str, str]]]:
    """Index listino rows by bare ADM code (96 -> AMMS96)."""
    aliases: Dict[str, List[Dict[str, str]]] = {}
    for row in rows:
        if isinstance(row, dict):
            raw_code = row.get("codice") or row.get("adm_codice")
            description = row.get("descrizione") or row.get("adm_descrizione") or ""
        else:
            raw_code = row[0] if row else None
            description = row[1] if len(row) > 1 else ""
        code = adm_numeric_code(raw_code)
        if not code or not str(raw_code or "").strip().upper().startswith("AMMS"):
            continue
        aliases.setdefault(code, []).append({
            "codice": adm_local_code(code),
            "descrizione": str(description or "").strip(),
        })
    return aliases


def _canonical_product_code(code: Any, description: Any, aliases: Dict[str, List[Dict[str, str]]]) -> str:
    raw = _product_code_text(code)
    if raw.startswith("AMMS"):
        return adm_local_code(raw)
    if not raw.isdigit():
        return raw
    numeric = raw.lstrip("0") or raw
    for candidate in aliases.get(numeric, []):
        if _same_adm_product(description, candidate["descrizione"]):
            return candidate["codice"]
    return raw


async def _vending_stock_by_code() -> Optional[Dict[str, int]]:
    """Return the physical vending stock grouped by product code.

    The vending columns are the sole source of truth for vending quantities.
    Product documents retain a synchronized cache for backwards compatibility,
    but every operational read uses this aggregation.
    """
    # Alcuni test unitari isolano soltanto le collezioni coinvolte. In quel
    # contesto manteniamo il valore cache del prodotto; nell'app reale la
    # collezione Vending è sempre disponibile, anche quando è vuota.
    if not hasattr(db, "vending"):
        return None
    rows = await db.vending.find({}, {"_id": 0, "codice": 1, "giacenza": 1}).to_list(5000)
    totals: Dict[str, int] = {}
    for row in rows:
        code = str(row.get("codice") or "").strip()
        if not code:
            continue
        totals[code] = totals.get(code, 0) + max(0, int(row.get("giacenza", 0) or 0))
    return totals


def _apply_vending_stock(products: List[Dict[str, Any]], totals: Optional[Dict[str, int]]) -> List[Dict[str, Any]]:
    if totals is None:
        return products
    for product in products:
        product["giacenza_vending"] = totals.get(str(product.get("codice") or ""), 0)
    return products


async def _remap_code_references(old_code: str, new_code: str) -> None:
    for collection_name in ("vendite", "db_storico_vend", "storico_ordini", "vending", "ordini_fornitore_righe"):
        await db[collection_name].update_many({"codice": old_code}, {"$set": {"codice": new_code}})


async def reconcile_adm_product_aliases() -> int:
    """Merge verified numeric/AMMS aliases already present in the database."""
    products = await db.prodotti.find({}, {"_id": 0}).to_list(MAX_LIMIT)
    listino = await db.listino_adm.find(
        {},
        {"_id": 0, "codice": 1, "adm_codice": 1, "descrizione": 1, "adm_descrizione": 1},
    ).to_list(MAX_LIMIT)
    aliases = _adm_alias_index(listino)
    candidates = []
    by_code = {str(product.get("codice") or "").strip().upper(): product for product in products}
    for product in products:
        old_code = _product_code_text(product.get("codice"))
        new_code = _canonical_product_code(old_code, product.get("descrizione"), aliases)
        if old_code != new_code:
            candidates.append((product, old_code, new_code))
    if not candidates:
        return 0

    await create_backup_snapshot("Prima unificazione codici ADM duplicati", "migrazione-codici-adm")
    merged = 0
    for alias, old_code, new_code in candidates:
        canonical = by_code.get(new_code)
        await _remap_code_references(old_code, new_code)
        alias_filter = {"id": alias["id"]} if alias.get("id") else {"codice": old_code}
        if canonical:
            merged_values = {
                "alias_unificati": sorted(set(canonical.get("alias_unificati") or []) | {old_code}),
            }
            canonical_filter = {"id": canonical["id"]} if canonical.get("id") else {"codice": new_code}
            await db.prodotti.update_one(canonical_filter, {"$set": merged_values})
            await db.prodotti.delete_one(alias_filter)
            canonical.update(merged_values)
        else:
            await db.prodotti.update_one(alias_filter, {"$set": {
                "codice": new_code,
                "categoria": _cat_from_desc(alias.get("descrizione", ""), new_code),
                "alias_unificati": sorted(set(alias.get("alias_unificati") or []) | {old_code}),
            }})
            by_code[new_code] = alias
        merged += 1
    return merged


async def restore_canonical_product_snapshots() -> int:
    """One-time repair for aliases that were initially merged by summing stale stock."""
    migration_id = "restore-canonical-adm-snapshots-v1"
    if await db.app_migrations.find_one({"id": migration_id}):
        return 0
    backup = await db.backup_snapshots.find_one(
        {"reason": "migrazione-codici-adm"},
        {"_id": 0, "id": 1},
        sort=[("created_at", 1)],
    )
    if not backup:
        return 0
    products = await db.prodotti.find(
        {"alias_unificati": {"$exists": True, "$ne": []}},
        {"_id": 0},
    ).to_list(MAX_LIMIT)
    if not products:
        return 0

    await create_backup_snapshot("Prima ripristino valori canonici ADM", "ripristino-codici-adm")
    restored = 0
    snapshot_fields = (
        "acquistati", "venduti_negozio", "venduti_vending",
        "giacenza_negozio", "giacenza_vending", "presente_ultimo_import", "ultimo_import_id",
    )
    for product in products:
        item = await db.backup_snapshot_items.find_one({
            "snapshot_id": backup["id"],
            "collection": "prodotti",
            "doc.codice": product["codice"],
        }, {"_id": 0, "doc": 1})
        original = (item or {}).get("doc")
        if not original:
            continue
        values = {field: original[field] for field in snapshot_fields if field in original}
        product_filter = {"id": product["id"]} if product.get("id") else {"codice": product["codice"]}
        await db.prodotti.update_one(product_filter, {"$set": values})
        restored += 1
    await db.app_migrations.insert_one({
        "id": migration_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "backup_id": backup["id"],
        "prodotti_ripristinati": restored,
    })
    return restored


async def reconcile_all_vending_stock() -> int:
    """One-time migration: make every cached product total match Vending."""
    migration_id = "vending-columns-authoritative-v1"
    if await db.app_migrations.find_one({"id": migration_id}):
        return 0
    products = await db.prodotti.find({}, {"_id": 0, "codice": 1, "giacenza_vending": 1}).to_list(MAX_LIMIT)
    if not products:
        return 0
    backup = await create_backup_snapshot(
        "Prima riconciliazione completa giacenze Vending",
        "vending-fonte-unica",
    )
    totals = await _vending_stock_by_code() or {}
    changed = 0
    for product in products:
        code = str(product.get("codice") or "")
        authoritative = totals.get(code, 0)
        if int(product.get("giacenza_vending", 0) or 0) == authoritative:
            continue
        result = await db.prodotti.update_one(
            {"codice": code},
            {"$set": {"giacenza_vending": authoritative}},
        )
        changed += int(result.modified_count or 0)
    await db.app_migrations.insert_one({
        "id": migration_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "backup_id": backup["id"],
        "prodotti_riconciliati": changed,
    })
    return changed


async def reconcile_vending_cash_state() -> Dict[str, Any]:
    """One-time repair of the operational vending cash cache.

    Accounting collections remain authoritative and are never changed here.
    """
    existing_migration = await db.app_migrations.find_one(
        {"id": VENDING_CASH_RECONCILIATION_ID}, {"_id": 0}
    )
    if existing_migration:
        return {"gia_applicata": True, **existing_migration}

    accounting_snapshot = await _vending_cash_accounting_snapshot()
    expected = money(accounting_snapshot.get("giacenza") or 0)
    state = await db.cassa_vending_stato.find_one(
        {"id": VENDING_CASH_STATE_ID}, {"_id": 0}
    )
    previous = money((state or {}).get("giacenza") or 0)
    changed = state is None or previous != expected
    backup_id = None
    now = datetime.now(timezone.utc).isoformat()

    if changed:
        backup = await create_backup_snapshot(
            "Prima del riallineamento saldo contanti vending",
            "riallineamento-saldo-contanti-vending",
            [
                "parametri",
                "db_storico_vend",
                "db_storico_vending_ext",
                "vendite",
                "prelievi_vending",
                "scontrini_vending",
                "cassa_vending_stato",
                "vending_accounting_snapshots",
                "vending_accounting_state",
                "vending_accounting_events",
                "audit_events",
            ],
            metadata={
                "saldo_tecnico_precedente": previous,
                "saldo_contabile_atteso": expected,
            },
        )
        backup_id = backup["id"]
        await db.cassa_vending_stato.update_one(
            {"id": VENDING_CASH_STATE_ID},
            {
                "$set": {
                    "id": VENDING_CASH_STATE_ID,
                    "giacenza": expected,
                    "origine": "CONTABILITA_DERIVATA",
                    "updated_at": now,
                    "reconciled_at": now,
                },
                "$setOnInsert": {"initialized_at": now},
            },
            upsert=True,
        )
        await _record_audit_event(
            "RICONCILIATO",
            "CASSA_VENDING_STATO",
            VENDING_CASH_STATE_ID,
            prima={"giacenza": previous},
            dopo={**accounting_snapshot, "giacenza": expected},
            idempotency_key=VENDING_CASH_RECONCILIATION_ID,
        )

    migration = {
        "id": VENDING_CASH_RECONCILIATION_ID,
        "created_at": now,
        "backup_id": backup_id,
        "modificato": changed,
        "saldo_tecnico_precedente": previous,
        "saldo_contabile_atteso": expected,
        "fonti": accounting_snapshot,
    }
    await db.app_migrations.insert_one(migration.copy())
    return {"gia_applicata": False, **migration}


async def _read_capped(file: UploadFile, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    """Read an UploadFile with a hard size cap; raise 413 if exceeded."""
    buf = bytearray()
    while True:
        chunk = await file.read(1024 * 64)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise HTTPException(413, f"File troppo grande (max {max_bytes // 1024 // 1024} MB)")
    return bytes(buf)


async def _restore_backup_snapshot_collections(snapshot_id: str) -> None:
    """Restore only the collections contained in a known internal snapshot."""
    meta = await db.backup_snapshots.find_one(
        {"id": snapshot_id, "status": {"$ne": "PRUNED"}},
        {"_id": 0, "counts": 1},
    )
    if not meta:
        raise RuntimeError(f"Backup di sicurezza {snapshot_id} non trovato")
    names = [name for name in (meta.get("counts") or {}) if name in BACKUP_COLLECTIONS]
    collections = await _backup_documents(snapshot_id, names)
    await _run_transactionally(lambda session: _restore_collections(collections, session))


# ------------------------- Register -------------------------
_is_electronic_inhalation = is_electronic_inhalation
_electronic_inhalation_filter = electronic_inhalation_filter
_cat_from_desc = category_from_description
excel_import = create_excel_import_router(
    get_database=lambda: db,
    read_capped=_read_capped,
    create_backup_snapshot=lambda *args, **kwargs: create_backup_snapshot(*args, **kwargs),
    restore_backup_collections=lambda snapshot_id: _restore_backup_snapshot_collections(snapshot_id),
    record_import_history=lambda *args, **kwargs: record_import_history(*args, **kwargs),
    import_smart_venue=lambda worksheet: _import_smart_venue(worksheet),
    sync_product_vending_stock=lambda *args, **kwargs: _sync_product_vending_stock(*args, **kwargs),
    product_model=Prodotto,
    historical_order_model=OrdineStorico,
    physical_shop_stock_from_excel=physical_shop_stock_from_excel,
    product_code_text=_product_code_text,
    canonical_product_code=_canonical_product_code,
    adm_alias_index=_adm_alias_index,
    remap_code_references=lambda *args, **kwargs: _remap_code_references(*args, **kwargs),
    normalize_nonnegative_money=nonnegative_money,
    normalize_money=money,
    decimal_money=money_decimal,
    apply_electronic_categories=lambda: _apply_electronic_inhalation_categories(),
    excel_import_backup_collections=EXCEL_IMPORT_BACKUP_COLLECTIONS,
)
excel_import_router = excel_import["router"]
IMPORT_PROGRESS = excel_import["IMPORT_PROGRESS"]
_set_import_progress = excel_import["set_import_progress"]
_bulk_upsert = excel_import["bulk_upsert"]
_bulk_update = excel_import["bulk_update"]
_apply_electronic_inhalation_categories = excel_import["apply_electronic_inhalation_categories"]
_import_prodotti = excel_import["import_products"]
_excel_header_key = excel_import["excel_header_key"]
_find_product_stock_sheet = excel_import["find_product_stock_sheet"]
_import_product_stock_sheet = excel_import["import_product_stock_sheet"]
_import_listino = excel_import["import_price_list"]
_import_vending = excel_import["import_vending"]
_import_storico = excel_import["import_order_history"]
_import_parametri = excel_import["import_parameters"]
_import_db_storico_vend = excel_import["import_historical_sales"]
_import_db_storico_vending_ext = excel_import["import_extended_vending_history"]
import_excel = excel_import["import_excel"]
import_excel_full_status = excel_import["import_excel_full_status"]
import_excel_full = excel_import["import_excel_full"]

csv_vending_parser = create_csv_vending_parser(
    normalize_nonnegative_money=nonnegative_money,
    sum_money=money_sum,
    payment_bucket=_vending_payment_bucket,
    product_code_text=_product_code_text,
    adm_numeric_code=adm_numeric_code,
)
_parse_csv_vending = csv_vending_parser["parse_csv_vending"]
_summarize_vending_csv = csv_vending_parser["summarize_vending_csv"]
_csv_vending_event_base = csv_vending_parser["csv_vending_event_base"]
_assign_csv_vending_event_keys = csv_vending_parser["assign_csv_vending_event_keys"]
_csv_vending_compatibility_key = csv_vending_parser["csv_vending_compatibility_key"]
_assign_csv_vending_accounting_keys = csv_vending_parser["assign_csv_vending_accounting_keys"]
csv_vending_accounting = create_csv_vending_accounting_router(
    get_database=lambda: db,
    read_capped=_read_capped,
    parse_csv_vending=_parse_csv_vending,
    summarize_vending_csv=_summarize_vending_csv,
    parse_sale_datetime=_parse_sale_datetime,
    filter_new_csv_vending_rows=_filter_new_csv_vending_rows,
    assign_event_keys=_assign_csv_vending_event_keys,
    compatibility_key=_csv_vending_compatibility_key,
    assign_accounting_keys=_assign_csv_vending_accounting_keys,
    local_sale_datetime=_local_sale_datetime,
    normalize_money=money,
    decimal_money=money_decimal,
    sum_money=money_sum,
    payment_bucket=_vending_payment_bucket,
    bulk_upsert=lambda *args, **kwargs: _bulk_upsert(*args, **kwargs),
    create_backup_snapshot=lambda *args, **kwargs: create_backup_snapshot(*args, **kwargs),
)
csv_vending_accounting_router = csv_vending_accounting["router"]
_exclude_imported_csv_vending_rows = csv_vending_accounting["exclude_imported_csv_vending_rows"]
_latest_restored_vending_snapshot_at = csv_vending_accounting["latest_restored_vending_snapshot_at"]
_annotate_csv_vending_stock_effects = csv_vending_accounting["annotate_csv_vending_stock_effects"]
_prepare_csv_vending_import = csv_vending_accounting["prepare_csv_vending_import"]
_latest_vending_accounting_snapshot = csv_vending_accounting["latest_vending_accounting_snapshot"]
_public_vending_accounting_snapshot = csv_vending_accounting["public_vending_accounting_snapshot"]
_ensure_vending_accounting_state = csv_vending_accounting["ensure_vending_accounting_state"]
_vending_accounting_ledger_summary = csv_vending_accounting["vending_accounting_ledger_summary"]
_store_vending_accounting_snapshot = csv_vending_accounting["store_vending_accounting_snapshot"]
preview_vending_accounting_csv = csv_vending_accounting["preview_vending_accounting_csv"]
align_vending_accounting_csv = csv_vending_accounting["align_vending_accounting_csv"]
vending_accounting_source = csv_vending_accounting["vending_accounting_source"]
vending = create_vending_router(
    get_database=lambda: db,
    get_params=lambda: get_params(),
    param_value=_param,
    product_code_text=_product_code_text,
    session_kwargs=_session_kwargs,
    run_transactionally=_run_transactionally,
)
vending_router = vending["router"]
create_vending_column = vending["create_vending_column"]
list_vending = vending["list_vending"]
_sync_product_vending_stock = vending["sync_product_vending_stock"]
update_vending_giacenza = vending["update_vending_giacenza"]
update_vending = vending["update_vending"]
ricarica_vending = vending["ricarica_vending"]
ricarica_vending_completa = vending["ricarica_vending_completa"]
vending_ricarica_pdf = vending["vending_ricarica_pdf"]
vending_giacenza_pdf = vending["vending_giacenza_pdf"]
sales = create_sales_router(
    get_database=lambda: db,
    cap_limit=_cap,
    product_code_text=_product_code_text,
    normalize_vending_column=_normalize_vending_column,
    session_kwargs=_session_kwargs,
    run_transactionally=lambda operation: _run_transactionally(operation),
    sync_product_vending_stock=lambda *args, **kwargs: _sync_product_vending_stock(*args, **kwargs),
    normalize_nonnegative_money=nonnegative_money,
    decimal_money=money_decimal,
    normalize_money=money,
    request_operator=_request_operator,
    cash_movement_model=MovimentoCassa,
    create_backup_snapshot=lambda *args, **kwargs: create_backup_snapshot(*args, **kwargs),
)
sales_router = sales["router"]
list_vendite = sales["list_vendite"]
add_vendita = sales["add_vendita"]
del_vendita = sales["del_vendita"]
ultima_vendita_manuale = sales["ultima_vendita_manuale"]
annulla_vendita_manuale = sales["annulla_vendita_manuale"]
bulk_vendite = sales["bulk_vendite"]
vendita_cassa_rapida = sales["vendita_cassa_rapida"]
annulla_vendita_cassa_rapida = sales["annulla_vendita_cassa_rapida"]
ultimo_bulk_vendite = sales["ultimo_bulk_vendite"]
annulla_bulk_vendite = sales["annulla_bulk_vendite"]
orders = create_orders_router(
    get_database=lambda: db,
    cap_limit=_cap,
    request_operator=_request_operator,
    session_kwargs=_session_kwargs,
    run_transactionally=_run_transactionally,
    create_backup_snapshot=lambda *args, **kwargs: create_backup_snapshot(*args, **kwargs),
    product_model=Prodotto,
    categorize_description=_cat_from_desc,
    normalize_nonnegative_money=nonnegative_money,
    record_audit_event=_record_audit_event,
    read_capped=_read_capped,
)
orders_router = orders["router"]
list_ordini = orders["list_ordini"]
add_ordine = orders["add_ordine"]
conferma_carico_smart_venue = orders["conferma_carico_smart_venue"]
del_ordine = orders["del_ordine"]
bulk_carico = orders["bulk_carico"]
preview_auto_order_excel = orders["preview_auto_order_excel"]
repair_smart_venue_auto_order_2026_10_12 = orders["repair_smart_venue_auto_order_2026_10_12"]
historical_smart_venue_metadata_migration = orders["historical_smart_venue_metadata_migration"]
backup = create_backup_router(
    get_database=lambda: db,
    session_kwargs=_session_kwargs,
    run_transactionally=_run_transactionally,
    read_capped=_read_capped,
)
backup_router = backup["router"]
create_backup_snapshot = backup["create_backup_snapshot"]
backup_retention_preview = backup["backup_retention_preview"]
prune_backup_snapshots = backup["prune_backup_snapshots"]
ensure_daily_backup = backup["ensure_daily_backup"]
schedule_daily_backup = backup["schedule_daily_backup"]
_backup_documents = backup["backup_documents"]
_validate_backup_file = backup["validate_backup_file"]
_restore_collections = backup["restore_collections"]
_restore_lock = backup["restore_lock"]
backup_create = backup["backup_create"]
backup_cleanup_preview = backup["backup_cleanup_preview"]
backup_cleanup = backup["backup_cleanup"]
backup_list = backup["backup_list"]
backup_download = backup["backup_download"]
backup_restore = backup["backup_restore"]
backup_restore_file = backup["backup_restore_file"]
inventory_analytics_router, prodotti_top, pivot = create_inventory_analytics_router(
    get_database=lambda: db,
    cap_limit=_cap,
    get_params=lambda: get_params(),
    vending_stock_by_code=_vending_stock_by_code,
    apply_vending_stock=_apply_vending_stock,
    money=money,
    money_decimal=money_decimal,
)
real_stock = create_real_stock_router(
    get_database=lambda: db,
    product_code_text=_product_code_text,
    request_operator=_request_operator,
    session_kwargs=_session_kwargs,
    run_transactionally=_run_transactionally,
    record_audit_event=_record_audit_event,
    cap_limit=_cap,
)
real_stock_router = real_stock["router"]
list_categorie_prodotti = real_stock["list_categorie_prodotti"]
list_giacenze_reali = real_stock["list_giacenze_reali"]
update_giacenza_reale = real_stock["update_giacenza_reale"]
storico_rettifiche_inventario = real_stock["storico_rettifiche_inventario"]
products = create_products_router(
    get_database=lambda: db,
    query_regex=_q_regex,
    cap_limit=_cap,
    vending_stock_by_code=_vending_stock_by_code,
    apply_vending_stock=_apply_vending_stock,
    product_code_text=_product_code_text,
    adm_numeric_code=adm_numeric_code,
    adm_local_code=adm_local_code,
    adm_alias_index=_adm_alias_index,
    canonical_product_code=_canonical_product_code,
    request_operator=_request_operator,
    session_kwargs=_session_kwargs,
    run_transactionally=_run_transactionally,
    normalize_money=money,
    multiply_money=money_product,
)
products_router = products["router"]
list_prodotti = products["list_prodotti"]
_canonical_manual_product_payload = products["canonical_manual_product_payload"]
create_prodotto = products["create_prodotto"]
update_prodotto = products["update_prodotto"]
update_venduti_negozio = products["update_venduti_negozio"]
delete_prodotto = products["delete_prodotto"]
(
    adm_router,
    list_listino,
    adm_categories,
    adm_sync,
    fit_status,
    fit_preview,
    fit_apply,
) = create_adm_router(
    get_database=lambda: db,
    cap_limit=_cap,
    fetch_categories=lambda: fetch_adm_categories_sync(),
    parse_pdf=lambda category: parse_adm_pdf_sync(category),
    fetch_fit_tariff=lambda: fetch_and_parse_fit_tariff_sync(),
    bulk_upsert=lambda collection, operations: _bulk_upsert(collection, operations),
    bulk_update=lambda collection, operations: _bulk_update(collection, operations),
    apply_electronic_inhalation_categories=lambda: _apply_electronic_inhalation_categories(),
    create_backup_snapshot=lambda *args, **kwargs: create_backup_snapshot(*args, **kwargs),
    record_audit_event=lambda *args, **kwargs: _record_audit_event(*args, **kwargs),
)
operations = create_operations_router(
    get_database=lambda: db,
    backup_collections=BACKUP_COLLECTIONS,
    parse_sale_date=_parse_sale_date,
    latest_sale_date=_latest_sale_date,
    query_regex=_q_regex,
    vending_stock_by_code=_vending_stock_by_code,
    apply_vending_stock=_apply_vending_stock,
    get_current_anomalies=lambda: anomalie(),
    get_dashboard=lambda: dashboard(),
    get_auto_order=lambda: auto_order(),
    schedule_daily_backup=lambda: schedule_daily_backup(),
)
operations_router = operations["router"]
data_status_payload = operations["data_status_payload"]
data_status = operations["data_status"]
global_search = operations["global_search"]
anomalie = operations["anomalie"]
dismiss_all_anomalies = operations["dismiss_all_anomalies"]
dismiss_anomaly = operations["dismiss_anomaly"]
report_giornaliero = operations["report_giornaliero"]
(
    import_history_router,
    record_import_history,
    _latest_accounting_import,
    import_history,
) = create_import_history_router(
    get_database=lambda: db,
)
cash_movements = create_cash_movements_router(
    get_database=lambda: db,
    record_audit_event=_record_audit_event,
    cap_limit=_cap,
    get_authoritative_vending_cash_snapshot=lambda: _vending_cash_accounting_snapshot(),
    run_transactionally=_run_transactionally,
)
cash_movements_router = cash_movements["router"]
_vending_cash_balance = cash_movements["vending_cash_balance"]
_adjust_vending_cash_balance = cash_movements["adjust_vending_cash_balance"]
_collection_money_total = cash_movements["collection_money_total"]
_versamenti_summary = cash_movements["versamenti_summary"]
list_versamenti = cash_movements["list_versamenti"]
add_versamento = cash_movements["add_versamento"]
del_versamento = cash_movements["del_versamento"]
_prelievi_vending_summary = cash_movements["prelievi_vending_summary"]
list_prelievi_vending = cash_movements["list_prelievi_vending"]
add_prelievo_vending = cash_movements["add_prelievo_vending"]
del_prelievo_vending = cash_movements["del_prelievo_vending"]
_scontrini_vending_summary = cash_movements["scontrini_vending_summary"]
list_scontrini_vending = cash_movements["list_scontrini_vending"]
add_scontrino_vending = cash_movements["add_scontrino_vending"]
del_scontrino_vending = cash_movements["del_scontrino_vending"]
list_cassa = cash_movements["list_cassa"]
add_cassa = cash_movements["add_cassa"]
del_cassa = cash_movements["del_cassa"]
csv_vending_sales = create_csv_vending_sales_router(
    get_database=lambda: db,
    read_capped=_read_capped,
    prepare_csv_vending_import=lambda *args, **kwargs: _prepare_csv_vending_import(*args, **kwargs),
    parse_csv_vending=_parse_csv_vending,
    summarize_vending_csv=_summarize_vending_csv,
    sum_money=money_sum,
    is_cash_payment=_is_cash_payment,
    session_kwargs=_session_kwargs,
    sync_product_vending_stock=lambda *args, **kwargs: _sync_product_vending_stock(*args, **kwargs),
    product_code_text=_product_code_text,
    adm_local_code=adm_local_code,
    sale_model=VenditaGiornaliera,
    run_transactionally=lambda operation: _run_transactionally(operation),
    ensure_vending_cash_balance=lambda: _vending_cash_balance(),
    adjust_vending_cash_balance=lambda *args, **kwargs: _adjust_vending_cash_balance(*args, **kwargs),
    get_store_vending_accounting_snapshot=lambda *args, **kwargs: _store_vending_accounting_snapshot(*args, **kwargs),
    accounting_state_id=VENDING_ACCOUNTING_STATE_ID,
    create_backup_snapshot=lambda *args, **kwargs: create_backup_snapshot(*args, **kwargs),
)
csv_vending_sales_router = csv_vending_sales["router"]
preview_csv_vending = csv_vending_sales["preview_csv_vending"]
_rollback_csv_import_sales = csv_vending_sales["rollback_csv_import_sales"]
import_csv_vending = csv_vending_sales["import_csv_vending"]
ultimo_csv_vending = csv_vending_sales["ultimo_csv_vending"]
annulla_csv_vending = csv_vending_sales["annulla_csv_vending"]
parameters_router, get_params, list_parametri, update_parametro = create_parameters_router(
    get_database=lambda: db,
    make_parameter=lambda nome, valore: Parametro(nome=nome, valore=valore).model_dump(),
    normalize_nonnegative_money=nonnegative_money,
    sum_money=money_sum,
    adjust_cash_balance=_adjust_vending_cash_balance,
    record_audit_event=_record_audit_event,
)
auto_order_module = create_auto_order_router(
    get_database=lambda: db,
    get_params=lambda: get_params(),
    param_value=_param,
    lot_for=lotto_for,
    vending_stock_by_code=_vending_stock_by_code,
    apply_vending_stock=_apply_vending_stock,
    normalize_money=money,
    sum_money=money_sum,
    multiply_money=money_product,
    get_auto_order=lambda: auto_order(),
)
auto_order_router = auto_order_module["router"]
auto_order = auto_order_module["auto_order"]
_auto_order_selected_rows = auto_order_module["auto_order_selected_rows"]
_auto_order_rows_from_query = auto_order_module["auto_order_rows_from_query"]
conferma_auto_order = auto_order_module["conferma_auto_order"]
auto_order_pdf = auto_order_module["auto_order_pdf"]
auto_order_excel = auto_order_module["auto_order_excel"]
dashboard_module = create_dashboard_router(
    get_database=lambda: db,
    normalize_money=money,
    decimal_money=money_decimal,
    sum_money=money_sum,
    get_pivot=lambda: pivot(),
    get_latest_accounting_import=lambda: _latest_accounting_import(),
    get_vending_accounting_ledger_summary=lambda: _vending_accounting_ledger_summary(),
    get_latest_vending_accounting_snapshot=lambda: _latest_vending_accounting_snapshot(),
    get_versamenti_summary=lambda: _versamenti_summary(),
    get_prelievi_vending_summary=lambda: _prelievi_vending_summary(),
    get_scontrini_vending_summary=lambda: _scontrini_vending_summary(),
    get_vending_list=lambda: list_vending(),
)
dashboard_router = dashboard_module["router"]
_vending_payment_values = dashboard_module["vending_payment_values"]
_latest_historical_vending_date = dashboard_module["latest_historical_vending_date"]
_calculate_dashboard_balances = dashboard_module["calculate_dashboard_balances"]
_dashboard_balances = dashboard_module["dashboard_balances"]
_vending_cash_accounting_snapshot = dashboard_module["vending_cash_accounting_snapshot"]
_supplemental_store_cash_sales = dashboard_module["supplemental_store_cash_sales"]
_shop_sold_accounting_corrections = dashboard_module["shop_sold_accounting_corrections"]
_dashboard_sales_trend = dashboard_module["dashboard_sales_trend"]
_sales_period_report = dashboard_module["sales_period_report"]
report_vendite = dashboard_module["report_vendite"]
dashboard = dashboard_module["dashboard"]
reconciliation_router, accounting_reconciliation, update_reconciliation_case = create_reconciliation_router(
    get_database=lambda: db,
    payment_bucket=_vending_payment_bucket,
    request_operator=_request_operator,
    record_audit_event=_record_audit_event,
)
smart_venue = create_smart_venue_router(
    get_database=lambda: db,
    read_capped=_read_capped,
    create_backup_snapshot=lambda *args, **kwargs: create_backup_snapshot(*args, **kwargs),
    record_import_history=lambda *args, **kwargs: record_import_history(*args, **kwargs),
    bulk_upsert=lambda *args, **kwargs: _bulk_upsert(*args, **kwargs),
    product_model=Prodotto,
    product_categories=PRODUCT_CATEGORIES,
    product_code_text=_product_code_text,
    product_identity_tokens=_product_identity_tokens,
    adm_numeric_code=adm_numeric_code,
    adm_local_code=adm_local_code,
    nonnegative_money=nonnegative_money,
    money_product=money_product,
    vending_stock_by_code=_vending_stock_by_code,
    run_transactionally=_run_transactionally,
    session_kwargs=_session_kwargs,
    request_operator=_request_operator,
    record_audit_event=_record_audit_event,
)
smart_venue_router = smart_venue["router"]
agent_integration_router = create_agent_integration_router(get_database=lambda: db)
_import_smart_venue = smart_venue["import_smart_venue"]
_parse_smart_venue_bulk_text = smart_venue["parse_smart_venue_bulk_text"]
_resolve_smart_venue_product_rows = smart_venue["resolve_smart_venue_product_rows"]
_smart_venue_bulk_comparison = smart_venue["smart_venue_bulk_comparison"]
preview_smart_venue_bulk = smart_venue["preview_smart_venue_bulk"]
import_smart_venue_bulk = smart_venue["import_smart_venue_bulk"]
import_smart_venue_excel = smart_venue["import_smart_venue_excel"]
list_smart_venue = smart_venue["list_smart_venue"]
create_smart_venue_product = smart_venue["create_smart_venue_product"]
update_smart_venue_barcode = smart_venue["update_smart_venue_barcode"]
confirm_smart_venue_exit = smart_venue["confirm_smart_venue_exit"]
confirm_smart_venue_difference = smart_venue["confirm_smart_venue_difference"]
confirm_smart_venue_entry = smart_venue["confirm_smart_venue_entry"]
smart_venue_reconciliation_diagnostic = smart_venue["smart_venue_reconciliation_diagnostic"]
delete_smart_venue_row = smart_venue["delete_smart_venue_row"]
api.include_router(reconciliation_router)
api.include_router(audit_router)
api.include_router(backup_router)
api.include_router(orders_router)
api.include_router(vending_router)
api.include_router(sales_router)
api.include_router(csv_vending_accounting_router)
api.include_router(real_stock_router)
api.include_router(inventory_analytics_router)
api.include_router(products_router)
api.include_router(adm_router)
api.include_router(operations_router)
api.include_router(import_history_router)
api.include_router(cash_movements_router)
api.include_router(csv_vending_sales_router)
api.include_router(excel_import_router)
api.include_router(parameters_router)
api.include_router(auto_order_router)
api.include_router(dashboard_router)
api.include_router(smart_venue_router)
api.include_router(agent_integration_router)
app.include_router(api)


@app.head("/", include_in_schema=False)
async def head_root():
    return Response(status_code=200)

_cors_origins = [o.strip() for o in os.environ.get('CORS_ORIGINS', '*').split(',') if o.strip()]
_cors_credentials = _cors_origins != ['*']  # wildcard + credentials è invalido; disabilita credentials se wildcard

app.add_middleware(
    CORSMiddleware,
    allow_credentials=_cors_credentials,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s - %(message)s')


# ------------------------------------------------------------------
# React production build — Render
# ------------------------------------------------------------------
FRONTEND_BUILD_DIR = ROOT_DIR.parent / "frontend" / "build"


def _frontend_index_response():
    # index.html contiene i nomi hashati degli asset del singolo deploy. Se il
    # browser conserva l'HTML di una rotta SPA (es. /prodotti), al deploy
    # successivo può richiedere un bundle ormai rimosso e mostrare pagina bianca.
    return FileResponse(
        FRONTEND_BUILD_DIR / "index.html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


if FRONTEND_BUILD_DIR.exists():
    static_dir = FRONTEND_BUILD_DIR / "static"

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(404, "API endpoint not found")
        build_root = FRONTEND_BUILD_DIR.resolve()
        requested = (build_root / full_path).resolve()

        try:
            requested.relative_to(build_root)
        except ValueError:
            requested = build_root / "index.html"

        if requested.is_file() and requested.name != "index.html":
            return FileResponse(requested)

        return _frontend_index_response()

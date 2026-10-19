from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, Request, Header
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
from pymongo.errors import BulkWriteError, OperationFailure
import os, json, logging, uuid, io, re, asyncio, math, unicodedata, hashlib
from contextlib import asynccontextmanager
from pathlib import Path
import base64
import secrets
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from decimal import Decimal

if __package__:
    from .adm_router import (
        ADM_CATEGORY_ALIASES, ADM_PREZZI_URL, ListinoItem,
        _adm_product_update_operations, adm_local_code, adm_numeric_code,
        create_adm_router, fetch_adm_categories_sync, parse_adm_pdf_sync,
        parse_italian_money,
    )
    from .audit_router import create_audit_router
    from .auto_order_router import (
        AutoOrderConfermaIn, AutoOrderRigaIn, auto_order_snapshot_key,
        create_auto_order_router,
    )
    from .backup_router import (
        BACKUP_COLLECTIONS, BACKUP_FILE_FORMAT, BACKUP_FILE_VERSION,
        BACKUP_RETENTION_LIMIT, BACKUP_RETENTION_POLICY_ID,
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
    from .import_history_router import (
        create_import_history_router,
        import_includes_accounting as _import_includes_accounting,
    )
    from .inventory_analytics_router import create_inventory_analytics_router
    from .money import Money, NonNegativeMoney, money, money_decimal, money_sum, money_product, nonnegative_money
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
    from .vending_router import VendingColonna, VendingCreateIn, create_vending_router
else:
    from adm_router import (
        ADM_CATEGORY_ALIASES, ADM_PREZZI_URL, ListinoItem,
        _adm_product_update_operations, adm_local_code, adm_numeric_code,
        create_adm_router, fetch_adm_categories_sync, parse_adm_pdf_sync,
        parse_italian_money,
    )
    from audit_router import create_audit_router
    from auto_order_router import (
        AutoOrderConfermaIn, AutoOrderRigaIn, auto_order_snapshot_key,
        create_auto_order_router,
    )
    from backup_router import (
        BACKUP_COLLECTIONS, BACKUP_FILE_FORMAT, BACKUP_FILE_VERSION,
        BACKUP_RETENTION_LIMIT, BACKUP_RETENTION_POLICY_ID,
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
    from import_history_router import (
        create_import_history_router,
        import_includes_accounting as _import_includes_accounting,
    )
    from inventory_analytics_router import create_inventory_analytics_router
    from money import Money, NonNegativeMoney, money, money_decimal, money_sum, money_product, nonnegative_money
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
    from vending_router import VendingColonna, VendingCreateIn, create_vending_router

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]


def _validate_runtime_security() -> None:
    """In produzione le credenziali devono essere complete e CORS non può essere aperto."""
    environment = str(os.environ.get("ENVIRONMENT") or "development").strip().lower()
    if environment not in {"production", "prod"}:
        return
    missing = [name for name in ("APP_USERNAME", "APP_PASSWORD") if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Configurazione produzione incompleta: manca {', '.join(missing)}")
    origins = [item.strip() for item in os.environ.get("CORS_ORIGINS", "").split(",") if item.strip()]
    if "*" in origins:
        raise RuntimeError("CORS_ORIGINS non può contenere '*' in produzione")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # on_start è risolto quando il lifespan viene eseguito, dopo il caricamento
    # completo del modulo.
    _validate_runtime_security()
    await on_start()
    try:
        yield
    finally:
        client.close()


app = FastAPI(title="God Services Gestionale Tabacchi", lifespan=lifespan)
api = APIRouter(prefix="/api")
audit_router, _record_audit_event, list_audit_events = create_audit_router(
    get_database=lambda: db,
)


@app.middleware("http")
async def optional_basic_auth(request: Request, call_next):
    """Protegge il deployment quando APP_USERNAME/APP_PASSWORD sono configurati."""
    username = os.environ.get("APP_USERNAME")
    password = os.environ.get("APP_PASSWORD")

    is_health_check = request.url.path == "/api/" or (
        request.method == "HEAD" and request.url.path == "/"
    )

    if not username or not password or is_health_check:
        request.state.authenticated_user = "operatore-locale"
        return await call_next(request)

    authorization = request.headers.get("Authorization", "")

    try:
        scheme, encoded = authorization.split(" ", 1)
        decoded = base64.b64decode(encoded).decode("utf-8")
        supplied_username, supplied_password = decoded.split(":", 1)

        authenticated = (
            scheme.lower() == "basic"
            and secrets.compare_digest(supplied_username, username)
            and secrets.compare_digest(supplied_password, password)
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
    return response


# ------------------------- Models -------------------------
class SmartVenueProductIn(BaseModel):
    codice: str
    descrizione: str
    barcode: str = ""
    categoria: str = "ACCESSORI"
    prezzo: NonNegativeMoney = 0
    acquistati: int = 0
    giacenza_negozio: int = 0
    giacenza_vending: int = 0
    smart_venue: int = 0


class SmartVenueBarcodeIn(BaseModel):
    barcode: str = ""


class SmartVenueBulkIn(BaseModel):
    testo: str


class Parametro(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nome: str
    valore: float
    descrizione: str = ""


# Stato effimero degli import in corso. Serve soltanto a mostrare alla UI la
# fase realmente raggiunta dal processo; i risultati definitivi restano nello
# storico import salvato su MongoDB.
IMPORT_PROGRESS: Dict[str, Dict[str, Any]] = {}


def _set_import_progress(
    job_id: Optional[str],
    status: str,
    message: str,
    current: int = 0,
    total: int = 1,
    **extra: Any,
) -> None:
    if not job_id:
        return
    safe_total = max(1, total)
    IMPORT_PROGRESS[job_id] = {
        "job_id": job_id,
        "status": status,
        "message": message,
        "current": current,
        "total": safe_total,
        "percent": min(100, max(0, round(current / safe_total * 100))),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


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
        # Gli import fanno upsert su queste chiavi: senza indici Atlas deve
        # scandire le collezioni per ogni riga, anche quando usiamo bulk_write.
        await asyncio.gather(
            db.prodotti.create_index("codice"),
            db.listino_adm.create_index("codice"),
            db.vending.create_index("colonna"),
            db.parametri.create_index("nome"),
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
            db.ordini_fornitore.create_index("batch_key", unique=True),
            db.ordini_fornitore_righe.create_index("batch_key"),
            db.ricezioni_merce.create_index("idempotency_key", unique=True),
            db.ricezioni_merce.create_index("created_at"),
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
        smart_venue_repair = await repair_smart_venue_auto_order_2026_10_12()
        if smart_venue_repair.get("righe_riparate"):
            logging.info(
                "Repaired Smart Venue for %s receipt rows (%s pieces)",
                smart_venue_repair["righe_riparate"],
                smart_venue_repair["pezzi_ripristinati"],
            )
    except Exception as e:
        logging.exception("seed failed: %s", e)


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
    try:
        async with await database_client.start_session() as session:
            async with session.start_transaction():
                return await operation(session)
    except (AttributeError, NotImplementedError):
        return await operation(None)
    except OperationFailure as exc:
        message = str(exc).lower()
        if exc.code in {20, 303} or "transaction numbers are only allowed" in message:
            return await operation(None)
        raise


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
    return {"app": "God Services Gestionale Tabacchi", "status": "ok"}


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


@api.post("/vendite/preview-csv-vending")
async def preview_csv_vending(file: UploadFile = File(...), pagamento: str = "CONTANTI"):
    """Mostra vendite nuove e impatto stock senza ripetere eventi già importati."""
    content = await _read_capped(file)
    raw = content.decode("utf-8-sig", errors="replace")
    parsed, cutoff, file_rows = await _prepare_csv_vending_import(raw, pagamento)
    accounting_summary = _summarize_vending_csv(_parse_csv_vending(raw, pagamento))
    payment_counts: Dict[str, int] = {}
    payment_amounts: Dict[str, float] = {}
    total = 0.0
    for row in parsed["righe"]:
        method = row["pagamento"]
        payment_counts[method] = payment_counts.get(method, 0) + 1
        payment_amounts[method] = money_sum([payment_amounts.get(method, 0), row["prezzo"]])
        total = money_sum([total, row["prezzo"]])
    dates = [row["data"][:10] for row in parsed["righe"]]
    return {
        "righe": len(parsed["righe"]),
        "saltati": parsed["saltati"],
        "errori": parsed["errori"],
        "delimitatore": parsed["delimitatore"],
        "pagamenti": payment_counts,
        "importi_pagamenti": payment_amounts,
        "totale": round(total, 2),
        "data_da": min(dates, default=None),
        "data_a": max(dates, default=None),
        "righe_file": file_rows,
        "righe_gia_presenti": file_rows - len(parsed["righe"]),
        "righe_storico_excel": parsed["righe_storico_excel"],
        "righe_csv_gia_importate": parsed["righe_csv_gia_importate"],
        "ultima_vendita_excel": cutoff.isoformat() if cutoff else None,
        "file_sha256": hashlib.sha256(content).hexdigest(),
        **parsed["stock"],
        "importabile": parsed["stock"]["importabile"] and not parsed["errori"],
        "contabilita_csv": accounting_summary,
        "vendite": parsed["righe"],
    }


async def _rollback_csv_import_sales(batch_id: str, session=None) -> Dict[str, Any]:
    """Compensa le sole vendite/scorte di un batch CSV non finalizzato."""
    kwargs = _session_kwargs(session)
    sales = await db.vendite.find(
        {"batch_id": batch_id, "sorgente": "CSV_VENDING"},
        {"_id": 0},
        **kwargs,
    ).to_list(None)
    removed = 0
    cash_total = 0.0
    touched_codes = set()
    for sale in sales:
        undo_meta = sale.get("undo_meta") or {}
        codice = str(sale.get("codice") or "")
        if undo_meta.get("product_stock_adjusted"):
            await db.prodotti.update_one(
                {"codice": codice},
                {"$inc": {"venduti_vending": -int(sale.get("quantita") or 0)}},
                **kwargs,
            )
        elif undo_meta.get("product_created"):
            product = await db.prodotti.find_one(
                {"id": undo_meta.get("product_id")}, {"_id": 0}, **kwargs
            )
            can_delete = product and product.get("created_from_csv_batch") == batch_id and all(
                int(product.get(field) or 0) == expected
                for field, expected in {
                    "acquistati": 0,
                    "venduti_negozio": 0,
                    "venduti_vending": 1,
                    "giacenza_negozio": 0,
                    "giacenza_vending": 0,
                }.items()
            )
            if can_delete:
                await db.prodotti.delete_one(
                    {"id": product["id"], "created_from_csv_batch": batch_id}, **kwargs
                )
            else:
                await db.prodotti.update_one(
                    {"codice": codice}, {"$inc": {"venduti_vending": -1}}, **kwargs
                )
        if undo_meta.get("vending_stock_decremented"):
            await db.vending.update_one(
                {"id": undo_meta.get("vending_column_id")},
                {"$inc": {"giacenza": int(sale.get("quantita") or 0)}},
                **kwargs,
            )
        if codice:
            touched_codes.add(codice)
        if _is_cash_payment(sale.get("pagamento")):
            cash_total = money_sum([cash_total, sale.get("importo") or 0])
        deletion = await db.vendite.delete_one(
            {"id": sale.get("id"), "batch_id": batch_id}, **kwargs
        )
        if deletion.deleted_count != 1:
            raise RuntimeError("Rollback CSV incompleto: una vendita non è stata eliminata")
        removed += 1
    for codice in touched_codes:
        await _sync_product_vending_stock(codice, session)
    return {"rimossi": removed, "contanti": round(cash_total, 2)}


@api.post("/vendite/import-csv-vending")
async def import_csv_vending(file: UploadFile = File(...), pagamento: str = "CONTANTI"):
    """Importa il CSV gia' validato dall'anteprima della UI."""
    content = await _read_capped(file)
    raw = content.decode("utf-8-sig", errors="replace")
    parsed, cutoff, file_rows = await _prepare_csv_vending_import(raw, pagamento)
    if parsed["errori"]:
        raise HTTPException(
            422,
            f"Il CSV contiene {len(parsed['errori'])} righe non valide: correggile prima dell'importazione",
        )
    stock_summary = parsed["stock"]
    if not stock_summary["importabile"]:
        details = []
        if stock_summary.get("colonne_non_trovate"):
            details.append(
                "colonne non configurate: "
                + ", ".join(stock_summary["colonne_non_trovate"])
            )
        if stock_summary["colonne_senza_riferimento_giacenza"]:
            details.append(
                "riferimento giacenza mancante per "
                + ", ".join(stock_summary["colonne_senza_riferimento_giacenza"])
            )
        if stock_summary["errori_giacenza"]:
            details.append("vendite superiori alla giacenza disponibile")
        raise HTTPException(409, "Import CSV bloccato: " + "; ".join(details))
    batch_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    file_sha256 = hashlib.sha256(content).hexdigest()
    inserted = 0
    imported_cash = 0.0
    errors = list(parsed["errori"])
    payment_counts: Dict[str, int] = {}
    payment_amounts: Dict[str, float] = {}
    for csv_row in parsed["righe"]:
        undo_meta: Dict[str, Any] = {}
        codice = ""
        try:
            codice = csv_row["codice"]
            nome = csv_row["nome"]
            prezzo_f = csv_row["prezzo"]
            data_iso = csv_row["data"]
            colonna = csv_row["colonna"]
            categoria = csv_row["categoria"]
            pag = csv_row["pagamento"]

            prod = None
            if codice:
                raw_code = _product_code_text(codice)
                candidates = [raw_code]
                if raw_code.isdigit() or raw_code.startswith("AMMS"):
                    candidates.append(adm_local_code(raw_code))
                prod = await db.prodotti.find_one({"codice": {"$in": list(dict.fromkeys(candidates))}})
                if prod:
                    codice = prod["codice"]
            # se codice manca prova a risolvere dal nome
            if not codice and nome:
                prod = await db.prodotti.find_one({"descrizione": {"$regex": f"^{re.escape(nome)}$", "$options": "i"}})
                if prod:
                    codice = prod["codice"]
            if not codice:
                raise ValueError(f"prodotto non riconosciuto: {nome or 'senza nome'}")

            # Scala soltanto vendite successive alla fotografia fisica della colonna.
            if colonna:
                col = await db.vending.find_one({"colonna": colonna})
                if col:
                    if codice and col.get("codice") and str(col.get("codice")) != codice:
                        raise ValueError(
                            f"colonna {colonna} associata a {col.get('codice')}, CSV indica {codice}"
                        )
                    undo_meta["vending_column_id"] = col["id"]
                    undo_meta["vending_stock_decremented"] = False
                    undo_meta["vending_stock_skip_reason"] = csv_row.get("giacenza_motivo")
                    if csv_row.get("scala_giacenza"):
                        result = await db.vending.update_one(
                            {"id": col["id"], "giacenza": {"$gte": 1}},
                            {"$inc": {"giacenza": -1}},
                        )
                        if result.modified_count != 1:
                            raise ValueError(f"giacenza insufficiente nella colonna {colonna}")
                        undo_meta["vending_stock_decremented"] = True
            # aggiorna prodotto
            prod = prod or await db.prodotti.find_one({"codice": codice})
            if prod:
                await db.prodotti.update_one({"codice": codice}, {"$inc": {"venduti_vending": 1}})
                undo_meta["product_stock_adjusted"] = True
                if colonna:
                    await _sync_product_vending_stock(codice)
            else:
                raise ValueError(f"prodotto non trovato: {codice}")

            # ogni riga CSV = 1 pezzo venduto (formato tipico distributore)
            v = VenditaGiornaliera(
                data=data_iso, codice=codice, descrizione=nome, quantita=1, importo=prezzo_f,
                canale="VENDING", pagamento=pag, sorgente="CSV_VENDING",
                colonna=colonna or None, batch_id=batch_id, undo_meta=undo_meta,
            )
            sale_doc = v.model_dump()
            sale_doc.update({
                "csv_event_key": csv_row["csv_event_key"],
                "csv_event_occurrence": csv_row["csv_event_occurrence"],
                "csv_event_compatibility_key": csv_row["csv_event_compatibility_key"],
                "csv_accounting_occurrence": csv_row["csv_accounting_occurrence"],
                "csv_accounting_key": csv_row["csv_accounting_key"],
                "csv_file_sha256": file_sha256,
                "stock_effect_applied": bool(undo_meta.get("vending_stock_decremented")),
                "stock_effect_reason": csv_row.get("giacenza_motivo"),
            })
            await db.vendite.insert_one(sale_doc)
            inserted += 1
            payment_counts[pag] = payment_counts.get(pag, 0) + 1
            payment_amounts[pag] = money_sum([payment_amounts.get(pag, 0), prezzo_f])
            if _is_cash_payment(pag):
                imported_cash = money_sum([imported_cash, prezzo_f])
        except Exception as exc:
            if undo_meta.get("product_stock_adjusted"):
                await db.prodotti.update_one(
                    {"codice": codice},
                    {"$inc": {"venduti_vending": -1}},
                )
            elif undo_meta.get("product_created"):
                await db.prodotti.delete_one({"id": undo_meta.get("product_id"), "created_from_csv_batch": batch_id})
            if undo_meta.get("vending_stock_decremented"):
                await db.vending.update_one(
                    {"id": undo_meta.get("vending_column_id")},
                    {"$inc": {"giacenza": 1}},
                )
            if codice:
                await _sync_product_vending_stock(codice)
            errors.append({"riga": csv_row["riga"], "errore": str(exc)})
    if errors:
        # Nessun effetto parziale: le righe già applicate vengono compensate
        # prima di restituire l'errore e la contabilità non viene aggiornata.
        await _run_transactionally(
            lambda session: _rollback_csv_import_sales(batch_id, session)
        )
        raise HTTPException(
            409,
            detail={
                "message": "Import CSV rifiutato: nessuna vendita registrata",
                "errori": errors,
            },
        )
    previous_accounting_state = await db.vending_accounting_state.find_one(
        {"id": VENDING_ACCOUNTING_STATE_ID}, {"_id": 0}
    )
    cash_adjusted = False
    try:
        if imported_cash:
            await _adjust_vending_cash_balance(imported_cash)
            cash_adjusted = True
        accounting_snapshot, _ = await _store_vending_accounting_snapshot(
            content,
            file.filename or "vendite-vending.csv",
            source_batch_id=batch_id if inserted else None,
        )
        if inserted:
            await db.vendite_bulk_imports.insert_one({
                "id": batch_id,
                "created_at": created_at,
                "sorgente": "CSV_VENDING",
                "canale": "VENDING",
                "pagamento": pagamento,
                "inseriti": inserted,
                "saltati": parsed["saltati"],
                "file_sha256": file_sha256,
                "file_name": Path(file.filename or "vendite-vending.csv").name,
                "accounting_snapshot_id": accounting_snapshot["id"],
                "previous_accounting_snapshot_id": accounting_snapshot.get("previous_snapshot_id"),
                "righe_file": file_rows,
                "righe_storico_excel": parsed["righe_storico_excel"],
                "righe_csv_gia_importate": parsed["righe_csv_gia_importate"],
                **stock_summary,
                "status": "active",
            })
    except Exception as exc:
        if inserted:
            await db.vending_accounting_events.delete_many({"source_batch_id": batch_id})
            await db.vendite_bulk_imports.delete_one({"id": batch_id})
        if previous_accounting_state is None:
            await db.vending_accounting_state.delete_one({"id": VENDING_ACCOUNTING_STATE_ID})
        else:
            await db.vending_accounting_state.replace_one(
                {"id": VENDING_ACCOUNTING_STATE_ID}, previous_accounting_state, upsert=True
            )
        if cash_adjusted:
            await _adjust_vending_cash_balance(-imported_cash)
        await _run_transactionally(
            lambda session: _rollback_csv_import_sales(batch_id, session)
        )
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(500, f"Import CSV annullato: {exc}") from exc
    return {
        "inseriti": inserted,
        "saltati": parsed["saltati"],
        "errori": errors,
        "delimitatore": parsed["delimitatore"],
        "pagamenti": payment_counts,
        "importi_pagamenti": payment_amounts,
        "contanti_aggiunti_giacenza": round(imported_cash, 2),
        "righe_file": file_rows,
        "righe_gia_presenti": file_rows - len(parsed["righe"]),
        "righe_storico_excel": parsed["righe_storico_excel"],
        "righe_csv_gia_importate": parsed["righe_csv_gia_importate"],
        "ultima_vendita_excel": cutoff.isoformat() if cutoff else None,
        "file_sha256": file_sha256,
        **stock_summary,
        "batch_id": batch_id if inserted else None,
        "created_at": created_at if inserted else None,
        "sorgente": "CSV_VENDING",
        "contabilita_allineata": True,
        "contabilita_snapshot_id": accounting_snapshot["id"],
        "contabilita_importi_pagamenti": accounting_snapshot.get("importi_pagamenti", {}),
        "contabilita_totale": accounting_snapshot.get("totale", 0),
    }


@api.get("/vendite/csv/ultimo")
async def ultimo_csv_vending():
    """Restituisce l'ultimo CSV vending ancora annullabile."""
    return await db.vendite_bulk_imports.find_one(
        {"status": "active", "sorgente": "CSV_VENDING"},
        {"_id": 0},
        sort=[("created_at", -1)],
    )


@api.post("/vendite/csv/{batch_id}/annulla")
async def annulla_csv_vending(batch_id: str):
    """Annulla un import CSV e ripristina vendite, prodotti, vending e cassa."""
    claim = await db.vendite_bulk_imports.update_one(
        {"id": batch_id, "status": "active", "sorgente": "CSV_VENDING"},
        {"$set": {"status": "annulling"}},
    )
    if claim.modified_count != 1:
        raise HTTPException(404, "Caricamento CSV non trovato o già annullato")

    batch = await db.vendite_bulk_imports.find_one({"id": batch_id}, {"_id": 0})
    async def operation(session):
        kwargs = _session_kwargs(session)
        rollback = await _rollback_csv_import_sales(batch_id, session)
        cash_to_restore = float(rollback["contanti"])
        if cash_to_restore:
            await _adjust_vending_cash_balance(-cash_to_restore, session)

        accounting_restored = False
        ledger_deletion = await db.vending_accounting_events.delete_many(
            {"source_batch_id": batch_id}, **kwargs
        )
        if int(getattr(ledger_deletion, "deleted_count", 0) or 0):
            accounting_restored = True
            await db.vending_accounting_state.update_one(
                {"id": VENDING_ACCOUNTING_STATE_ID},
                {"$set": {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "last_undo_batch_id": batch_id,
                }},
                **kwargs,
            )
        snapshot_id = (batch or {}).get("accounting_snapshot_id")
        if snapshot_id and snapshot_id != VENDING_ACCOUNTING_STATE_ID:
            snapshot = await db.vending_accounting_snapshots.find_one(
                {"id": snapshot_id, "status": "active"}, {"_id": 0}, **kwargs
            )
            if snapshot:
                reverted_at = datetime.now(timezone.utc).isoformat()
                await db.vending_accounting_snapshots.update_one(
                    {"id": snapshot_id, "status": "active"},
                    {"$set": {"status": "reverted", "reverted_at": reverted_at}},
                    **kwargs,
                )
                previous_id = snapshot.get("previous_snapshot_id")
                if previous_id:
                    await db.vending_accounting_snapshots.update_one(
                        {"id": previous_id, "status": "superseded"},
                        {"$set": {"status": "active", "reactivated_at": reverted_at}},
                        **kwargs,
                    )
                accounting_restored = True
        await db.vendite_bulk_imports.update_one(
            {"id": batch_id, "status": "annulling"},
            {"$set": {
                "status": "annulled",
                "annulled_at": datetime.now(timezone.utc).isoformat(),
                "rimossi": rollback["rimossi"],
            }},
            **kwargs,
        )
        return {
            "removed": rollback["rimossi"],
            "cash": cash_to_restore,
            "accounting_restored": accounting_restored,
        }

    try:
        result = await _run_transactionally(operation)
    except Exception as exc:
        await db.vendite_bulk_imports.update_one(
            {"id": batch_id, "status": "annulling"},
            {"$set": {"status": "active"}},
        )
        raise HTTPException(500, f"Impossibile annullare il CSV: {exc}") from exc

    return {
        "ok": True,
        "batch_id": batch_id,
        "rimossi": result["removed"],
        "contanti_rimossi_giacenza": round(result["cash"], 2),
        "contabilita_ripristinata": result["accounting_restored"],
    }




# ------------------------- Storico ordini -------------------------
# ------------------------- Import Excel -------------------------
@api.post("/import/excel")
async def import_excel(file: UploadFile = File(...)):
    try:
        import openpyxl
    except Exception:
        raise HTTPException(500, "openpyxl non installato")
    content = await _read_capped(file)
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, keep_vba=False)
    inserted = updated = 0
    if "RIEP_VENDITA" in wb.sheetnames:
        ws = wb["RIEP_VENDITA"]
        for row in ws.iter_rows(min_row=3, values_only=True):
            codice = row[0]
            if not codice:
                continue
            desc = row[1] or ""
            data_p = {
                "codice": str(codice).strip(),
                "descrizione": str(desc).strip(),
                "acquistati": int(row[2] or 0),
                "venduti_negozio": int(row[3] or 0),
                "prezzo": nonnegative_money(row[7] or 0),
                "giacenza_vending": int(row[13] or 0),
                "venduti_vending": int(row[17] or 0),
            }
            data_p["giacenza_negozio"] = physical_shop_stock_from_excel(
                row[6], data_p["giacenza_vending"], data_p["venduti_vending"]
            )
            insert_defaults = Prodotto(
                codice=data_p["codice"],
                descrizione=data_p["descrizione"],
            ).model_dump()
            insert_defaults = {k: v for k, v in insert_defaults.items() if k not in data_p}
            r = await db.prodotti.update_one(
                {"codice": data_p["codice"]},
                {"$set": data_p, "$setOnInsert": insert_defaults},
                upsert=True,
            )
            if r.upserted_id:
                inserted += 1
            else:
                updated += 1
    return {"ok": True, "inseriti": inserted, "aggiornati": updated}


# ------------------------- Import Excel FULL (multi-sheet sync) -------------------------
ELECTRONIC_INHALATION_CATEGORY = "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE ELETTRONICA"
ELECTRONIC_INHALATION_BRANDS = ("ELFBAR", "ELFLIQ", "LOST MARY", "KIWI", "ELFA", "ELFX", "VAPORESSO")


def _is_electronic_inhalation(desc: str) -> bool:
    d = " ".join((desc or "").upper().split())
    return any(brand in d for brand in ELECTRONIC_INHALATION_BRANDS)


def _electronic_inhalation_filter() -> Dict[str, Any]:
    return {"$or": [{"descrizione": {"$regex": re.escape(brand), "$options": "i"}} for brand in ELECTRONIC_INHALATION_BRANDS]}


async def _apply_electronic_inhalation_categories() -> int:
    """Reclassify ELFBAR/ELFLIQ/LOST MARY/KIWI even after ADM fallback to ACCESSORI."""
    result = await db.prodotti.update_many(
        _electronic_inhalation_filter(),
        {"$set": {"categoria": ELECTRONIC_INHALATION_CATEGORY}},
    )
    return int(result.modified_count or 0)


def _cat_from_desc(desc: str, code: str) -> str:
    d = (desc or "").upper()
    if _is_electronic_inhalation(d):
        return ELECTRONIC_INHALATION_CATEGORY
    for kw in ("VAPORESSO", "POD", "KIT", "MG/ML", "LIQUID", "TEREA"):
        if kw in d:
            return "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE"
    if str(code or "").startswith("AMMS"):
        return "SIGARETTE"
    return "ACCESSORI"


async def _bulk_upsert(collection, operations, batch_size: int = 1000) -> Dict[str, int]:
    """Esegue gli upsert in batch per evitare un round-trip Atlas per ogni riga."""
    inserted = updated = errors = 0
    for start in range(0, len(operations), batch_size):
        batch = operations[start:start + batch_size]
        try:
            result = await collection.bulk_write(batch, ordered=False)
            inserted += result.upserted_count
            updated += result.matched_count
        except BulkWriteError as exc:
            details = exc.details or {}
            inserted += details.get("nUpserted", 0)
            updated += details.get("nMatched", 0)
            errors += len(details.get("writeErrors", [])) or len(batch)
    return {"inseriti": inserted, "aggiornati": updated, "errori": errors}


async def _bulk_update(collection, operations, batch_size: int = 1000) -> Dict[str, int]:
    """Esegue aggiornamenti multipli in batch evitando un round-trip per riga."""
    matched = modified = errors = 0
    for start in range(0, len(operations), batch_size):
        batch = operations[start:start + batch_size]
        try:
            result = await collection.bulk_write(batch, ordered=False)
            matched += result.matched_count
            modified += result.modified_count
        except BulkWriteError as exc:
            details = exc.details or {}
            matched += details.get("nMatched", 0)
            modified += details.get("nModified", 0)
            errors += len(details.get("writeErrors", [])) or len(batch)
    return {"trovati": matched, "modificati": modified, "errori": errors}


async def _import_prodotti(ws, adm_aliases: Optional[Dict[str, List[Dict[str, str]]]] = None) -> Dict[str, int]:
    adm_aliases = adm_aliases or {}
    import_id = str(uuid.uuid4())
    codici_importati = set()
    alias_codes = set()
    alias_mappings: Dict[str, str] = {}
    products_by_code: Dict[str, Dict[str, Any]] = {}
    canonical_rows = set()
    valore_venduto_negozio_excel = Decimal(0)
    valore_venduto_vending_excel = Decimal(0)
    err = 0
    for row in ws.iter_rows(min_row=3, values_only=True):
        try:
            codice = row[0]
            if not codice:
                continue
            raw_code = _product_code_text(codice)
            desc = str(row[1] or "").strip()
            codice_s = _canonical_product_code(raw_code, desc, adm_aliases)
            if codice_s != raw_code:
                alias_codes.add(raw_code)
                alias_mappings[raw_code] = codice_s
            prezzo = nonnegative_money(row[7] or 0)
            venduti_negozio = int(row[3] or 0)
            venduti_vending = int(row[17] or 0)
            valore_venduto_negozio_excel += venduti_negozio * money_decimal(prezzo)
            valore_venduto_vending_excel += venduti_vending * money_decimal(prezzo)
            codici_importati.add(codice_s)
            row_data = {
                "codice": codice_s,
                "descrizione": desc,
                "categoria": _cat_from_desc(desc, codice_s),
                "acquistati": int(row[2] or 0),
                "venduti_negozio": venduti_negozio,
                "prezzo": prezzo,
                "giacenza_vending": int(row[13] or 0),
                "venduti_vending": venduti_vending,
                "presente_ultimo_import": True,
                "ultimo_import_id": import_id,
            }
            row_data["giacenza_negozio"] = physical_shop_stock_from_excel(
                row[6], row_data["giacenza_vending"], row_data["venduti_vending"]
            )
            existing = products_by_code.get(codice_s)
            if existing:
                if raw_code == codice_s:
                    products_by_code[codice_s] = row_data
                    canonical_rows.add(codice_s)
                elif codice_s not in canonical_rows and not existing.get("descrizione"):
                    products_by_code[codice_s] = row_data
            else:
                products_by_code[codice_s] = row_data
                if raw_code == codice_s:
                    canonical_rows.add(codice_s)
        except Exception:
            err += 1
    operations = []
    for code, data in products_by_code.items():
        insert_defaults = Prodotto(codice=code, descrizione=data["descrizione"]).model_dump()
        insert_defaults = {key: value for key, value in insert_defaults.items() if key not in data}
        operations.append(UpdateOne(
            {"codice": code},
            {"$set": data, "$setOnInsert": insert_defaults},
            upsert=True,
        ))
    result = await _bulk_upsert(db.prodotti, operations)
    if alias_codes:
        for old_code, new_code in alias_mappings.items():
            await _remap_code_references(old_code, new_code)
        await db.prodotti.delete_many({"codice": {"$in": sorted(alias_codes)}})
    if codici_importati:
        await db.prodotti.update_many(
            {"codice": {"$nin": list(codici_importati)}},
            {"$set": {"presente_ultimo_import": False}},
        )
    result["errori"] += err
    result["elettroniche"] = await _apply_electronic_inhalation_categories()
    result["alias_unificati"] = len(alias_codes)
    result["ultimo_import_id"] = import_id
    result["valore_venduto_negozio_excel"] = money(valore_venduto_negozio_excel)
    result["valore_venduto_vending_excel"] = money(valore_venduto_vending_excel)
    result["valore_venduto_totale_excel"] = money(valore_venduto_negozio_excel + valore_venduto_vending_excel)
    return result


def _excel_header_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = text.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", ascii_text)


def _find_product_stock_sheet(wb):
    """Trova un foglio tabellare con le colonne della fotografia giacenze."""
    aliases = {
        "codice": {"codice", "cod", "code"},
        "descrizione": {"descrizione", "descr", "prodotto"},
        "acquistati": {"acquistati", "acquisti"},
        "giacenza_negozio": {"giacenzanegozio", "giacnegozio", "rimanenzenegozio"},
        "giacenza_vending": {"giacenzavending", "giacvending", "rimanenzevending"},
    }
    required = set(aliases)
    for ws in wb.worksheets:
        for row_number, row in enumerate(
            ws.iter_rows(min_row=1, max_row=min(ws.max_row, 10), values_only=True),
            start=1,
        ):
            columns: Dict[str, int] = {}
            for index, value in enumerate(row):
                key = _excel_header_key(value)
                for field, names in aliases.items():
                    if key in names and field not in columns:
                        columns[field] = index
            if required.issubset(columns):
                return ws, row_number, columns
    return None


async def _import_product_stock_sheet(
    ws,
    header_row: int,
    columns: Dict[str, int],
    adm_aliases: Optional[Dict[str, List[Dict[str, str]]]] = None,
) -> Dict[str, int]:
    """Importa soltanto le giacenze da un foglio con intestazioni esplicite.

    ACQUISTATI viene usato esclusivamente per riconoscere il formato del file:
    un aggiornamento inventariale non deve mai alterare la contabilità.
    """
    adm_aliases = adm_aliases or {}
    rows_by_code: Dict[str, Dict[str, Any]] = {}
    errors = 0
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        try:
            raw_code = row[columns["codice"]] if len(row) > columns["codice"] else None
            if raw_code in (None, ""):
                continue
            description = str(row[columns["descrizione"]] or "").strip()
            code = _canonical_product_code(raw_code, description, adm_aliases)
            if not code:
                continue
            rows_by_code[code] = {
                "codice": code,
                "descrizione": description,
                "giacenza_negozio": int(row[columns["giacenza_negozio"]] or 0),
                "giacenza_vending": int(row[columns["giacenza_vending"]] or 0),
            }
        except (IndexError, TypeError, ValueError):
            errors += 1

    operations = []
    for code, data in rows_by_code.items():
        insert_defaults = Prodotto(codice=code, descrizione=data["descrizione"]).model_dump()
        insert_defaults = {key: value for key, value in insert_defaults.items() if key not in data}
        operations.append(UpdateOne(
            {"codice": code},
            {"$set": data, "$setOnInsert": insert_defaults},
            upsert=True,
        ))
    result = await _bulk_upsert(db.prodotti, operations)
    result["errori"] += errors
    result["righe_lette"] = len(rows_by_code)
    return result


async def _import_listino(ws) -> Dict[str, int]:
    operations = []
    err = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        try:
            if not row[0]:
                continue
            codice = str(row[0]).strip()
            data_p = {
                "codice": codice,
                "descrizione": str(row[1] or "").strip(),
                "prezzo": nonnegative_money(row[2] or 0),
                "confezione": str(row[3] or "").strip(),
            }
            operations.append(UpdateOne({"codice": codice}, {"$set": data_p}, upsert=True))
        except Exception:
            err += 1
    result = await _bulk_upsert(db.listino_adm, operations)
    result["errori"] += err
    return result


async def _import_vending(ws) -> Dict[str, int]:
    operations = []
    codici_importati = set()
    err = 0
    snapshot_at = datetime.now(timezone.utc).isoformat()
    for row in ws.iter_rows(min_row=4, values_only=True):
        try:
            if not row[0]:
                continue
            colonna = str(row[0]).strip()
            if not re.match(r"^[A-Z]\d+$", colonna):
                continue
            data_p = {
                "colonna": colonna,
                "codice": str(row[1] or "").strip(),
                "descrizione": str(row[2] or "").strip(),
                "giacenza": int(row[3]) if row[3] is not None else 0,
                "capacita_max": int(row[4] or 5),
                "soglia_minima": int(row[5]) if row[5] is not None else 2,
                "giacenza_aggiornata_il": snapshot_at,
                "giacenza_sorgente": "IMPORT_EXCEL",
            }
            operations.append(UpdateOne({"colonna": colonna}, {"$set": data_p}, upsert=True))
            if data_p["codice"]:
                codici_importati.add(data_p["codice"])
        except Exception:
            err += 1
    result = await _bulk_upsert(db.vending, operations)
    # La giacenza registrata nelle singole colonne vending e' la fotografia
    # fisica autorevole. RIEP_VENDITA puo' contenere un totale vecchio o anche
    # negativo: dopo l'import delle colonne riallineiamo quindi il totale del
    # prodotto alla loro somma, cosi' Prodotti, Magazzino e Smart Venue leggono
    # tutti la stessa giacenza reale.
    for codice in sorted(codici_importati):
        await _sync_product_vending_stock(codice)
    result["prodotti_riconciliati"] = len(codici_importati)
    result["errori"] += err
    return result


async def _import_smart_venue(ws) -> Dict[str, int]:
    """Importa la fotografia SMART VENUE dal relativo foglio Excel.

    Il foglio usa la colonna E (codice2) come barcode e la colonna F
    (RIMANENZE4) come quantità presente in SMART VENUE. Le rimanenze del
    gestionale non vengono importate da qui: vengono sempre calcolate dai
    prodotti al momento della lettura.
    """
    rows_by_code: Dict[str, Dict[str, Any]] = {}
    errors = 0
    for row_number, row in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
        try:
            if not row or not row[0]:
                continue
            code = _product_code_text(row[0])
            if not code:
                continue
            rows_by_code[code] = {
                "id": f"smart-venue:{code}",
                "codice": code,
                "descrizione": str(row[1] or "").strip(),
                "smart_venue": int(row[5] or 0) if len(row) > 5 else 0,
                "origine": "EXCEL",
                "barcode": str(row[4] or "").strip() if len(row) > 4 else "",
                "riga_excel": row_number,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        except (TypeError, ValueError):
            errors += 1

    manual_rows = await db.smart_venue.find(
        {"origine": "MANUALE"}, {"_id": 0}
    ).to_list(5000)
    for manual_row in manual_rows:
        code = str(manual_row.get("codice") or "")
        if code and code not in rows_by_code:
            rows_by_code[code] = manual_row

    await db.smart_venue.delete_many({})
    if rows_by_code:
        await db.smart_venue.insert_many(list(rows_by_code.values()))
    return {"inseriti": len(rows_by_code), "aggiornati": 0, "errori": errors}


def _parse_smart_venue_bulk_text(text: str) -> Dict[str, Any]:
    """Parse the four-line copy format exported by the SmartVenue website."""
    lines = [line.strip() for line in str(text or "").replace("\r", "").split("\n") if line.strip()]
    errors: List[Dict[str, Any]] = []
    grouped: Dict[str, Dict[str, Any]] = {}
    if not lines:
        return {"righe": [], "errori": [{"riga": 1, "errore": "Nessun dato incollato"}], "record": 0}
    if len(lines) % 4:
        errors.append({
            "riga": len(lines) - (len(lines) % 4) + 1,
            "errore": "Il testo deve contenere blocchi completi di 4 righe",
        })

    for start in range(0, len(lines) - (len(lines) % 4), 4):
        header, price_raw, quantity_raw, value_raw = lines[start:start + 4]
        try:
            left, adm_code, category = header.rsplit(" - ", 2)
            barcode, description = left.split(" - ", 1)
            numeric_code = adm_numeric_code(adm_code)
            if not numeric_code:
                raise ValueError("codice ADM mancante")
            code = adm_local_code(numeric_code)
            price = nonnegative_money(price_raw)
            quantity_float = float(quantity_raw.replace(",", "."))
            if not quantity_float.is_integer() or quantity_float < 0:
                raise ValueError("quantità non valida")
            quantity = int(quantity_float)
            total_value = nonnegative_money(value_raw)
            expected_value = money_product(price, quantity)
            warning = None
            if abs(expected_value - total_value) > 0.02:
                warning = f"valore {total_value:.2f} diverso da prezzo × quantità {expected_value:.2f}"

            current = grouped.setdefault(code, {
                "codice": code,
                "descrizione": description.strip(),
                "categoria": category.strip(),
                "smart_venue": 0,
                "barcodes": [],
                "righe_sorgente": [],
                "avvisi": [],
            })
            current["smart_venue"] += quantity
            if barcode.strip() and barcode.strip() not in current["barcodes"]:
                current["barcodes"].append(barcode.strip())
            current["righe_sorgente"].append(start // 4 + 1)
            if warning:
                current["avvisi"].append(warning)
        except (ValueError, TypeError) as exc:
            errors.append({"riga": start + 1, "errore": str(exc)})

    return {
        "righe": sorted(grouped.values(), key=lambda row: row["codice"]),
        "errori": errors,
        "record": len(lines) // 4,
    }


def _resolve_smart_venue_product_rows(
    rows: List[Dict[str, Any]],
    products: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Resolve renamed ADM codes and collapse duplicate old/new-code rows.

    SmartVenue can retain an obsolete ADM row while exposing the same physical
    stock on its replacement code. We only infer an alias when a normalized
    description identifies exactly one product in the management database.
    Quantities from old/new aliases are snapshots of the same stock, so the
    maximum is retained instead of summing them twice.
    """
    products_by_code = {str(product.get("codice") or ""): product for product in products}
    products_by_identity: Dict[tuple, List[Dict[str, Any]]] = {}
    for product in products:
        identity = _product_identity_tokens(product.get("descrizione"))
        if identity:
            products_by_identity.setdefault(identity, []).append(product)

    resolved: Dict[str, Dict[str, Any]] = {}
    for source in rows:
        row = dict(source)
        requested_code = str(row.get("codice") or "")
        product = products_by_code.get(requested_code)
        if product is None:
            candidates = products_by_identity.get(_product_identity_tokens(row.get("descrizione")), [])
            if len(candidates) == 1:
                product = candidates[0]
        canonical_code = str((product or {}).get("codice") or requested_code)
        row["codice"] = canonical_code

        current = resolved.get(canonical_code)
        if current is None:
            row["codici_sorgente"] = [requested_code] if requested_code else []
            resolved[canonical_code] = row
            continue

        current["smart_venue"] = max(
            int(current.get("smart_venue", 0) or 0),
            int(row.get("smart_venue", 0) or 0),
        )
        for field in ("barcodes", "righe_sorgente", "avvisi", "codici_sorgente"):
            incoming = row.get(field, [])
            if field == "codici_sorgente":
                incoming = [requested_code] if requested_code else []
            current[field] = list(dict.fromkeys([*(current.get(field) or []), *incoming]))

    return sorted(resolved.values(), key=lambda row: row["codice"])


async def _smart_venue_bulk_comparison(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    products, vending_totals = await asyncio.gather(
        db.prodotti.find({}, {"_id": 0}).to_list(5000),
        _vending_stock_by_code(),
    )
    rows = _resolve_smart_venue_product_rows(rows, products)
    products_by_code = {str(product.get("codice") or ""): product for product in products}
    vending_totals = vending_totals or {}
    products_by_numeric: Dict[str, Dict[str, Any]] = {}
    for product in products:
        numeric = adm_numeric_code(product.get("codice"))
        if numeric:
            current = products_by_numeric.get(numeric)
            if current is None or str(product.get("codice") or "").startswith("AMMS"):
                products_by_numeric[numeric] = product

    compared = []
    for row in rows:
        requested_code = str(row.get("codice") or "")
        product = products_by_code.get(requested_code) or products_by_numeric.get(adm_numeric_code(requested_code))
        code = str((product or {}).get("codice") or requested_code)
        smart_total = int(row.get("smart_venue", 0) or 0)
        vending_total = int(vending_totals.get(code, 0) or 0)
        shop_total = int((product or {}).get("giacenza_negozio", 0) or 0)
        management_total = shop_total + vending_total
        inferred_shop = smart_total - vending_total
        difference = smart_total - management_total
        if not product:
            status = "CODICE MANCANTE"
        elif inferred_shop < 0:
            status = "ANOMALIA VENDING"
        elif difference == 0:
            status = "OK"
        else:
            status = "DIFFERENZA"
        compared.append({
            **row,
            "codice": code,
            "prodotto_trovato": bool(product),
            "giacenza_vending": vending_total,
            "giacenza_negozio": shop_total,
            "gestionale_totale": management_total,
            "negozio_calcolato": inferred_shop,
            "differenza": difference,
            "stato": status,
        })
    return compared


@api.post("/smart-venue/bulk/preview")
async def preview_smart_venue_bulk(body: SmartVenueBulkIn):
    parsed = _parse_smart_venue_bulk_text(body.testo)
    rows = await _smart_venue_bulk_comparison(parsed["righe"])
    return {
        **parsed,
        "righe": rows,
        "prodotti": len(rows),
        "totale_smart_venue": sum(row["smart_venue"] for row in rows),
        "differenze": sum(1 for row in rows if row["stato"] != "OK"),
    }


@api.post("/smart-venue/bulk/import")
async def import_smart_venue_bulk(body: SmartVenueBulkIn):
    parsed = _parse_smart_venue_bulk_text(body.testo)
    if parsed["errori"]:
        raise HTTPException(422, {"message": "Correggi gli errori prima di salvare", "errori": parsed["errori"]})
    if not parsed["righe"]:
        raise HTTPException(422, "Nessun prodotto valido")
    products = await db.prodotti.find({}, {"_id": 0}).to_list(5000)
    resolved_rows = _resolve_smart_venue_product_rows(parsed["righe"], products)
    backup = await create_backup_snapshot(
        "Prima import testo SmartVenue",
        "pre-import-smart-venue-bulk",
        ["smart_venue", "smart_venue_hidden", "smart_venue_values"],
    )
    batch_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    imported_codes = []
    operations = []
    for row in resolved_rows:
        code = row["codice"]
        imported_codes.append(code)
        operations.append(UpdateOne(
            {"codice": code},
            {"$set": {
                "id": f"smart-venue:{code}",
                "codice": code,
                "descrizione": row["descrizione"],
                "smart_venue": row["smart_venue"],
                "barcode": row["barcodes"][0] if row["barcodes"] else "",
                "barcodes": row["barcodes"],
                "origine": "BULK_TESTO",
                "batch_id": batch_id,
                "updated_at": now,
            }},
            upsert=True,
        ))
    write_result = await _bulk_upsert(db.smart_venue, operations)
    if write_result["errori"]:
        raise HTTPException(500, "Salvataggio SmartVenue incompleto: riprova")
    await db.smart_venue.delete_many({
        "origine": "BULK_TESTO",
        "codice": {"$nin": imported_codes},
    })
    rows = await _smart_venue_bulk_comparison(resolved_rows)
    response = {
        "ok": True,
        "batch_id": batch_id,
        "backup_id": backup["id"],
        "created_at": now,
        "righe": rows,
        "prodotti": len(rows),
        "totale_smart_venue": sum(row["smart_venue"] for row in rows),
        "differenze": sum(1 for row in rows if row["stato"] != "OK"),
        "errori": [],
    }
    await record_import_history("Testo SmartVenue", response, backup["id"])
    return response


@api.post("/smart-venue/import-excel")
async def import_smart_venue_excel(file: UploadFile = File(...)):
    """Aggiorna barcode e SMART VENUE dalle colonne E/F del relativo foglio."""
    try:
        import openpyxl
    except Exception:
        raise HTTPException(500, "openpyxl non installato")
    content = await _read_capped(file)
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True, keep_vba=False)
    except Exception as exc:
        raise HTTPException(422, f"File non leggibile: {exc}")
    if "(SMART VENUE)" not in workbook.sheetnames:
        raise HTTPException(422, "Foglio (SMART VENUE) non trovato")

    backup = await create_backup_snapshot(
        f"Prima import SMART VENUE {file.filename or 'Excel'}",
        "pre-import-smart-venue",
    )
    result = await _import_smart_venue(workbook["(SMART VENUE)"])
    response = {
        "ok": True,
        "file": file.filename,
        "backup_id": backup["id"],
        "tipo_import": "smart_venue",
        "contabilita_inclusa": False,
        "fogli_trovati": ["(SMART VENUE)"],
        "fogli_mancanti": [],
        "dettaglio": {"(SMART VENUE)": result},
        "totali": {"smart_venue_righe": result["inseriti"]},
    }
    await record_import_history(file.filename or "Excel SMART VENUE", response, backup["id"])
    return response


@api.get("/smart-venue")
async def list_smart_venue():
    hidden_rows, smart_rows, products, vending_totals = await asyncio.gather(
        db.smart_venue_hidden.find({}, {"_id": 0, "product_id": 1}).to_list(5000),
        db.smart_venue.find({}, {"_id": 0}).sort("codice", 1).to_list(5000),
        db.prodotti.find({}, {"_id": 0}).sort("codice", 1).to_list(5000),
        _vending_stock_by_code(),
    )
    _apply_vending_stock(products, vending_totals)
    smart_rows = _resolve_smart_venue_product_rows(smart_rows, products)
    hidden_ids = {str(row.get("product_id")) for row in hidden_rows if row.get("product_id")}
    products_by_code = {str(row.get("codice") or ""): row for row in products}
    rows = []
    for smart_row in smart_rows:
        row_id = str(smart_row.get("id") or f"smart-venue:{smart_row.get('codice', '')}")
        if row_id in hidden_ids:
            continue
        code = str(smart_row.get("codice") or "")
        product = products_by_code.get(code, {})
        giacenza_negozio = int(product.get("giacenza_negozio", 0) or 0)
        giacenza_vending = int(product.get("giacenza_vending", 0) or 0)
        rimanenze = giacenza_negozio + giacenza_vending
        smart_venue = int(smart_row.get("smart_venue", smart_row.get("rimanenze_smart", 0)) or 0)
        difference = smart_venue - rimanenze
        inferred_shop = smart_venue - giacenza_vending
        if not product:
            status = "CODICE MANCANTE"
        elif inferred_shop < 0:
            status = "ANOMALIA VENDING"
        elif difference == 0:
            status = "OK"
        else:
            status = "DIFFERENZA"
        rows.append({
            "id": row_id,
            "codice": code,
            "descrizione": str(smart_row.get("descrizione") or product.get("descrizione") or ""),
            # Acquistati e giacenze appartengono al prodotto reale. Il foglio
            # SMART VENUE fornisce soltanto la sua fotografia di confronto.
            "acquistati": int(product.get("acquistati", 0) or 0),
            "rimanenze": rimanenze,
            "giacenza_negozio": giacenza_negozio,
            "giacenza_vending": giacenza_vending,
            "gestionale_totale": rimanenze,
            "negozio_calcolato": inferred_shop,
            "smart_venue": smart_venue,
            "barcode": str(
                smart_row.get("barcode", smart_row.get("codice_smart", "")) or ""
            ),
            "barcodes": smart_row.get("barcodes") or [],
            "differenza": difference,
            "stato": status,
        })
    return rows


@api.post("/smart-venue")
async def create_smart_venue_product(body: SmartVenueProductIn):
    code = _product_code_text(body.codice)
    description = str(body.descrizione or "").strip()
    category = str(body.categoria or "").strip().upper()
    if not code or not description:
        raise HTTPException(422, "Codice e descrizione sono obbligatori")
    if category not in PRODUCT_CATEGORIES:
        raise HTTPException(422, "Categoria prodotto non valida")
    for field in ("acquistati", "giacenza_negozio", "smart_venue"):
        if getattr(body, field) < 0:
            raise HTTPException(422, f"{field.replace('_', ' ').capitalize()} non può essere negativo")
    if body.prezzo < 0:
        raise HTTPException(422, "Prezzo non può essere negativo")
    if await db.smart_venue.find_one({"codice": code}):
        raise HTTPException(409, f"Codice già presente in SMART VENUE: {code}")

    product = await db.prodotti.find_one({"codice": code}, {"_id": 0})
    if product is None:
        product = Prodotto(
            codice=code,
            descrizione=description,
            categoria=category,
            prezzo=body.prezzo,
            acquistati=body.acquistati,
            giacenza_negozio=body.giacenza_negozio,
            giacenza_vending=0,
        ).model_dump()
        await db.prodotti.insert_one(product)

    row_id = f"smart-venue:{code}"
    smart_row = {
        "id": row_id,
        "codice": code,
        "descrizione": description,
        "barcode": str(body.barcode or "").strip(),
        "smart_venue": body.smart_venue,
        "origine": "MANUALE",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Mantieni la risposta priva dell'ObjectId aggiunto in-place da PyMongo.
    await db.smart_venue.insert_one(dict(smart_row))
    await db.smart_venue_hidden.delete_many({"product_id": row_id})
    rimanenze = int(product.get("giacenza_negozio", 0) or 0) + int(product.get("giacenza_vending", 0) or 0)
    return {
        **smart_row,
        "descrizione": str(product.get("descrizione") or description),
        "acquistati": int(product.get("acquistati", 0) or 0),
        "rimanenze": rimanenze,
        "differenza": body.smart_venue - rimanenze,
    }


@api.put("/smart-venue/{row_id}/barcode")
async def update_smart_venue_barcode(row_id: str, body: SmartVenueBarcodeIn):
    barcode = str(body.barcode or "").strip()
    if len(barcode) > 100:
        raise HTTPException(422, "Barcode troppo lungo")
    result = await db.smart_venue.update_one(
        {"id": row_id},
        {"$set": {
            "barcode": barcode,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Riga SMARTV VENUE non trovata")
    return {"ok": True, "id": row_id, "barcode": barcode}


def _parse_smart_venue_quantity(value: Any) -> int:
    if isinstance(value, bool):
        raise HTTPException(422, "Inserimento Smart Venue non valido")
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        raise HTTPException(422, "Inserimento Smart Venue non valido")
    if isinstance(value, float) and not value.is_integer():
        raise HTTPException(422, "Inserimento Smart Venue deve essere un numero intero")
    if quantity < 0:
        raise HTTPException(422, "Inserimento Smart Venue non può essere negativo")
    return quantity


async def _smart_venue_row_and_stock(row_id: str) -> tuple[Dict[str, Any], int]:
    smart_row = await db.smart_venue.find_one({"id": row_id}, {"_id": 0})
    if not smart_row:
        raise HTTPException(404, "Riga SMARTV VENUE non trovata")
    product = await db.prodotti.find_one({"codice": smart_row.get("codice")}, {"_id": 0}) or {}
    vending_totals = await _vending_stock_by_code() or {}
    rimanenze = int(product.get("giacenza_negozio", 0) or 0) + int(vending_totals.get(str(product.get("codice") or ""), 0) or 0)
    return smart_row, rimanenze


@api.post("/smart-venue/{row_id}/conferma")
async def confirm_smart_venue_difference(row_id: str, body: Optional[Dict[str, Any]] = None):
    smart_row, rimanenze = await _smart_venue_row_and_stock(row_id)
    current_smart_venue = int(smart_row.get("smart_venue", smart_row.get("rimanenze_smart", 0)) or 0)
    difference = current_smart_venue - rimanenze
    requested = (body or {}).get("inserimento_smart_venue")
    quantity = difference if requested in (None, "") else _parse_smart_venue_quantity(requested)
    if quantity < 0:
        raise HTTPException(422, "La differenza è negativa: inserisci una quantità manuale")
    if quantity > current_smart_venue:
        raise HTTPException(422, "Inserimento Smart Venue superiore alla quantità disponibile")

    updated_smart_venue = current_smart_venue - quantity
    await db.smart_venue.update_one(
        {"id": row_id},
        {"$set": {
            "smart_venue": updated_smart_venue,
            "ultimo_inserimento_smart_venue": quantity,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    return {
        "ok": True,
        "id": row_id,
        "rimanenze": rimanenze,
        "smart_venue": updated_smart_venue,
        "inserimento_smart_venue": quantity,
        "differenza": updated_smart_venue - rimanenze,
    }


@api.delete("/smart-venue/{row_id}")
async def delete_smart_venue_row(row_id: str):
    smart_row = await db.smart_venue.find_one({"id": row_id}, {"_id": 0, "id": 1, "codice": 1})
    if not smart_row:
        raise HTTPException(404, "Riga SMARTV VENUE non trovata")
    if not await db.smart_venue_hidden.find_one({"product_id": row_id}):
        await db.smart_venue_hidden.insert_one({
            "id": str(uuid.uuid4()),
            "product_id": row_id,
            "codice": smart_row.get("codice"),
            "hidden_at": datetime.now(timezone.utc).isoformat(),
        })
    return {"ok": True, "id": row_id}


async def _import_storico(ws) -> Dict[str, int]:
    ins = 0
    err = 0
    # elimina intero storico e ricrea (source of truth)
    await db.storico_ordini.delete_many({})
    batch = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        try:
            if not row[0]:
                continue
            data_v = row[0]
            if isinstance(data_v, datetime):
                data_v = data_v.isoformat()
            o = OrdineStorico(
                data=str(data_v),
                file_sorgente=str(row[1] or ""),
                codice=str(row[2] or "").strip(),
                descrizione=str(row[3] or "").strip(),
                quantita=int(row[4] or 0),
                prezzo=nonnegative_money(row[5] or 0),
            )
            batch.append(o.model_dump())
            if len(batch) >= 1000:
                await db.storico_ordini.insert_many(batch)
                ins += len(batch)
                batch = []
        except Exception:
            err += 1
    if batch:
        await db.storico_ordini.insert_many(batch)
        ins += len(batch)
    return {"inseriti": ins, "aggiornati": 0, "errori": err}


async def _import_parametri(ws) -> Dict[str, int]:
    # aggiorna solo parametri che ESISTONO già nel DB (preserva custom come AGGIO_PCT)
    operations = []
    skip = err = 0
    existing = {p["nome"] async for p in db.parametri.find({}, {"_id": 0, "nome": 1})}
    for row in ws.iter_rows(min_row=2, values_only=True):
        try:
            nome = row[0]
            if not nome:
                continue
            nome_s = str(nome).strip()
            valore = row[1]
            if valore is None:
                continue
            v = float(str(valore).replace(",", "."))
            if nome_s in existing:
                operations.append(UpdateOne({"nome": nome_s}, {"$set": {"valore": v}}))
            else:
                skip += 1
        except Exception:
            err += 1
    result = await _bulk_upsert(db.parametri, operations)
    result["saltati"] = skip
    result["errori"] += err
    return result


async def _import_db_storico_vend(ws) -> Dict[str, int]:
    """DB_STORICO_VEND: vendite storiche giornaliere. Full replace (fonte di verità)."""
    def header_key(value: Any) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        return re.sub(r"[^a-z0-9]", "", text.encode("ascii", "ignore").decode().lower())

    aliases = {
        "data": {"data", "giorno"},
        "codice": {"codice", "cod", "code"},
        "descrizione": {"descrizione", "descr", "prodotto"},
        "quantita": {"qta", "quantita", "pezzi"},
        "importo": {"importo", "totale", "valore"},
        "categoria": {"categoria", "cat"},
    }
    columns: Dict[str, int] = {}
    header_row = 0
    for row_number, row in enumerate(
        ws.iter_rows(min_row=1, max_row=min(ws.max_row, 10), values_only=True),
        start=1,
    ):
        candidate: Dict[str, int] = {}
        for index, value in enumerate(row):
            key = header_key(value)
            for field, names in aliases.items():
                if key in names and field not in candidate:
                    candidate[field] = index
        if {"data", "codice", "quantita"}.issubset(candidate):
            columns = candidate
            header_row = row_number
            break

    if not columns:
        raise HTTPException(
            422,
            "DB_STORICO_VEND: intestazioni Data, Codice e Quantità non riconosciute; storico precedente conservato",
        )

    def value_at(row: tuple, field: str, default: Any = None) -> Any:
        index = columns.get(field)
        return row[index] if index is not None and index < len(row) else default

    def iso_date(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        text = str(value or "").strip()
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y"):
            try:
                return datetime.strptime(text, fmt).isoformat()
            except ValueError:
                pass
        return text

    def product_code(value: Any) -> str:
        # Alcuni codici numerici nel file Excel ereditano erroneamente un formato
        # data (es. 10 diventa 10/01/1900): recuperiamo il seriale originale.
        if isinstance(value, datetime) and value.year < 1950:
            from openpyxl.utils.datetime import to_excel
            return str(int(to_excel(value)))
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value or "").strip()

    err = 0
    documents = []
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        try:
            data_v = value_at(row, "data")
            codice = product_code(value_at(row, "codice"))
            if not data_v or not codice:
                continue
            documents.append({
                "id": str(uuid.uuid4()),
                "data": iso_date(data_v),
                "codice": codice,
                "descrizione": str(value_at(row, "descrizione", "") or "").strip(),
                "quantita": int(value_at(row, "quantita", 0) or 0),
                "importo": money(value_at(row, "importo", 0) or 0),
                "categoria": str(value_at(row, "categoria", "") or "").strip(),
            })
        except Exception:
            err += 1

    # Il full replace avviene soltanto dopo aver riconosciuto e letto il foglio:
    # un cambio di layout non può più cancellare uno storico valido.
    await db.db_storico_vend.delete_many({})
    for start in range(0, len(documents), 1000):
        await db.db_storico_vend.insert_many(documents[start:start + 1000])
    return {"inseriti": len(documents), "aggiornati": 0, "errori": err}


async def _import_db_storico_vending_ext(ws) -> Dict[str, int]:
    """DB_STORICO_VENDING_EXT: storico dettagliato vendite vending. Full replace."""
    ins = err = 0
    await db.db_storico_vending_ext.delete_many({})
    batch = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        try:
            if not row or all(c in (None, "") for c in row):
                continue
            batch.append({
                "id": str(uuid.uuid4()),
                "raw": [str(c) if c is not None else "" for c in row[:18]],
            })
            if len(batch) >= 1000:
                await db.db_storico_vending_ext.insert_many(batch)
                ins += len(batch)
                batch = []
        except Exception:
            err += 1
    if batch:
        await db.db_storico_vending_ext.insert_many(batch)
        ins += len(batch)
    return {"inseriti": ins, "aggiornati": 0, "errori": err}


@api.get("/import/excel-full/status/{job_id}")
async def import_excel_full_status(job_id: str):
    progress = IMPORT_PROGRESS.get(job_id)
    if not progress:
        raise HTTPException(404, "Import non ancora avviato")
    return progress


@api.post("/import/excel-full")
async def import_excel_full(
    file: UploadFile = File(...),
    import_job_id: Optional[str] = Header(None, alias="X-Import-Job-ID"),
):
    """Import multi-sheet: prodotti, SMART VENUE, listino, vending e storici.
    Excel = fonte di verità (upsert). Righe DB non presenti nell'Excel sono conservate.
    Parametri custom (non presenti nel foglio PARAMETRI) sono preservati.
    """
    try:
        import openpyxl
    except Exception:
        raise HTTPException(500, "openpyxl non installato")
    _set_import_progress(import_job_id, "running", "Ricezione e controllo del file Excel", 1, 10)
    content = await _read_capped(file)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, keep_vba=False)
    except Exception as e:
        raise HTTPException(422, f"File non leggibile: {e}")

    expected_sheets = {
        "RIEP_VENDITA", "(SMART VENUE)", "LISTINO ADM", "RICARICA VENDING",
        "STORICO_ORDINI", "PARAMETRI", "DB_STORICO_VEND", "DB_STORICO_VENDING_EXT",
    }
    stock_sheet = None if "RIEP_VENDITA" in wb.sheetnames else _find_product_stock_sheet(wb)
    if not expected_sheets.intersection(wb.sheetnames) and stock_sheet is None:
        message = (
            "Nessun foglio importabile trovato. Usa il file gestionale completo oppure un foglio "
            "con le colonne CODICE, DESCRIZIONE, ACQUISTATI, GIACENZA NEGOZIO e GIACENZA VENDING."
        )
        _set_import_progress(import_job_id, "failed", message, 10, 10)
        raise HTTPException(422, message)

    _set_import_progress(import_job_id, "running", "Creazione del backup di sicurezza", 2, 10)
    backup = await create_backup_snapshot(
        f"Prima import {file.filename or 'Excel'}",
        "pre-import",
        EXCEL_IMPORT_BACKUP_COLLECTIONS,
    )

    fogli_trovati: List[str] = []
    fogli_mancanti: List[str] = []
    report: Dict[str, Any] = {}

    completed_steps = 0

    async def _run(name: str, importer):
        nonlocal completed_steps
        _set_import_progress(
            import_job_id,
            "running",
            f"Aggiornamento foglio {name}",
            2 + completed_steps,
            10,
            sheet=name,
        )
        if name in wb.sheetnames:
            fogli_trovati.append(name)
            report[name] = await importer(wb[name])
        else:
            fogli_mancanti.append(name)
        completed_steps += 1
        _set_import_progress(
            import_job_id,
            "running",
            f"Foglio {name} completato",
            2 + completed_steps,
            10,
            sheet=name,
            result=report.get(name),
        )

    adm_aliases = {}
    if "LISTINO ADM" in wb.sheetnames:
        adm_aliases = _adm_alias_index(wb["LISTINO ADM"].iter_rows(min_row=2, values_only=True))

    if "RIEP_VENDITA" in wb.sheetnames:
        await _run("RIEP_VENDITA", lambda ws: _import_prodotti(ws, adm_aliases))
        product_report_key = "RIEP_VENDITA"
    elif stock_sheet is not None:
        stock_ws, stock_header_row, stock_columns = stock_sheet
        product_report_key = stock_ws.title
        _set_import_progress(
            import_job_id,
            "running",
            f"Aggiornamento giacenze dal foglio {stock_ws.title}",
            2,
            10,
            sheet=stock_ws.title,
        )
        fogli_trovati.append(stock_ws.title)
        report[product_report_key] = await _import_product_stock_sheet(
            stock_ws,
            stock_header_row,
            stock_columns,
            adm_aliases,
        )
        completed_steps += 1
        _set_import_progress(
            import_job_id,
            "running",
            f"Giacenze dal foglio {stock_ws.title} completate",
            3,
            10,
            sheet=stock_ws.title,
            result=report[product_report_key],
        )
    else:
        await _run("RIEP_VENDITA", lambda ws: _import_prodotti(ws, adm_aliases))
        product_report_key = "RIEP_VENDITA"
    await _run("(SMART VENUE)", _import_smart_venue)
    await _run("LISTINO ADM", _import_listino)
    await _run("RICARICA VENDING", _import_vending)
    await _run("STORICO_ORDINI", _import_storico)
    await _run("PARAMETRI", _import_parametri)
    await _run("DB_STORICO_VEND", _import_db_storico_vend)
    await _run("DB_STORICO_VENDING_EXT", _import_db_storico_vending_ext)

    totali = {
        "prodotti_inseriti": report.get(product_report_key, {}).get("inseriti", 0),
        "prodotti_aggiornati": report.get(product_report_key, {}).get("aggiornati", 0),
        "listino_inseriti": report.get("LISTINO ADM", {}).get("inseriti", 0),
        "listino_aggiornati": report.get("LISTINO ADM", {}).get("aggiornati", 0),
        "vending_inseriti": report.get("RICARICA VENDING", {}).get("inseriti", 0),
        "vending_aggiornati": report.get("RICARICA VENDING", {}).get("aggiornati", 0),
        "smart_venue_righe": report.get("(SMART VENUE)", {}).get("inseriti", 0),
        "storico_ricreato": report.get("STORICO_ORDINI", {}).get("inseriti", 0),
        "parametri_aggiornati": report.get("PARAMETRI", {}).get("aggiornati", 0),
        "parametri_saltati": report.get("PARAMETRI", {}).get("saltati", 0),
        "db_storico_vend_righe": report.get("DB_STORICO_VEND", {}).get("inseriti", 0),
        "db_storico_vending_ext_righe": report.get("DB_STORICO_VENDING_EXT", {}).get("inseriti", 0),
        "ultimo_import_id": report.get("RIEP_VENDITA", {}).get("ultimo_import_id"),
        "valore_venduto_negozio_excel": report.get("RIEP_VENDITA", {}).get("valore_venduto_negozio_excel", 0),
        "valore_venduto_vending_excel": report.get("RIEP_VENDITA", {}).get("valore_venduto_vending_excel", 0),
        "valore_venduto_totale_excel": report.get("RIEP_VENDITA", {}).get("valore_venduto_totale_excel", 0),
    }
    response = {
        "ok": True,
        "file": file.filename,
        "backup_id": backup["id"],
        "tipo_import": "completo" if "RIEP_VENDITA" in fogli_trovati else "giacenze",
        "contabilita_inclusa": "RIEP_VENDITA" in fogli_trovati,
        "fogli_trovati": fogli_trovati,
        "fogli_mancanti": fogli_mancanti,
        "dettaglio": report,
        "totali": totali,
    }
    await record_import_history(file.filename or "Excel", response, backup["id"])
    _set_import_progress(
        import_job_id,
        "completed",
        "Aggiornamento completato",
        10,
        10,
        totals=totali,
    )
    return response


# ------------------------- Register -------------------------
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
)
sales_router = sales["router"]
list_vendite = sales["list_vendite"]
add_vendita = sales["add_vendita"]
del_vendita = sales["del_vendita"]
ultima_vendita_manuale = sales["ultima_vendita_manuale"]
annulla_vendita_manuale = sales["annulla_vendita_manuale"]
bulk_vendite = sales["bulk_vendite"]
vendita_cassa_rapida = sales["vendita_cassa_rapida"]
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
)
orders_router = orders["router"]
list_ordini = orders["list_ordini"]
add_ordine = orders["add_ordine"]
conferma_carico_smart_venue = orders["conferma_carico_smart_venue"]
del_ordine = orders["del_ordine"]
bulk_carico = orders["bulk_carico"]
_increment_smart_venue_for_receipt = orders["increment_smart_venue_for_receipt"]
repair_smart_venue_auto_order_2026_10_12 = orders["repair_smart_venue_auto_order_2026_10_12"]
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
adm_router, list_listino, adm_categories, adm_sync = create_adm_router(
    get_database=lambda: db,
    cap_limit=_cap,
    fetch_categories=lambda: fetch_adm_categories_sync(),
    parse_pdf=lambda category: parse_adm_pdf_sync(category),
    bulk_upsert=lambda collection, operations: _bulk_upsert(collection, operations),
    bulk_update=lambda collection, operations: _bulk_update(collection, operations),
    apply_electronic_inhalation_categories=lambda: _apply_electronic_inhalation_categories(),
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
_supplemental_store_cash_sales = dashboard_module["supplemental_store_cash_sales"]
_shop_sold_accounting_corrections = dashboard_module["shop_sold_accounting_corrections"]
_dashboard_sales_trend = dashboard_module["dashboard_sales_trend"]
dashboard = dashboard_module["dashboard"]
reconciliation_router, accounting_reconciliation, update_reconciliation_case = create_reconciliation_router(
    get_database=lambda: db,
    payment_bucket=_vending_payment_bucket,
    request_operator=_request_operator,
    record_audit_event=_record_audit_event,
)
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
api.include_router(parameters_router)
api.include_router(auto_order_router)
api.include_router(dashboard_router)
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

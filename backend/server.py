from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, Request, Header
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateMany, UpdateOne
from pymongo.errors import BulkWriteError, DuplicateKeyError, OperationFailure
import os, json, logging, uuid, io, re, asyncio, math, unicodedata, hashlib, gc, tempfile
from contextlib import asynccontextmanager
from pathlib import Path
import base64
import secrets
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal

if __package__:
    from .money import Money, NonNegativeMoney, money, money_decimal, money_sum, money_product, nonnegative_money
    from .reconciliation import reconcile
else:
    from money import Money, NonNegativeMoney, money, money_decimal, money_sum, money_product, nonnegative_money
    from reconciliation import reconcile

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
class Prodotto(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codice: str
    descrizione: str
    categoria: str = "ACCESSORI"
    prezzo: NonNegativeMoney = 0
    acquistati: int = 0
    venduti_negozio: int = 0
    venduti_vending: int = 0
    giacenza_negozio: int = 0
    giacenza_vending: int = 0
    colonna_vending: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ProdottoIn(BaseModel):
    codice: str
    descrizione: str
    categoria: str = "ACCESSORI"
    prezzo: NonNegativeMoney = 0
    acquistati: int = 0
    venduti_negozio: int = 0
    venduti_vending: int = 0
    giacenza_negozio: int = 0
    giacenza_vending: int = 0
    colonna_vending: Optional[str] = None


class VendutiNegozioIn(BaseModel):
    venduti_negozio: int = Field(ge=0)
    valore_atteso: int = Field(ge=0)


PRODUCT_CATEGORIES = {
    "SIGARETTE",
    "SIGARI",
    "SIGARETTI",
    "FIUTO E MASTICO",
    "TRINCIATI PER SIGARETTA",
    "ALTRI TABACCHI DA FUMO",
    "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE",
    "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE ELETTRONICA",
    "ACCESSORI",
}


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


class ListinoItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codice: str
    descrizione: str
    prezzo: NonNegativeMoney = 0
    confezione: str = ""


class VendingColonna(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    colonna: str
    codice: str = ""
    descrizione: str = ""
    giacenza: int = 0
    capacita_max: int = 5
    soglia_minima: int = 2


class VendingCreateIn(BaseModel):
    colonna: str
    codice: str
    capacita_max: int = 5
    soglia_minima: int = 2
    giacenza_iniziale: int = 0


class VenditaGiornaliera(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: str
    codice: str
    descrizione: str = ""
    quantita: int = 1
    importo: NonNegativeMoney = 0
    canale: str = "NEGOZIO"  # NEGOZIO / VENDING
    pagamento: str = "CONTANTI"
    colonna: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sorgente: str = "MANUALE"
    batch_id: Optional[str] = None
    undo_meta: Dict[str, Any] = Field(default_factory=dict)


class VenditaIn(BaseModel):
    data: str
    codice: str
    descrizione: Optional[str] = ""
    quantita: int = Field(default=1, gt=0)
    importo: NonNegativeMoney = Field(default=0, ge=0)
    canale: str = "NEGOZIO"
    pagamento: str = "CONTANTI"
    colonna: Optional[str] = None


class SmartVenueBulkIn(BaseModel):
    testo: str


class OrdineStorico(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: str
    file_sorgente: str = ""
    codice: str
    descrizione: str = ""
    quantita: int
    prezzo: NonNegativeMoney = 0
    batch_id: Optional[str] = None
    smart_venue_applicabile: bool = False
    caricato_smart_venue: Optional[bool] = None
    caricato_smart_venue_il: Optional[str] = None
    caricato_smart_venue_da: Optional[str] = None
    operatore: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class OrdineIn(BaseModel):
    data: str
    codice: str
    descrizione: Optional[str] = ""
    quantita: int
    prezzo: NonNegativeMoney = 0
    file_sorgente: Optional[str] = "manuale"


class GiacenzaRealeIn(BaseModel):
    giacenza_negozio: int
    giacenza_vending: int
    attesa_giacenza_negozio: int
    attesa_giacenza_vending: int
    motivazione: str = ""


class AutoOrderRigaIn(BaseModel):
    codice: str
    quantita: int = Field(gt=0, le=100000)


class AutoOrderConfermaIn(BaseModel):
    idempotency_key: Optional[str] = None
    batch_key: Optional[str] = None
    righe: Optional[List[AutoOrderRigaIn]] = None


class MovimentoCassa(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: str
    tipo: str  # ENTRATA / USCITA / SALDO_INIZIALE
    importo: Money
    descrizione: str = ""
    operatore: str = ""


class MovimentoIn(BaseModel):
    data: str
    tipo: str
    importo: Money
    descrizione: Optional[str] = ""
    operatore: Optional[str] = ""
    idempotency_key: Optional[str] = None


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
    descrizione: Optional[str] = ""
    operatore: Optional[str] = ""
    idempotency_key: Optional[str] = None


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
    descrizione: Optional[str] = ""
    operatore: Optional[str] = ""
    idempotency_key: Optional[str] = None


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
    descrizione: Optional[str] = ""
    operatore: Optional[str] = ""
    idempotency_key: Optional[str] = None


class Parametro(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nome: str
    valore: float
    descrizione: str = ""


class ParametroIn(BaseModel):
    valore: float


BACKUP_COLLECTIONS = [
    "prodotti",
    "listino_adm",
    "vending",
    "storico_ordini",
    "parametri",
    "db_storico_vend",
    "db_storico_vending_ext",
    "vendite",
    "vendite_bulk_imports",
    "versamenti",
    "prelievi_vending",
    "scontrini_vending",
    "cassa_vending_stato",
    "vending_accounting_snapshots",
    "vending_accounting_state",
    "vending_accounting_events",
    "cassa",
    "ordini_fornitore",
    "ordini_fornitore_righe",
    "import_history",
    "adm_sync",
    "anomalie_ignorate",
    "smart_venue",
    "smart_venue_hidden",
    "smart_venue_values",
    "ricezioni_merce",
    "rettifiche_inventario",
    "rettifiche_venduti",
    "audit_events",
]

BACKUP_FILE_FORMAT = "gestionale-tabacchi-backup"
BACKUP_FILE_VERSION = 1
OPTIONAL_BACKUP_COLLECTIONS = {
    "vendite_bulk_imports",
    "scontrini_vending",
    "cassa_vending_stato",
    "vending_accounting_snapshots",
    "vending_accounting_state",
    "vending_accounting_events",
    "anomalie_ignorate",
    "smart_venue",
    "smart_venue_hidden",
    "smart_venue_values",
    "ricezioni_merce",
    "rettifiche_inventario",
    "rettifiche_venduti",
    "audit_events",
}

BACKUP_RETENTION_LIMIT = 3
BACKUP_RETENTION_POLICY_ID = "backup-retention-v1"
EXCEL_IMPORT_BACKUP_COLLECTIONS = [
    "prodotti",
    "listino_adm",
    "vending",
    "storico_ordini",
    "parametri",
    "db_storico_vend",
    "db_storico_vending_ext",
    "smart_venue",
    "smart_venue_hidden",
    "smart_venue_values",
]


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


async def _record_audit_event(
    azione: str,
    entita: str,
    entita_id: Optional[str] = None,
    *,
    operatore: str = "operatore-locale",
    prima: Optional[Dict[str, Any]] = None,
    dopo: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    batch_id: Optional[str] = None,
) -> None:
    """Scrive un evento append-only senza includere identificativi Mongo interni."""
    audit_collection = getattr(db, "audit_events", None)
    # Fixture legacy/read-only che espongono solo le raccolte necessarie alla
    # singola operazione non devono fallire per l'audit opzionale.
    if audit_collection is None:
        return
    await audit_collection.insert_one({
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "azione": azione,
        "entita": entita,
        "entita_id": str(entita_id) if entita_id is not None else None,
        "operatore": operatore,
        "prima": dict(prima) if prima else None,
        "dopo": dict(dopo) if dopo else None,
        "idempotency_key": idempotency_key,
        "batch_id": batch_id,
    })


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


async def get_params() -> Dict[str, float]:
    docs = await db.parametri.find({}, {"_id": 0}).to_list(1000)
    return {d["nome"]: float(d["valore"]) for d in docs}


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
    payload = {
        "data_riferimento_domanda": ao.get("data_riferimento_domanda"),
        "parametri": {
            k: ao.get("parametri", {}).get(k)
            for k in (
                "GIORNI_COPERTURA_MIN",
                "GIORNI_COPERTURA_TARGET",
                "AUTO_ORDER_FINESTRA_BREVE_GG",
                "AUTO_ORDER_FINESTRA_LUNGA_GG",
                "AUTO_ORDER_PESO_BREVE",
                "AUTO_ORDER_MIN_VENDUTO_BREVE",
                "AUTO_ORDER_MIN_VENDUTO_LUNGO",
                "AUTO_ORDER_FATTORE_SICUREZZA",
            )
        },
        "righe": [
            {
                "codice": r.get("codice"),
                "qta_da_ordinare": r.get("qta_da_ordinare"),
                "prezzo": r.get("prezzo"),
                "totale": r.get("totale"),
            }
            for r in ao.get("righe", [])
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "auto-order:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
ADM_PREZZI_URL = "https://www.adm.gov.it/portale/monopoli/tabacchi/prezzi/prezzi_pubblico"
ADM_CATEGORY_ALIASES = {
    "SIGARETTE": ["sigarette"],
    "SIGARI": ["sigari"],
    "SIGARETTI": ["sigaretti"],
    "FIUTO E MASTICO": ["fiuto", "mastico"],
    "TRINCIATI PER SIGARETTA": ["trinciati", "ryo"],
    "ALTRI TABACCHI DA FUMO": ["altri tabacchi"],
    "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE": ["inalazione senza combustione", "prodotti da inalazione"],
}


def _q_regex(q: str) -> Dict[str, Any]:
    """Safe regex from user input (escaped, bounded)."""
    return {"$regex": re.escape(q[:200]), "$options": "i"}


def _cap(limit: int) -> int:
    return max(1, min(int(limit or 0), MAX_LIMIT))


def adm_numeric_code(codice: Any) -> str:
    text = str(codice or "").strip().upper()
    if text.startswith("AMMS"):
        text = text[4:]
    digits = re.sub(r"\D", "", text)
    return digits.lstrip("0") or digits


def adm_local_code(codice_adm: Any) -> str:
    code = adm_numeric_code(codice_adm)
    return f"AMMS{code}" if code else ""


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


def parse_italian_money(value: Any) -> float:
    text = str(value or "").strip().replace(".", "").replace(",", ".")
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        return float(text) if text else 0.0
    except ValueError:
        return 0.0


@api.get("/prodotti")
async def list_prodotti(q: Optional[str] = None, categoria: Optional[str] = None, limit: int = 500):
    filt: Dict[str, Any] = {}
    if categoria:
        filt["categoria"] = categoria
    if q:
        rx = _q_regex(q)
        filt["$or"] = [{"codice": rx}, {"descrizione": rx}]
    capped_limit = _cap(limit)
    docs, vending_totals = await asyncio.gather(
        db.prodotti.find(filt, {"_id": 0}).sort("codice", 1).limit(capped_limit).to_list(capped_limit),
        _vending_stock_by_code(),
    )
    return _apply_vending_stock(docs, vending_totals)


async def _canonical_manual_product_payload(p: ProdottoIn) -> Dict[str, Any]:
    payload = p.model_dump()
    raw_code = _product_code_text(payload["codice"])
    numeric = adm_numeric_code(raw_code)
    listino = await db.listino_adm.find({"$or": [
        {"codice": adm_local_code(numeric)},
        {"adm_codice": numeric},
    ]}, {"_id": 0}).to_list(20) if numeric else []
    payload["codice"] = _canonical_product_code(raw_code, payload["descrizione"], _adm_alias_index(listino))
    return payload


@api.post("/prodotti")
async def create_prodotto(p: ProdottoIn):
    payload = await _canonical_manual_product_payload(p)
    # La quantità vending si modifica esclusivamente nelle colonne Vending.
    payload["giacenza_vending"] = 0
    if await db.prodotti.find_one({"codice": payload["codice"]}):
        raise HTTPException(409, f"Codice prodotto già presente: {payload['codice']}")
    prod = Prodotto(**payload)
    await db.prodotti.insert_one(prod.model_dump())
    return prod.model_dump()


@api.put("/prodotti/{prod_id}")
async def update_prodotto(prod_id: str, p: ProdottoIn):
    payload = await _canonical_manual_product_payload(p)
    # Le giacenze vending e i contatori venduti hanno flussi dedicati: il form
    # anagrafico non deve poterli azzerare tramite i default di ProdottoIn.
    payload.pop("giacenza_vending", None)
    payload.pop("venduti_negozio", None)
    payload.pop("venduti_vending", None)
    collision = await db.prodotti.find_one({"codice": payload["codice"], "id": {"$ne": prod_id}})
    if collision:
        raise HTTPException(409, f"Codice prodotto già presente: {payload['codice']}")
    r = await db.prodotti.update_one({"id": prod_id}, {"$set": payload})
    if r.matched_count == 0:
        raise HTTPException(404, "not found")
    doc = await db.prodotti.find_one({"id": prod_id}, {"_id": 0})
    vending_totals = await _vending_stock_by_code() or {}
    doc["giacenza_vending"] = vending_totals.get(str(doc.get("codice") or ""), 0)
    return doc


@api.patch("/prodotti/{prod_id}/venduti-negozio")
async def update_venduti_negozio(prod_id: str, body: VendutiNegozioIn, request: Request):
    operator = _request_operator(request)

    async def persist_correction(session):
        kwargs = _session_kwargs(session)
        product = await db.prodotti.find_one({"id": prod_id}, {"_id": 0}, **kwargs)
        if not product:
            raise HTTPException(404, "Prodotto non trovato")

        current_value = int(product.get("venduti_negozio", 0) or 0)
        if current_value != body.valore_atteso:
            raise HTTPException(
                409,
                "Il valore dei venduti negozio è cambiato nel frattempo. Ricarica e riprova.",
            )

        if current_value != body.venduti_negozio:
            delta_quantita = body.venduti_negozio - current_value
            prezzo_unitario = money(product.get("prezzo", 0) or 0)
            importo_contabile = money_product(prezzo_unitario, delta_quantita)
            # Increasing shop sales consumes physical shop stock; decreasing them restores it.
            giacenza_precedente = int(product.get("giacenza_negozio", 0) or 0)
            nuova_giacenza = giacenza_precedente - delta_quantita
            result = await db.prodotti.update_one(
                {"id": prod_id, "venduti_negozio": current_value},
                {"$set": {
                    "venduti_negozio": body.venduti_negozio,
                    "giacenza_negozio": nuova_giacenza,
                }},
                **kwargs,
            )
            if result.matched_count != 1:
                raise HTTPException(
                    409,
                    "Il valore dei venduti negozio è cambiato nel frattempo. Ricarica e riprova.",
                )
            try:
                await db.rettifiche_venduti.insert_one({
                    "id": str(uuid.uuid4()),
                    "prodotto_id": prod_id,
                    "codice": product.get("codice", ""),
                    "campo": "venduti_negozio",
                    "valore_precedente": current_value,
                    "valore_nuovo": body.venduti_negozio,
                    "delta_quantita": delta_quantita,
                    "prezzo_unitario": prezzo_unitario,
                    "importo_contabile": importo_contabile,
                    "delta_giacenza_negozio": -delta_quantita,
                    "giacenza_precedente": giacenza_precedente,
                    "giacenza_nuova": nuova_giacenza,
                    "operatore": operator,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }, **kwargs)
            except Exception:
                if session is None:
                    await db.prodotti.update_one(
                        {"id": prod_id, "venduti_negozio": body.venduti_negozio},
                        {"$set": {
                            "venduti_negozio": current_value,
                            "giacenza_negozio": giacenza_precedente,
                        }},
                    )
                raise

        product["venduti_negozio"] = body.venduti_negozio
        product["giacenza_negozio"] = int(product.get("giacenza_negozio", 0) or 0) - (
            body.venduti_negozio - current_value
        )
        product["venduti_totale"] = body.venduti_negozio + int(product.get("venduti_vending", 0) or 0)
        return product

    return await _run_transactionally(persist_correction)


@api.delete("/prodotti/{prod_id}")
async def delete_prodotto(prod_id: str):
    await db.prodotti.delete_one({"id": prod_id})
    return {"ok": True}


# ------------------------- Listino ADM -------------------------
@api.get("/listino")
async def list_listino(q: Optional[str] = None, limit: int = 300):
    filt: Dict[str, Any] = {}
    if q:
        rx = _q_regex(q)
        filt["$or"] = [{"codice": rx}, {"descrizione": rx}]
    capped_limit = _cap(limit)
    docs = await db.listino_adm.find(filt, {"_id": 0}).sort("descrizione", 1).limit(capped_limit).to_list(capped_limit)
    total = await db.listino_adm.count_documents(filt)
    return {"items": docs, "total": total}


def fetch_adm_categories_sync() -> List[Dict[str, Any]]:
    import requests
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    response = requests.get(ADM_PREZZI_URL, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    categories: Dict[str, Dict[str, Any]] = {}
    page_text = " ".join(soup.get_text(" ", strip=True).split())
    for category, aliases in ADM_CATEGORY_ALIASES.items():
        for a in soup.find_all("a"):
            text = " ".join(a.get_text(" ", strip=True).split())
            href = a.get("href") or ""
            haystack = f"{text} {href}".lower()
            if not href or ".pdf" not in href.lower():
                continue
            if any(alias.lower() in haystack for alias in aliases):
                updated_match = re.search(rf"{re.escape(text)}.*?aggiornato il\s+(\d{{2}}/\d{{2}}/\d{{4}})", page_text, re.I)
                categories[category] = {
                    "categoria": category,
                    "titolo": text or category.title(),
                    "url": urljoin(ADM_PREZZI_URL, href),
                    "aggiornato_il": updated_match.group(1) if updated_match else None,
                }
                break
    return [categories[k] for k in ADM_CATEGORY_ALIASES if k in categories]


def parse_adm_pdf_sync(category: Dict[str, Any]) -> List[Dict[str, Any]]:
    import requests
    import pdfplumber

    rows: List[Dict[str, Any]] = []
    # I listini ADM possono essere grandi. Scaricarli su un file temporaneo evita
    # di tenere contemporaneamente in RAM sia i byte del PDF sia le strutture di
    # pdfplumber; il filesystem effimero di Render e sufficiente per questo uso.
    with requests.get(category["url"], timeout=90, stream=True) as response:
        response.raise_for_status()
        with tempfile.TemporaryFile(suffix=".pdf") as pdf_file:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if chunk:
                    pdf_file.write(chunk)
            pdf_file.seek(0)

            with pdfplumber.open(pdf_file) as pdf:
                for page in pdf.pages:
                    try:
                        tables = page.extract_tables() or []
                        for table in tables:
                            if not table:
                                continue
                            for row in table[1:]:
                                if not row or len(row) < 4:
                                    continue
                                code = adm_numeric_code(row[0])
                                if not code or not code.isdigit():
                                    continue
                                descrizione = " ".join(str(row[1] or "").replace("\n", " ").split())
                                confezione = " ".join(str(row[2] or "").replace("\n", " ").split()) if len(row) > 2 else ""
                                prezzo = parse_italian_money(row[-1])
                                if not descrizione:
                                    continue
                                rows.append({
                                    "adm_codice": code,
                                    "codice": adm_local_code(code),
                                    "descrizione": descrizione,
                                    "confezione": confezione,
                                    "prezzo": prezzo,
                                    "categoria_adm": category["categoria"],
                                    "fonte": "ADM",
                                    "adm_pdf_url": category["url"],
                                    "adm_aggiornato_il": category.get("aggiornato_il"),
                                })
                    finally:
                        # pdfplumber conserva cache grafiche pesanti per pagina.
                        page.close()
    return rows


def _adm_product_update_operations(rows: List[Dict[str, Any]], product_codes: set[str]):
    """Prepara update ADM solo per codici realmente presenti nei prodotti."""
    operations = []
    for item in rows:
        candidate_codes = {item["codice"].upper(), item["adm_codice"].upper()}
        if candidate_codes.isdisjoint(product_codes):
            continue
        operations.append(UpdateMany(
            {"$or": [{"codice": item["codice"]}, {"codice": item["adm_codice"]}]},
            {"$set": {
                "categoria": item["categoria_adm"],
                "categoria_adm": item["categoria_adm"],
                "adm_codice": item["adm_codice"],
                "adm_descrizione": item["descrizione"],
                "adm_prezzo": item["prezzo"],
                "adm_aggiornato_il": item.get("adm_aggiornato_il"),
            }},
        ))
    return operations


@api.get("/adm/categories")
async def adm_categories():
    categories = await asyncio.to_thread(fetch_adm_categories_sync)
    last_sync = await db.adm_sync.find_one({}, {"_id": 0}, sort=[("created_at", -1)])
    return {"source": ADM_PREZZI_URL, "categories": categories, "last_sync": last_sync}


@api.post("/adm/sync")
async def adm_sync():
    started = datetime.now(timezone.utc).isoformat()
    categories = await asyncio.to_thread(fetch_adm_categories_sync)
    if not categories:
        raise HTTPException(502, "Nessun PDF ADM trovato nella pagina ufficiale")

    total_rows = inserted = updated = product_updates = 0
    detail = []
    product_codes = {
        str(product.get("codice") or "").strip().upper()
        async for product in db.prodotti.find({}, {"_id": 0, "codice": 1})
        if product.get("codice")
    }

    # Una categoria alla volta: sul piano Render Free l'estrazione parallela di
    # sette PDF supera i 512 MB e provoca il riavvio del processo.
    for category in categories:
        rows = await asyncio.to_thread(parse_adm_pdf_sync, category)
        total_rows += len(rows)
        operations = []
        for item in rows:
            insert_defaults = ListinoItem(
                codice=item["codice"],
                descrizione=item["descrizione"],
                prezzo=item["prezzo"],
                confezione=item["confezione"],
            ).model_dump()
            set_data = {**item, "updated_at": started}
            operations.append(UpdateOne(
                {"$or": [
                    {"adm_codice": item["adm_codice"]},
                    {"codice": item["codice"]},
                    {"codice": item["adm_codice"]},
                ]},
                {"$set": set_data, "$setOnInsert": insert_defaults},
                upsert=True,
            ))
        result = await _bulk_upsert(db.listino_adm, operations)
        inserted += result.get("inseriti", 0)
        updated += result.get("aggiornati", 0)
        product_operations = _adm_product_update_operations(rows, product_codes)
        product_result = await _bulk_update(db.prodotti, product_operations)
        product_updates += product_result["modificati"]
        detail.append({
            **category,
            "righe": len(rows),
            **result,
            "prodotti_trovati": product_result["trovati"],
            "prodotti_aggiornati": product_result["modificati"],
        })

        # Rilascia subito righe, operazioni e cache cicliche prima del PDF seguente.
        del rows, operations, product_operations
        gc.collect()

    await db.prodotti.update_many(
        {"$or": [{"categoria_adm": {"$exists": False}}, {"categoria_adm": ""}, {"categoria_adm": None}]},
        {"$set": {"categoria": "ACCESSORI"}},
    )
    await _apply_electronic_inhalation_categories()
    linked_products = await db.prodotti.count_documents({"categoria_adm": {"$exists": True, "$ne": ""}})
    sync_doc = {
        "id": str(uuid.uuid4()),
        "created_at": started,
        "source": ADM_PREZZI_URL,
        "categorie": len(categories),
        "righe": total_rows,
        "inseriti": inserted,
        "aggiornati": updated,
        "prodotti_aggiornati": product_updates,
        "prodotti_collegati": linked_products,
        "dettaglio": detail,
    }
    await db.adm_sync.insert_one(sync_doc.copy())
    return sync_doc


# ------------------------- Vendite giornaliere -------------------------
@api.get("/vendite")
async def list_vendite(giorno: Optional[str] = None, limit: int = 500):
    filt: Dict[str, Any] = {}
    if giorno:
        # data ISO stringa (2026-02-18...) — validiamo formato semplice
        safe = re.sub(r"[^0-9\-T:.]", "", giorno)[:32]
        filt["data"] = {"$regex": f"^{re.escape(safe)}"}
    capped_limit = _cap(limit)
    docs = await db.vendite.find(filt, {"_id": 0}).sort("data", -1).limit(capped_limit).to_list(capped_limit)
    return docs


@api.post("/vendite")
async def add_vendita(
    v: VenditaIn,
    idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """Registra una vendita una sola volta e scala una scorta realmente disponibile."""
    channel = str(v.canale or "").strip().upper()
    if channel not in {"NEGOZIO", "VENDING"}:
        raise HTTPException(422, "Canale vendita non valido")
    codice = _product_code_text(v.codice)
    if not codice:
        raise HTTPException(422, "Codice prodotto obbligatorio")
    key = str(idempotency_key or "").strip() or None
    if key and not 10 <= len(key) <= 160:
        raise HTTPException(422, "X-Idempotency-Key deve contenere da 10 a 160 caratteri")
    if key:
        existing = await db.vendite.find_one({"idempotency_key": key}, {"_id": 0})
        if existing:
            return existing

    product = await db.prodotti.find_one({"codice": codice}, {"_id": 0})
    if not product:
        raise HTTPException(404, f"Prodotto non trovato: {codice}")

    column_code = None
    if channel == "VENDING":
        column_code = _normalize_vending_column(v.colonna)
        if not column_code:
            raise HTTPException(422, "Per una vendita Vending devi indicare la colonna")

    payload = v.model_dump()
    payload.update({
        "codice": codice,
        "canale": channel,
        "pagamento": str(v.pagamento or "CONTANTI").strip().upper(),
        "descrizione": v.descrizione or product.get("descrizione", ""),
        "colonna": column_code,
    })
    sale_doc = VenditaGiornaliera(**payload).model_dump()
    if key:
        sale_doc["idempotency_key"] = key

    async def operation(session):
        kwargs = _session_kwargs(session)
        if key:
            existing = await db.vendite.find_one({"idempotency_key": key}, {"_id": 0}, **kwargs)
            if existing:
                return existing

        column_changed = False
        product_changed = False
        try:
            if channel == "VENDING":
                column = await db.vending.find_one(
                    {"colonna": column_code, "codice": codice},
                    {"_id": 0},
                    **kwargs,
                )
                if not column:
                    raise HTTPException(422, "La colonna Vending non contiene il prodotto indicato")
                result = await db.vending.update_one(
                    {"id": column["id"], "codice": codice, "giacenza": {"$gte": v.quantita}},
                    {"$inc": {"giacenza": -v.quantita}},
                    **kwargs,
                )
                if result.modified_count != 1:
                    raise HTTPException(409, f"Giacenza insufficiente nella colonna {column_code}")
                column_changed = True
                result = await db.prodotti.update_one(
                    {"codice": codice},
                    {"$inc": {"venduti_vending": v.quantita}},
                    **kwargs,
                )
            else:
                result = await db.prodotti.update_one(
                    {"codice": codice, "giacenza_negozio": {"$gte": v.quantita}},
                    {"$inc": {"giacenza_negozio": -v.quantita, "venduti_negozio": v.quantita}},
                    **kwargs,
                )
            if result.modified_count != 1:
                raise HTTPException(409, f"Giacenza insufficiente per il prodotto {codice}")
            product_changed = True
            await db.vendite.insert_one(sale_doc.copy(), **kwargs)
            return sale_doc
        except Exception:
            # Mongo standalone e mongomock non hanno transazioni: compensiamo le
            # modifiche già applicate. Su Atlas il rollback è gestito dal server.
            if session is None:
                if product_changed:
                    increments = {"venduti_vending": -v.quantita} if channel == "VENDING" else {
                        "giacenza_negozio": v.quantita,
                        "venduti_negozio": -v.quantita,
                    }
                    await db.prodotti.update_one({"codice": codice}, {"$inc": increments})
                if column_changed:
                    await db.vending.update_one(
                        {"colonna": column_code, "codice": codice},
                        {"$inc": {"giacenza": v.quantita}},
                    )
            raise

    try:
        result = await _run_transactionally(operation)
    except DuplicateKeyError:
        if key:
            existing = await db.vendite.find_one({"idempotency_key": key}, {"_id": 0})
            if existing:
                result = existing
            else:
                raise
        else:
            raise
    if channel == "VENDING":
        await _sync_product_vending_stock(codice)
    return result


@api.delete("/vendite/{v_id}")
async def del_vendita(v_id: str):
    v = await db.vendite.find_one({"id": v_id})
    if not v:
        raise HTTPException(404, "Vendita non trovata")
    if v.get("sorgente", "MANUALE") != "MANUALE":
        raise HTTPException(409, "Questa vendita va annullata dal relativo caricamento")
    return await annulla_vendita_manuale(v_id)


@api.get("/vendite/manuale/ultima")
async def ultima_vendita_manuale():
    """Restituisce l'ultima vendita manuale ancora presente e annullabile."""
    return await db.vendite.find_one(
        {"sorgente": "MANUALE"},
        {"_id": 0},
        sort=[("created_at", -1)],
    )


@api.post("/vendite/manuale/{sale_id}/annulla")
async def annulla_vendita_manuale(sale_id: str):
    sale = await db.vendite.find_one({"id": sale_id, "sorgente": "MANUALE"}, {"_id": 0})
    if not sale:
        raise HTTPException(404, "Vendita manuale non trovata o già annullata")
    vend_field = "venduti_vending" if sale.get("canale") == "VENDING" else "venduti_negozio"
    quantity = int(sale.get("quantita") or 0)
    increments = {vend_field: -quantity}
    is_vending = sale.get("canale") == "VENDING"
    column_code = _normalize_vending_column(sale.get("colonna")) if is_vending else None
    if is_vending and not column_code:
        raise HTTPException(409, "Vendita Vending senza colonna: annullamento manuale richiesto")
    if not is_vending:
        increments["giacenza_negozio"] = quantity

    async def operation(session):
        kwargs = _session_kwargs(session)
        column_changed = False
        stock_changed = False
        try:
            if is_vending:
                column_update = await db.vending.update_one(
                    {"colonna": column_code, "codice": sale.get("codice")},
                    {"$inc": {"giacenza": quantity}},
                    **kwargs,
                )
                if column_update.modified_count != 1:
                    raise RuntimeError("La colonna Vending della vendita non esiste più")
                column_changed = True
            stock_update = await db.prodotti.update_one(
                {"codice": sale.get("codice")}, {"$inc": increments}, **kwargs
            )
            if stock_update.modified_count != 1:
                raise RuntimeError("Il prodotto della vendita non esiste più")
            stock_changed = True
            deletion = await db.vendite.delete_one(
                {"id": sale_id, "sorgente": "MANUALE"}, **kwargs
            )
            if deletion.deleted_count != 1:
                raise RuntimeError("La vendita è cambiata durante l'annullamento")
            if is_vending:
                await _sync_product_vending_stock(str(sale.get("codice") or ""), session)
        except Exception:
            if session is None:
                if stock_changed:
                    rollback = {vend_field: quantity}
                    if not is_vending:
                        rollback["giacenza_negozio"] = -quantity
                    await db.prodotti.update_one(
                        {"codice": sale.get("codice")}, {"$inc": rollback}
                    )
                if column_changed:
                    await db.vending.update_one(
                        {"colonna": column_code, "codice": sale.get("codice")},
                        {"$inc": {"giacenza": -quantity}},
                    )
                    await _sync_product_vending_stock(str(sale.get("codice") or ""))
            raise

    try:
        await _run_transactionally(operation)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "rimossi": 1, "id": sale_id}


class BulkVenditaIn(BaseModel):
    canale: str = "NEGOZIO"
    pagamento: str = "CONTANTI"
    righe: List[Dict[str, Any]]  # {data, codice, descrizione, quantita, importo}


class CassaRapidaIn(BaseModel):
    """Vendita POS atomica: vendite, scorte e movimento cassa nello stesso batch."""
    data: str
    pagamento: str = "CONTANTI"
    righe: List[Dict[str, Any]]
    idempotency_key: Optional[str] = None


@api.post("/vendite/bulk")
async def bulk_vendite(body: BulkVenditaIn):
    """Bulk paste da Excel: rows with data, codice, descrizione, quantita, importo."""
    if str(body.canale or "").upper() == "VENDING":
        raise HTTPException(422, "Per le vendite Vending usa il CSV distributore, che identifica la colonna reale")
    batch_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    skipped = 0
    errors = []
    prepared = []
    required_by_code: Dict[str, int] = {}
    for i, r in enumerate(body.righe):
        try:
            codice = _product_code_text(r.get("codice"))
            if not codice:
                skipped += 1
                continue
            qta = int(float(r.get("quantita") or 0))
            imp = nonnegative_money(r.get("importo", 0))
            if qta <= 0:
                raise ValueError("quantità non valida")
            if imp < 0:
                raise ValueError("importo non valido")
            data = str(r.get("data") or datetime.now(timezone.utc).isoformat())
            desc = str(r.get("descrizione") or "")
            prod = await db.prodotti.find_one({"codice": codice}, {"_id": 0})
            if not prod:
                raise ValueError(f"prodotto non trovato: {codice}")
            required_by_code[codice] = required_by_code.get(codice, 0) + qta
            prepared.append(VenditaGiornaliera(
                data=data,
                codice=codice,
                descrizione=desc or prod.get("descrizione", ""),
                quantita=qta,
                importo=imp,
                canale="NEGOZIO",
                pagamento=str(body.pagamento or "CONTANTI").strip().upper(),
                sorgente="BULK",
                batch_id=batch_id,
            ).model_dump())
        except Exception as e:
            errors.append({"riga": i + 1, "errore": str(e)})
    for codice, required in required_by_code.items():
        product = await db.prodotti.find_one({"codice": codice}, {"_id": 0, "giacenza_negozio": 1})
        available = int((product or {}).get("giacenza_negozio") or 0)
        if required > available:
            errors.append({"codice": codice, "errore": f"giacenza {available}, richiesti {required}"})
    if errors:
        raise HTTPException(422, detail={"message": "Caricamento rifiutato: nessuna vendita registrata", "errori": errors})

    async def operation(session):
        kwargs = _session_kwargs(session)
        changed: List[Dict[str, Any]] = []
        inserted_ids: List[str] = []
        try:
            for sale in prepared:
                quantity = int(sale["quantita"])
                update = await db.prodotti.update_one(
                    {"codice": sale["codice"], "giacenza_negozio": {"$gte": quantity}},
                    {"$inc": {"giacenza_negozio": -quantity, "venduti_negozio": quantity}},
                    **kwargs,
                )
                if update.modified_count != 1:
                    raise RuntimeError(f"Giacenza cambiata per {sale['codice']}")
                changed.append(sale)
                await db.vendite.insert_one(sale.copy(), **kwargs)
                inserted_ids.append(sale["id"])
            if prepared:
                await db.vendite_bulk_imports.insert_one({
                    "id": batch_id,
                    "created_at": created_at,
                    "sorgente": "BULK",
                    "canale": "NEGOZIO",
                    "pagamento": str(body.pagamento or "CONTANTI").strip().upper(),
                    "inseriti": len(prepared),
                    "saltati": skipped,
                    "status": "active",
                }, **kwargs)
        except Exception:
            if session is None:
                if inserted_ids:
                    await db.vendite.delete_many({"id": {"$in": inserted_ids}, "batch_id": batch_id})
                for sale in changed:
                    quantity = int(sale["quantita"])
                    await db.prodotti.update_one(
                        {"codice": sale["codice"]},
                        {"$inc": {"giacenza_negozio": quantity, "venduti_negozio": -quantity}},
                    )
                await db.vendite_bulk_imports.delete_one({"id": batch_id})
            raise

    await _run_transactionally(operation)
    inserted = len(prepared)
    return {
        "inseriti": inserted,
        "saltati": skipped,
        "errori": errors,
        "batch_id": batch_id if inserted else None,
        "created_at": created_at if inserted else None,
        "canale": body.canale,
        "pagamento": body.pagamento,
    }


@api.post("/vendite/cassa-rapida")
async def vendita_cassa_rapida(body: CassaRapidaIn, request: Request = None):
    """Registra un carrello POS senza lasciare vendite o cassa a metà."""
    key = str(body.idempotency_key or "").strip() or None
    if key and not 10 <= len(key) <= 160:
        raise HTTPException(422, "idempotency_key deve contenere da 10 a 160 caratteri")
    pagamento = str(body.pagamento or "CONTANTI").strip().upper()
    if not body.righe:
        raise HTTPException(422, "Il carrello è vuoto")
    if key:
        existing = await db.cassa_rapida_batches.find_one({"idempotency_key": key}, {"_id": 0})
        if existing:
            return existing

    batch_id = str(uuid.uuid4())
    prepared = []
    total = Decimal(0)
    for index, row in enumerate(body.righe):
        try:
            codice = _product_code_text(row.get("codice"))
            quantity = int(row.get("quantita") or 0)
            if not codice or quantity <= 0:
                raise ValueError("prodotto o quantità non validi")
            product = await db.prodotti.find_one({"codice": codice}, {"_id": 0})
            if not product:
                raise ValueError(f"prodotto non trovato: {codice}")
            unit_price = money_decimal(row.get("prezzo", product.get("prezzo", 0)), nonnegative=True)
            line_total = unit_price * quantity
            prepared.append({
                "id": str(uuid.uuid4()), "data": body.data, "codice": codice,
                "descrizione": str(row.get("descrizione") or product.get("descrizione") or ""),
                "quantita": quantity, "importo": money(line_total), "canale": "NEGOZIO",
                "pagamento": pagamento, "sorgente": "POS", "batch_id": batch_id,
                "operatore": _request_operator(request),
            })
            total += line_total
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise HTTPException(422, f"Riga {index + 1}: {exc}") from exc

    total_float = money(total)
    created_at = datetime.now(timezone.utc).isoformat()
    batch = {"id": batch_id, "created_at": created_at, "sorgente": "POS", "pagamento": pagamento,
             "totale": total_float, "pezzi": sum(int(row["quantita"]) for row in prepared),
             "idempotency_key": key, "status": "active"}
    cash_entry = MovimentoCassa(
        data=body.data, tipo="ENTRATA", importo=total_float,
        descrizione=f"Vendita banco ({batch['pezzi']} pz · {pagamento})",
        operatore=_request_operator(request),
    ).model_dump()

    async def operation(session):
        kwargs = _session_kwargs(session)
        if key:
            existing = await db.cassa_rapida_batches.find_one({"idempotency_key": key}, {"_id": 0}, **kwargs)
            if existing:
                return existing
        changed = []
        try:
            for sale in prepared:
                quantity = int(sale["quantita"])
                result = await db.prodotti.update_one(
                    {"codice": sale["codice"], "giacenza_negozio": {"$gte": quantity}},
                    {"$inc": {"giacenza_negozio": -quantity, "venduti_negozio": quantity}}, **kwargs,
                )
                if result.modified_count != 1:
                    raise RuntimeError(f"Giacenza insufficiente per il prodotto {sale['codice']}")
                changed.append(sale)
                await db.vendite.insert_one(sale.copy(), **kwargs)
            await db.cassa.insert_one(cash_entry.copy(), **kwargs)
            await db.cassa_rapida_batches.insert_one(batch.copy(), **kwargs)
            return batch
        except Exception:
            if session is None:
                await db.cassa.delete_one({"id": cash_entry["id"]})
                await db.vendite.delete_many({"batch_id": batch_id, "sorgente": "POS"})
                for sale in changed:
                    await db.prodotti.update_one({"codice": sale["codice"]}, {"$inc": {
                        "giacenza_negozio": int(sale["quantita"]), "venduti_negozio": -int(sale["quantita"])
                    }})
                await db.cassa_rapida_batches.delete_one({"id": batch_id})
            raise

    try:
        return await _run_transactionally(operation)
    except DuplicateKeyError:
        if key:
            existing = await db.cassa_rapida_batches.find_one({"idempotency_key": key}, {"_id": 0})
            if existing:
                return existing
        raise


@api.get("/vendite/bulk/ultimo")
async def ultimo_bulk_vendite():
    """Restituisce l'ultimo caricamento bulk ancora annullabile."""
    return await db.vendite_bulk_imports.find_one(
        {
            "status": "active",
            "$or": [{"sorgente": "BULK"}, {"sorgente": {"$exists": False}}],
        },
        {"_id": 0},
        sort=[("created_at", -1)],
    )


@api.post("/vendite/bulk/{batch_id}/annulla")
async def annulla_bulk_vendite(batch_id: str):
    """Annulla un singolo caricamento bulk e ripristina le scorte coinvolte."""
    claim = await db.vendite_bulk_imports.update_one(
        {"id": batch_id, "status": "active"},
        {"$set": {"status": "annulling"}},
    )
    if claim.modified_count != 1:
        raise HTTPException(404, "Caricamento non trovato o già annullato")

    async def operation(session):
        kwargs = _session_kwargs(session)
        sales = await db.vendite.find(
            {"batch_id": batch_id, "sorgente": "BULK"},
            {"_id": 0},
            **kwargs,
        ).to_list(None)
        completed = []
        current_stock_change = None
        try:
            for sale in sales:
                field = "giacenza_vending" if sale.get("canale") == "VENDING" else "giacenza_negozio"
                vend_field = "venduti_vending" if sale.get("canale") == "VENDING" else "venduti_negozio"
                quantity = int(sale.get("quantita") or 0)
                stock_update = await db.prodotti.update_one(
                    {"codice": sale.get("codice")},
                    {"$inc": {field: quantity, vend_field: -quantity}},
                    **kwargs,
                )
                if stock_update.modified_count != 1:
                    raise RuntimeError(f"Prodotto non trovato: {sale.get('codice')}")
                current_stock_change = (sale, field, vend_field, quantity)
                deletion = await db.vendite.delete_one(
                    {"id": sale.get("id"), "batch_id": batch_id}, **kwargs
                )
                if deletion.deleted_count != 1:
                    raise RuntimeError("Una vendita del caricamento non è stata eliminata")
                completed.append(current_stock_change)
                current_stock_change = None

            await db.vendite_bulk_imports.update_one(
                {"id": batch_id, "status": "annulling"},
                {"$set": {
                    "status": "annulled",
                    "annulled_at": datetime.now(timezone.utc).isoformat(),
                    "rimossi": len(completed),
                }},
                **kwargs,
            )
            return len(completed)
        except Exception:
            if session is None:
                if current_stock_change:
                    sale, field, vend_field, quantity = current_stock_change
                    await db.prodotti.update_one(
                        {"codice": sale.get("codice")},
                        {"$inc": {field: -quantity, vend_field: quantity}},
                    )
                for sale, field, vend_field, quantity in reversed(completed):
                    await db.vendite.insert_one(sale.copy())
                    await db.prodotti.update_one(
                        {"codice": sale.get("codice")},
                        {"$inc": {field: -quantity, vend_field: quantity}},
                    )
            raise

    try:
        removed = await _run_transactionally(operation)
    except Exception as exc:
        await db.vendite_bulk_imports.update_one(
            {"id": batch_id, "status": "annulling"},
            {"$set": {"status": "active"}},
        )
        raise HTTPException(500, f"Impossibile annullare il caricamento: {exc}") from exc

    return {"ok": True, "batch_id": batch_id, "rimossi": removed}


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


async def create_backup_snapshot(
    label: str,
    reason: str = "manuale",
    collection_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    snapshot_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    counts: Dict[str, int] = {}
    names = BACKUP_COLLECTIONS if collection_names is None else [
        name for name in collection_names if name in BACKUP_COLLECTIONS
    ]
    checksums: Dict[str, str] = {}
    await db.backup_snapshot_items.delete_many({"snapshot_id": snapshot_id})
    for collection_name in names:
        collection = db[collection_name]
        cursor = collection.find({}, {"_id": 0})
        digest = hashlib.sha256()
        count = 0
        pending = []

        async def store_document(document):
            nonlocal count, pending
            digest.update(json.dumps(
                document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8"))
            digest.update(b"\n")
            pending.append({
                "snapshot_id": snapshot_id,
                "collection": collection_name,
                "position": count,
                "doc": document,
            })
            count += 1
            if len(pending) >= 1000:
                await db.backup_snapshot_items.insert_many(pending)
                pending = []

        try:
            async for doc in cursor:
                await store_document(doc)
        except TypeError:
            # Compatibilità con i semplici cursor finti usati dai test.
            for doc in await cursor.to_list(None):
                await store_document(doc)
        if pending:
            await db.backup_snapshot_items.insert_many(pending)
        counts[collection_name] = count
        checksums[collection_name] = digest.hexdigest()
    total_docs = sum(counts.values())
    meta = {
        "id": snapshot_id,
        "label": label[:180],
        "reason": reason[:80],
        "created_at": created_at,
        "counts": counts,
        "checksums": checksums,
        "total_docs": total_docs,
        "partial": collection_names is not None,
        "status": "READY",
    }
    # Motor muta il dizionario passato a insert_one aggiungendo ``_id``.
    # Manteniamo pulito l'oggetto restituito all'API: ObjectId non e' JSON
    # serializzabile e faceva apparire fallito un backup gia' creato.
    await db.backup_snapshots.insert_one(meta.copy())
    migration_collection = getattr(db, "app_migrations", None)
    retention_enabled = await migration_collection.find_one(
        {"id": BACKUP_RETENTION_POLICY_ID}, {"_id": 0, "id": 1}
    ) if migration_collection is not None else None
    if retention_enabled:
        try:
            await prune_backup_snapshots(BACKUP_RETENTION_LIMIT)
        except Exception:
            # Il backup appena creato resta valido anche se la pulizia non riesce.
            logging.exception("Impossibile applicare la retention dei backup MongoDB")
    return meta


async def backup_retention_preview(keep: int = BACKUP_RETENTION_LIMIT) -> Dict[str, Any]:
    """Mostra quali snapshot interni verrebbero svuotati dalla retention."""
    keep = max(1, min(int(keep or BACKUP_RETENTION_LIMIT), 20))
    snapshots = await db.backup_snapshots.find(
        {"status": {"$ne": "PRUNED"}},
        {"_id": 0},
    ).sort("created_at", -1).to_list(None)
    candidates = snapshots[keep:]
    return {
        "keep": keep,
        "snapshot_totali": len(snapshots),
        "snapshot_conservati": len(snapshots[:keep]),
        "snapshot_da_svuotare": len(candidates),
        "documenti_da_eliminare_stimati": sum(int(row.get("total_docs") or 0) for row in candidates),
        "candidati": [
            {
                "id": row.get("id"),
                "label": row.get("label"),
                "reason": row.get("reason"),
                "created_at": row.get("created_at"),
                "total_docs": int(row.get("total_docs") or 0),
            }
            for row in candidates
        ],
    }


async def prune_backup_snapshots(keep: int = BACKUP_RETENTION_LIMIT) -> Dict[str, Any]:
    """Libera i documenti duplicati, mantenendo i backup interni più recenti."""
    preview = await backup_retention_preview(keep)
    pruned_at = datetime.now(timezone.utc).isoformat()
    deleted_items = 0
    for candidate in preview["candidati"]:
        snapshot_id = candidate.get("id")
        if not snapshot_id:
            continue
        deletion = await db.backup_snapshot_items.delete_many({"snapshot_id": snapshot_id})
        deleted_items += int(getattr(deletion, "deleted_count", 0) or 0)
        await db.backup_snapshots.update_one(
            {"id": snapshot_id, "status": {"$ne": "PRUNED"}},
            {"$set": {
                "status": "PRUNED",
                "pruned_at": pruned_at,
                "pruned_docs": int(candidate.get("total_docs") or 0),
            }},
        )
    return {
        **preview,
        "snapshot_svuotati": len(preview["candidati"]),
        "documenti_eliminati": deleted_items,
        "pruned_at": pruned_at,
    }


async def _backup_documents(
    snapshot_id: str, collection_names: Optional[List[str]] = None
) -> Dict[str, List[Dict[str, Any]]]:
    collections: Dict[str, List[Dict[str, Any]]] = {}
    names = BACKUP_COLLECTIONS if collection_names is None else collection_names
    for collection_name in names:
        items = await db.backup_snapshot_items.find(
            {"snapshot_id": snapshot_id, "collection": collection_name},
            {"_id": 0, "doc": 1},
        ).sort("position", 1).to_list(None)
        collections[collection_name] = [item["doc"] for item in items]
    return collections


def _validate_backup_file(payload: Any) -> Dict[str, List[Dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise HTTPException(422, "Il file di backup non contiene un oggetto JSON valido")
    if payload.get("format") != BACKUP_FILE_FORMAT or payload.get("version") != BACKUP_FILE_VERSION:
        raise HTTPException(422, "Formato o versione del backup non riconosciuti")
    collections = payload.get("collections")
    if not isinstance(collections, dict):
        raise HTTPException(422, "Il backup non contiene le collezioni dati")
    missing = [
        name for name in BACKUP_COLLECTIONS
        if name not in collections and name not in OPTIONAL_BACKUP_COLLECTIONS
    ]
    unknown = [name for name in collections if name not in BACKUP_COLLECTIONS]
    if missing:
        raise HTTPException(422, f"Backup incompleto: mancano {', '.join(missing)}")
    if unknown:
        raise HTTPException(422, f"Backup non valido: collezioni sconosciute {', '.join(unknown)}")
    declared_counts = payload.get("counts") or {}
    declared_checksums = payload.get("checksums") or {}
    for collection_name, docs in collections.items():
        if not isinstance(docs, list):
            raise HTTPException(422, f"Collezione {collection_name} non valida")
        if any(not isinstance(doc, dict) or "_id" in doc for doc in docs):
            raise HTTPException(422, f"Documento non valido nella collezione {collection_name}")
        if collection_name in declared_counts and int(declared_counts[collection_name]) != len(docs):
            raise HTTPException(422, f"Conteggio non valido per la collezione {collection_name}")
        if collection_name in declared_checksums:
            digest = hashlib.sha256()
            for doc in docs:
                digest.update(json.dumps(
                    doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
                ).encode("utf-8"))
                digest.update(b"\n")
            if not secrets.compare_digest(str(declared_checksums[collection_name]), digest.hexdigest()):
                raise HTTPException(422, f"Checksum non valido per la collezione {collection_name}")
    return collections


async def _restore_collections(collections: Dict[str, List[Dict[str, Any]]], session=None):
    kwargs = _session_kwargs(session)
    for collection_name in BACKUP_COLLECTIONS:
        if collection_name not in collections:
            continue
        await db[collection_name].delete_many({}, **kwargs)
        docs = collections[collection_name]
        for start in range(0, len(docs), 1000):
            await db[collection_name].insert_many(docs[start:start + 1000], **kwargs)


@asynccontextmanager
async def _restore_lock():
    token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    await db.operation_locks.delete_many({
        "_id": "backup-restore",
        "expires_at": {"$lte": now},
    })
    try:
        await db.operation_locks.insert_one({
            "_id": "backup-restore",
            "token": token,
            "created_at": now,
            "expires_at": now + timedelta(hours=6),
        })
    except DuplicateKeyError as exc:
        raise HTTPException(409, "Un altro ripristino è già in corso") from exc
    try:
        yield
    finally:
        await db.operation_locks.delete_one({"_id": "backup-restore", "token": token})


@api.post("/backup/create")
async def backup_create(body: Optional[Dict[str, Any]] = None):
    label = str((body or {}).get("label") or f"Backup {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}")
    reason = str((body or {}).get("reason") or "manuale")
    return await create_backup_snapshot(label, reason)


@api.get("/backup/retention-preview")
async def backup_cleanup_preview(keep: int = BACKUP_RETENTION_LIMIT):
    return await backup_retention_preview(keep)


@api.post("/backup/prune")
async def backup_cleanup(keep: int = BACKUP_RETENTION_LIMIT):
    result = await prune_backup_snapshots(keep)
    await db.app_migrations.update_one(
        {"id": BACKUP_RETENTION_POLICY_ID},
        {"$set": {
            "id": BACKUP_RETENTION_POLICY_ID,
            "enabled_at": datetime.now(timezone.utc).isoformat(),
            "keep": result["keep"],
        }},
        upsert=True,
    )
    result["retention_enabled"] = True
    return result


@api.get("/backup")
async def backup_list(limit: int = 20):
    capped_limit = max(1, min(int(limit or 20), 100))
    docs = await db.backup_snapshots.find(
        {"status": {"$ne": "PRUNED"}}, {"_id": 0}
    ).sort("created_at", -1).limit(capped_limit).to_list(capped_limit)
    return docs


@api.get("/backup/{snapshot_id}/download")
async def backup_download(snapshot_id: str):
    meta = await db.backup_snapshots.find_one(
        {"id": snapshot_id, "status": {"$ne": "PRUNED"}}, {"_id": 0}
    )
    if not meta:
        raise HTTPException(404, "backup non trovato")
    backed_up_names = list((meta.get("counts") or {}).keys())
    missing = [
        name for name in BACKUP_COLLECTIONS
        if name not in backed_up_names and name not in OPTIONAL_BACKUP_COLLECTIONS
    ]
    if missing:
        if meta.get("partial"):
            raise HTTPException(
                409,
                "Questo è un backup automatico parziale: può essere ripristinato dall'app, ma non scaricato come backup completo.",
            )
        raise HTTPException(
            409,
            "Questo backup è precedente al formato completo. Creane uno nuovo per scaricare anche vendite, cassa e ordini.",
        )
    collections = await _backup_documents(snapshot_id)
    payload = {
        "format": BACKUP_FILE_FORMAT,
        "version": BACKUP_FILE_VERSION,
        "created_at": meta.get("created_at"),
        "label": meta.get("label"),
        "counts": {name: len(docs) for name, docs in collections.items()},
        "checksums": meta.get("checksums") or {},
        "collections": collections,
    }
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    timestamp = str(meta.get("created_at") or datetime.now(timezone.utc).isoformat())[:19].replace(":", "-")
    filename = f"gestionale-backup-{timestamp}.json"
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api.post("/backup/{snapshot_id}/restore")
async def backup_restore(snapshot_id: str):
    async with _restore_lock():
        meta = await db.backup_snapshots.find_one(
            {"id": snapshot_id, "status": {"$ne": "PRUNED"}}, {"_id": 0}
        )
        if not meta:
            raise HTTPException(404, "backup non trovato")
        restore_backup = await create_backup_snapshot(
            f"Prima del ripristino {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}",
            "pre-restore",
        )
        # I vecchi snapshot non contenevano tutte le collezioni operative: in quel
        # caso ripristiniamo soltanto ciò che era stato effettivamente salvato.
        backed_up_names = [name for name in (meta.get("counts") or {}) if name in BACKUP_COLLECTIONS]
        collections = await _backup_documents(snapshot_id, backed_up_names)
        await _run_transactionally(lambda session: _restore_collections(collections, session))
        if "vending" in backed_up_names:
            await db.vending.update_many({}, {"$set": {
                "giacenza_aggiornata_il": meta.get("created_at"),
                "giacenza_sorgente": "BACKUP_RIPRISTINATO",
            }})
        await db.backup_snapshots.update_one(
            {"id": snapshot_id},
            {"$set": {"last_restored_at": datetime.now(timezone.utc).isoformat()}},
        )
        return {"ok": True, "restored": snapshot_id, "pre_restore_backup": restore_backup["id"]}


@api.post("/backup/restore-file")
async def backup_restore_file(file: UploadFile = File(...)):
    raw = await _read_capped(file)
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, f"File di backup non leggibile: {exc}")
    collections = _validate_backup_file(payload)
    async with _restore_lock():
        restore_backup = await create_backup_snapshot(
            f"Prima del ripristino file {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}",
            "pre-restore-file",
        )
        await _run_transactionally(lambda session: _restore_collections(collections, session))
        if "vending" in collections:
            snapshot_at = payload.get("created_at") or datetime.now(timezone.utc).isoformat()
            await db.vending.update_many({}, {"$set": {
                "giacenza_aggiornata_il": snapshot_at,
                "giacenza_sorgente": "BACKUP_FILE_RIPRISTINATO",
            }})
        return {
            "ok": True,
            "file": file.filename,
            "restored_docs": sum(len(docs) for docs in collections.values()),
            "pre_restore_backup": restore_backup["id"],
        }


def _import_includes_accounting(report: Dict[str, Any]) -> bool:
    """Indica se l'import contiene la fotografia contabile RIEP_VENDITA."""
    return "RIEP_VENDITA" in (report.get("fogli_trovati") or [])


async def record_import_history(file_name: str, report: Dict[str, Any], backup_id: Optional[str] = None):
    totals = report.get("totali", {})
    includes_accounting = _import_includes_accounting(report)
    doc = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "file": file_name,
        "backup_id": backup_id,
        "tipo_import": report.get("tipo_import") or ("completo" if includes_accounting else "giacenze"),
        "contabilita_inclusa": includes_accounting,
        "fogli_trovati": report.get("fogli_trovati", []),
        "fogli_mancanti": report.get("fogli_mancanti", []),
        "totali": totals,
        "errori": sum((v or {}).get("errori", 0) for v in (report.get("dettaglio") or {}).values() if isinstance(v, dict)),
    }
    await db.import_history.insert_one(doc)
    return doc


async def _latest_accounting_import():
    """Trova l'ultimo import completo, ignorando gli upload di sole giacenze.

    La condizione sul nome del foglio mantiene compatibili gli import storici,
    creati prima dell'introduzione del flag ``contabilita_inclusa``.
    """
    return await db.import_history.find_one(
        {
            "$or": [
                {"contabilita_inclusa": True},
                {"fogli_trovati": "RIEP_VENDITA"},
            ]
        },
        {"_id": 0},
        sort=[("created_at", -1)],
    )


@api.get("/import/history")
async def import_history(limit: int = 20):
    capped_limit = max(1, min(int(limit or 20), 100))
    return await db.import_history.find({}, {"_id": 0}).sort("created_at", -1).limit(capped_limit).to_list(capped_limit)


async def data_status_payload() -> Dict[str, Any]:
    app_dates, imported_dates, counts, latest_import, latest_backup = await asyncio.gather(
        db.vendite.find({}, {"_id": 0, "data": 1}).to_list(None),
        db.db_storico_vend.find({}, {"_id": 0, "data": 1}).to_list(None),
        asyncio.gather(*[db[name].count_documents({}) for name in BACKUP_COLLECTIONS]),
        db.import_history.find_one({}, {"_id": 0}, sort=[("created_at", -1)]),
        db.backup_snapshots.find_one(
            {"status": {"$ne": "PRUNED"}}, {"_id": 0}, sort=[("created_at", -1)]
        ),
    )

    count_map = dict(zip(BACKUP_COLLECTIONS, counts))

    def date_only(raw: Any) -> Optional[str]:
        value = (raw or {}).get("data") if isinstance(raw, dict) else raw
        if not value:
            return None
        parsed = _parse_sale_date(value)
        return parsed.isoformat() if parsed else str(value)[:10]

    latest_app = _latest_sale_date(app_dates)
    latest_imported = _latest_sale_date(imported_dates)
    latest_days = [parsed for parsed in [latest_app, latest_imported] if parsed]
    latest_sales_day = max(latest_days).isoformat() if latest_days else None
    delay = None
    if latest_sales_day:
        try:
            delay = max(0, (datetime.now(timezone.utc).date() - datetime.fromisoformat(latest_sales_day).date()).days)
        except ValueError:
            delay = None
    return {
        "counts": count_map,
        "latest_app_sale": date_only(latest_app),
        "latest_imported_sale": date_only(latest_imported),
        "latest_sales_day": latest_sales_day,
        "sales_data_delay_days": delay,
        "latest_import": latest_import,
        "latest_backup": latest_backup,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@api.get("/data-status")
async def data_status():
    return await data_status_payload()


@api.get("/global-search")
async def global_search(q: str, limit: int = 8):
    text = (q or "").strip()
    if len(text) < 2:
        return {"items": []}
    capped = max(1, min(int(limit or 8), 20))
    rx = _q_regex(text)
    products, vending_totals = await asyncio.gather(
        db.prodotti.find(
            {"$or": [{"codice": rx}, {"descrizione": rx}]},
            {"_id": 0},
        ).sort("codice", 1).limit(capped).to_list(capped),
        _vending_stock_by_code(),
    )
    _apply_vending_stock(products, vending_totals)
    items = [
        {
            "type": "prodotto",
            "codice": p.get("codice"),
            "descrizione": p.get("descrizione", ""),
            "categoria": p.get("categoria", ""),
            "prezzo": p.get("prezzo", 0),
            "giacenza_negozio": p.get("giacenza_negozio", 0),
            "giacenza_vending": p.get("giacenza_vending", 0),
        }
        for p in products
    ]
    return {"items": items}


@api.get("/anomalie")
async def anomalie():
    prodotti = await db.prodotti.find({}, {"_id": 0}).to_list(5000)
    product_codes = {p.get("codice") for p in prodotti if p.get("codice")}
    items = []
    for p in prodotti:
        codice = p.get("codice", "")
        if int(p.get("giacenza_negozio", 0) or 0) < 0:
            items.append({"tipo": "stock_negativo", "severita": "alta", "codice": codice, "descrizione": p.get("descrizione", ""), "messaggio": f"Giacenza negozio negativa ({p.get('giacenza_negozio')})", "azione": "Verifica import o movimenti recenti"})
        if not float(p.get("prezzo", 0) or 0):
            items.append({"tipo": "prezzo_mancante", "severita": "media", "codice": codice, "descrizione": p.get("descrizione", ""), "messaggio": "Prezzo a zero", "azione": "Aggiorna prodotto o listino ADM"})
        if not p.get("descrizione"):
            items.append({"tipo": "descrizione_mancante", "severita": "bassa", "codice": codice, "descrizione": "", "messaggio": "Descrizione mancante", "azione": "Completa anagrafica prodotto"})
    vendite_codes = await db.vendite.distinct("codice")
    storico_codes = await db.db_storico_vend.distinct("codice")
    unknown_codes = sorted({c for c in [*vendite_codes, *storico_codes] if c and c not in product_codes})[:200]
    for codice in unknown_codes:
        items.append({"tipo": "vendita_senza_prodotto", "severita": "media", "codice": codice, "descrizione": "", "messaggio": "Vendite presenti ma prodotto non trovato", "azione": "Importa listino/prodotti aggiornati o crea prodotto"})
    status = await data_status_payload()
    if status.get("sales_data_delay_days") is not None and status["sales_data_delay_days"] > 2:
        items.insert(0, {
            "tipo": "storico_vecchio",
            "severita": "alta",
            "codice": "",
            "descrizione": "",
            "messaggio": f"Ultima vendita disponibile: {status.get('latest_sales_day')} ({status['sales_data_delay_days']} giorni fa)",
            "azione": "Importa Excel aggiornato prima di confermare ordini",
        })
    for item in items:
        item["id"] = hashlib.sha256(
            f"{item.get('tipo', '')}\0{item.get('codice', '')}".encode("utf-8")
        ).hexdigest()[:24]
    ignored = set(await db.anomalie_ignorate.distinct("key"))
    items = [item for item in items if item["id"] not in ignored]
    summary = {
        "alta": sum(1 for i in items if i["severita"] == "alta"),
        "media": sum(1 for i in items if i["severita"] == "media"),
        "bassa": sum(1 for i in items if i["severita"] == "bassa"),
        "totale": len(items),
    }
    return {"summary": summary, "items": items}


@api.delete("/anomalie")
async def dismiss_all_anomalies():
    current = await anomalie()
    now = datetime.now(timezone.utc).isoformat()
    keys = [item["id"] for item in current["items"]]
    if keys:
        existing = set(await db.anomalie_ignorate.distinct("key", {"key": {"$in": keys}}))
        missing = [{"key": key, "dismissed_at": now} for key in keys if key not in existing]
        if missing:
            await db.anomalie_ignorate.insert_many(missing)
    return {"ok": True, "eliminate": len(keys)}


@api.delete("/anomalie/{item_key}")
async def dismiss_anomaly(item_key: str):
    current = await anomalie()
    if item_key not in {item["id"] for item in current["items"]}:
        raise HTTPException(404, "Anomalia non trovata")
    await db.anomalie_ignorate.update_one(
        {"key": item_key},
        {"$set": {"key": item_key, "dismissed_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    return {"ok": True}


@api.get("/report/giornaliero")
async def report_giornaliero():
    status, dashboard_data, anomaly_data, ao = await asyncio.gather(
        data_status_payload(),
        dashboard(),
        anomalie(),
        auto_order(),
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "dashboard": dashboard_data,
        "anomalie": anomaly_data["summary"],
        "auto_order": {
            "righe": ao.get("n_righe", 0),
            "totale": ao.get("totale", 0),
            "data_riferimento_domanda": ao.get("data_riferimento_domanda"),
            "da_controllare": (ao.get("riepilogo_stati", {}).get("CONTROLLO MANUALE", 0) or 0) + (ao.get("n_anomalie_stock", 0) or 0),
        },
    }


def _csv_vending_datetime(value: Any) -> str:
    """Normalizza le date esportate dal distributore in un ISO filtrabile."""
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc).isoformat()
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).isoformat()
    except ValueError:
        pass
    for fmt in (
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
        "%d/%m/%y %H:%M:%S", "%d/%m/%y %H:%M",
        "%d-%m-%y %H:%M:%S", "%d-%m-%y %H:%M",
        "%d/%m/%Y", "%d-%m-%Y",
        "%d/%m/%y", "%d-%m-%y",
    ):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    raise ValueError(f"data non riconosciuta: {text}")


def _normalize_vending_column(value: Any) -> str:
    """Converte le colonne esportate come ``1-B02`` nel codice interno ``B02``."""
    column = re.sub(r"\s+", "", str(value or "").strip().upper())
    match = re.fullmatch(r"\d+[-/]([A-Z]+\d+)", column)
    return match.group(1) if match else column


def _parse_csv_vending(raw: str, pagamento: str = "CONTANTI") -> Dict[str, Any]:
    """Condivide parsing e validazione tra anteprima e import effettivo."""
    import csv
    sample = raw[:2000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except Exception:
        delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(raw), delimiter=delim)
    # normalizza header
    def norm(s): return re.sub(r"\s+", " ", (s or "").strip().lower())
    field_map = {norm(k): k for k in (reader.fieldnames or [])}
    def pick(row, *keys):
        for k in keys:
            actual = field_map.get(norm(k))
            if actual and row.get(actual) not in (None, ""):
                return row.get(actual)
        return None

    skipped = 0
    errors = []
    rows = []
    for i, row in enumerate(reader):
        try:
            codice = str(pick(row, "codice", "codice aams", "cod aams", "cod", "aams") or "").strip()
            nome = str(pick(row, "nome prodotto", "prodotto", "descrizione", "articolo") or "").strip()
            prezzo = pick(row, "prezzo", "importo")
            data = pick(row, "data", "date")
            colonna = _normalize_vending_column(pick(row, "colonna", "column"))
            categoria = str(pick(row, "categoria", "tipo") or "").strip()
            pag = str(pick(row, "pagamento", "payment") or pagamento).strip().upper() or pagamento.upper()

            if not codice and not nome:
                skipped += 1
                continue
            prezzo_f = nonnegative_money(prezzo) if prezzo not in (None, "") else 0.0
            data_iso = _csv_vending_datetime(data)
            rows.append({
                "riga": i + 2,
                "data": data_iso,
                "codice": codice,
                "nome": nome,
                "prezzo": prezzo_f,
                # Freeze the pre-hardening fingerprint: rounding money must not
                # turn a previously imported fractional-cent row into a new sale.
                "_identity_price": f"{float(str(prezzo or 0).replace(',', '.')):.2f}",
                "colonna": colonna,
                "categoria": categoria,
                "pagamento": pag,
            })
        except Exception as exc:
            errors.append({"riga": i + 2, "errore": str(exc)})
    return {"righe": rows, "saltati": skipped, "errori": errors, "delimitatore": delim}


def _summarize_vending_csv(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Riepiloga l'intero CSV senza applicare filtri dello storico Excel."""
    payment_counts: Dict[str, int] = {}
    payment_amounts: Dict[str, float] = {}
    dates = []
    for row in parsed.get("righe", []):
        bucket = _vending_payment_bucket(row.get("pagamento"))
        payment_counts[bucket] = payment_counts.get(bucket, 0) + 1
        payment_amounts[bucket] = money_sum([payment_amounts.get(bucket, 0), row.get("prezzo") or 0])
        parsed_date = _parse_sale_datetime(row.get("data"))
        if parsed_date:
            dates.append(parsed_date)
    electronic = money_sum(payment_amounts.get(bucket, 0) for bucket in ("CARTE", "PAGOBANCOMAT", "ALTRO_ELETTRONICO"))
    total = money_sum(payment_amounts.values())
    return {
        "righe": len(parsed.get("righe", [])),
        "saltati": int(parsed.get("saltati", 0)),
        "errori": parsed.get("errori", []),
        "pagamenti": payment_counts,
        "importi_pagamenti": payment_amounts,
        "totale_elettronici": electronic,
        "totale": total,
        "data_da": min(dates).isoformat() if dates else None,
        "data_a": max(dates).isoformat() if dates else None,
    }


def _parse_sale_datetime(value: Any):
    """Normalizza data e ora delle vendite senza perdere il dettaglio orario."""
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None
    if parsed:
        return parsed.replace(tzinfo=None)
    for fmt in (
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
        "%d/%m/%y %H:%M:%S", "%d/%m/%y %H:%M",
        "%d-%m-%y %H:%M:%S", "%d-%m-%y %H:%M",
        "%d/%m/%Y", "%d-%m-%Y",
        "%d/%m/%y", "%d-%m-%y",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _latest_historical_vending_datetime(documents: List[Dict[str, Any]]):
    """Restituisce l'ultimo timestamp già incluso nello storico vending Excel."""
    parsed_datetimes = []
    for document in documents:
        raw = document.get("raw") or []
        # DB_STORICO_VENDING_EXT: DATA_ORA è la seconda colonna. DATA resta
        # il fallback per vecchi file che non conservano l'orario.
        value = raw[1] if len(raw) > 1 and raw[1] else (raw[2] if len(raw) > 2 else None)
        parsed = _parse_sale_datetime(value)
        if parsed:
            parsed_datetimes.append(parsed)
    return max(parsed_datetimes, default=None)


def _filter_new_csv_vending_rows(
    rows: List[Dict[str, Any]], historical_documents: List[Dict[str, Any]]
) -> tuple[List[Dict[str, Any]], Any]:
    """Tiene solo le righe successive all'ultima vendita presente nell'Excel."""
    cutoff = _latest_historical_vending_datetime(historical_documents)
    if not cutoff:
        return rows, None
    return [row for row in rows if (_parse_sale_datetime(row.get("data")) or datetime.min) > cutoff], cutoff


def _csv_vending_event_base(row: Dict[str, Any]) -> str:
    """Identifica un tipo di riga senza confondere pezzi identici della stessa vendita."""
    payload = {
        "data": str(row.get("data") or ""),
        "colonna": _normalize_vending_column(row.get("colonna")),
        "codice": _product_code_text(row.get("codice")),
        "prezzo": row.get("_identity_price", f"{float(row.get('prezzo') or 0):.2f}"),
        "pagamento": str(row.get("pagamento") or "").strip().upper(),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _assign_csv_vending_event_keys(rows: List[Dict[str, Any]]) -> None:
    """Assegna identità esatta e contabile a ogni riga del distributore.

    ``csv_event_key`` conserva i secondi ed è utile per l'audit puntuale.
    ``csv_accounting_key`` usa invece la firma compatibile al minuto: resta
    stabile quando due export della stessa vendita alternano timestamp con e
    senza secondi. L'occorrenza mantiene distinti più pezzi realmente venduti
    nello stesso minuto con gli stessi dati.
    """
    exact_occurrences: Dict[str, int] = {}
    for row in rows:
        base = _csv_vending_event_base(row)
        occurrence = exact_occurrences.get(base, 0) + 1
        exact_occurrences[base] = occurrence
        row["csv_event_occurrence"] = occurrence
        row["csv_event_key"] = f"{base}:{occurrence}"
    _assign_csv_vending_accounting_keys(rows)


def _csv_vending_compatibility_key(row: Dict[str, Any]) -> str:
    """Chiave al minuto per confrontare export vecchi e nuovi del distributore.

    Gli export storici omettevano i secondi (``07/10/2026 23:58``), mentre
    quelli recenti li includono (``07-10-2026 23:58:16``). La chiave evento
    esatta resta la deduplicazione principale; questa firma viene usata solo
    come compatibilita' per le righe che non hanno trovato una corrispondenza
    esatta.
    """
    parsed = _parse_sale_datetime(row.get("data"))
    minute = parsed.strftime("%Y-%m-%dT%H:%M") if parsed else str(row.get("data") or "")[:16]
    raw_code = _product_code_text(row.get("codice"))
    numeric_code = adm_numeric_code(raw_code) if raw_code.isdigit() or raw_code.startswith("AMMS") else ""
    identity = numeric_code or raw_code
    if not identity:
        identity = re.sub(
            r"\s+", " ", str(row.get("nome") or row.get("descrizione") or "").strip().upper()
        )
    payload = {
        "minuto": minute,
        "colonna": _normalize_vending_column(row.get("colonna")),
        "prodotto": identity,
        "prezzo": row.get("_identity_price", f"{float(row.get('prezzo', row.get('importo', 0)) or 0):.2f}"),
        "pagamento": _vending_payment_bucket(row.get("pagamento")),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _assign_csv_vending_accounting_keys(rows: List[Dict[str, Any]]) -> None:
    """Assegna la chiave contabile v2 senza modificare l'identità esatta."""
    occurrences: Dict[str, int] = {}
    for row in rows:
        accounting_base = _csv_vending_compatibility_key(row)
        occurrence = occurrences.get(accounting_base, 0) + 1
        occurrences[accounting_base] = occurrence
        row["csv_event_compatibility_key"] = accounting_base
        row["csv_accounting_occurrence"] = occurrence
        row["csv_accounting_key"] = f"{accounting_base}:{occurrence}"


async def _exclude_imported_csv_vending_rows(
    rows: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], int]:
    """Esclude eventi gia' importati, inclusi quelli salvati senza secondi."""
    if not rows:
        return [], 0

    keys = [row["csv_event_key"] for row in rows]
    parsed_dates = [
        parsed for parsed in (_parse_sale_datetime(row.get("data")) for row in rows) if parsed
    ]
    date_floor = min(parsed_dates).replace(second=0, microsecond=0).isoformat()
    date_ceiling = (max(parsed_dates).replace(second=0, microsecond=0) + timedelta(minutes=1)).isoformat()
    existing_rows = await db.vendite.find(
        {
            "sorgente": "CSV_VENDING",
            "data": {"$gte": date_floor, "$lt": date_ceiling},
        },
        {
            "_id": 0,
            "csv_event_key": 1,
            "data": 1,
            "codice": 1,
            "descrizione": 1,
            "importo": 1,
            "colonna": 1,
            "pagamento": 1,
        },
    ).to_list(None)

    existing_exact_keys = {
        str(row.get("csv_event_key") or "") for row in existing_rows if row.get("csv_event_key")
    }
    exact_matches = {key for key in keys if key in existing_exact_keys}

    # Conta le vendite persistite per firma al minuto. Sottrai quelle gia'
    # abbinate esattamente, poi usa il residuo per assorbire soltanto le righe
    # legacy la cui precisione temporale e' cambiata tra due export.
    compatibility_counts: Dict[str, int] = {}
    for existing in existing_rows:
        compatibility_key = _csv_vending_compatibility_key(existing)
        compatibility_counts[compatibility_key] = compatibility_counts.get(compatibility_key, 0) + 1
    for row in rows:
        if row["csv_event_key"] not in exact_matches:
            continue
        compatibility_key = _csv_vending_compatibility_key(row)
        compatibility_counts[compatibility_key] = max(
            0, compatibility_counts.get(compatibility_key, 0) - 1
        )

    duplicate_ids = {id(row) for row in rows if row["csv_event_key"] in exact_matches}
    # Il file e' ordinato dal piu' recente al piu' vecchio. In caso di nuove
    # vendite identiche nello stesso minuto, abbina prima le piu' vecchie al
    # vecchio export e conserva come nuove le piu' recenti.
    for row in reversed(rows):
        if id(row) in duplicate_ids:
            continue
        compatibility_key = _csv_vending_compatibility_key(row)
        if compatibility_counts.get(compatibility_key, 0) <= 0:
            continue
        compatibility_counts[compatibility_key] -= 1
        duplicate_ids.add(id(row))

    new_rows = [row for row in rows if id(row) not in duplicate_ids]
    return new_rows, len(rows) - len(new_rows)


def _local_sale_datetime(value: Any) -> Optional[datetime]:
    """Confronta timestamp UTC di sistema e orari locali esportati dalla vending."""
    parsed = _parse_sale_datetime(value)
    if not parsed:
        return None
    text = str(value or "")
    if parsed.tzinfo is None and not re.search(r"(?:Z|[+-]\d\d:\d\d)$", text):
        return parsed
    aware = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return aware.astimezone(ZoneInfo("Europe/Rome")).replace(tzinfo=None)


async def _latest_restored_vending_snapshot_at() -> Optional[str]:
    restored = await db.backup_snapshots.find_one(
        {"last_restored_at": {"$exists": True}},
        {"_id": 0, "created_at": 1},
        sort=[("last_restored_at", -1)],
    )
    return str((restored or {}).get("created_at") or "") or None


async def _annotate_csv_vending_stock_effects(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Decide quali vendite sono successive alla fotografia fisica di ogni colonna."""
    column_codes = sorted({str(row.get("colonna") or "") for row in rows if row.get("colonna")})
    columns = await db.vending.find(
        {"colonna": {"$in": column_codes}},
        {"_id": 0},
    ).to_list(5000) if column_codes else []
    by_code = {str(column.get("colonna") or ""): column for column in columns}
    restored_snapshot = await _latest_restored_vending_snapshot_at()
    missing_baseline = set()
    missing_columns = set()
    stock_rows = 0
    reflected_rows = 0
    required_by_column: Dict[str, int] = {}

    for row in rows:
        column_code = str(row.get("colonna") or "")
        column = by_code.get(column_code)
        row["scala_giacenza"] = False
        if not column:
            row["giacenza_motivo"] = "COLONNA_NON_TROVATA"
            missing_columns.add(column_code or "(non indicata)")
            continue
        snapshot_value = column.get("giacenza_aggiornata_il") or restored_snapshot
        sale_at = _local_sale_datetime(row.get("data"))
        snapshot_at = _local_sale_datetime(snapshot_value)
        row["giacenza_rilevata_il"] = snapshot_value
        if not snapshot_at:
            missing_baseline.add(column_code)
            row["giacenza_motivo"] = "RIFERIMENTO_GIACENZA_MANCANTE"
            continue
        if sale_at and sale_at <= snapshot_at:
            reflected_rows += 1
            row["giacenza_motivo"] = "GIA_COMPRESA_NELLA_GIACENZA"
            continue
        row["scala_giacenza"] = True
        row["giacenza_motivo"] = "VENDITA_SUCCESSIVA_ALLA_GIACENZA"
        stock_rows += 1
        required_by_column[column_code] = required_by_column.get(column_code, 0) + 1

    stock_errors = []
    for column_code, required in sorted(required_by_column.items()):
        available = max(0, int(by_code[column_code].get("giacenza") or 0))
        if required > available:
            stock_errors.append({
                "colonna": column_code,
                "giacenza": available,
                "vendite_da_scalare": required,
                "mancanti": required - available,
            })
    return {
        "righe_da_scalare": stock_rows,
        "righe_gia_comprese_nella_giacenza": reflected_rows,
        "colonne_non_trovate": sorted(missing_columns),
        "colonne_senza_riferimento_giacenza": sorted(missing_baseline),
        "errori_giacenza": stock_errors,
        "importabile": not missing_columns and not missing_baseline and not stock_errors,
    }


async def _prepare_csv_vending_import(raw: str, pagamento: str) -> tuple[Dict[str, Any], Any, int]:
    parsed = _parse_csv_vending(raw, pagamento)
    historical_documents = await db.db_storico_vending_ext.find(
        {}, {"_id": 0, "raw": 1}
    ).to_list(None)
    all_rows = parsed["righe"]
    historical_rows, cutoff = _filter_new_csv_vending_rows(all_rows, historical_documents)
    _assign_csv_vending_event_keys(historical_rows)
    parsed["righe"], imported_count = await _exclude_imported_csv_vending_rows(historical_rows)
    parsed["righe_storico_excel"] = len(all_rows) - len(historical_rows)
    parsed["righe_csv_gia_importate"] = imported_count
    parsed["stock"] = await _annotate_csv_vending_stock_effects(parsed["righe"])
    return parsed, cutoff, len(all_rows)


async def _latest_vending_accounting_snapshot() -> Optional[Dict[str, Any]]:
    collection = getattr(db, "vending_accounting_snapshots", None)
    if collection is None:
        return None
    return await collection.find_one(
        {"status": "active"}, {"_id": 0}, sort=[("created_at", -1)]
    )


def _public_vending_accounting_snapshot(document: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value for key, value in document.items()
        if key not in {"_id", "sha256", "csv_event_keys"}
    }


VENDING_ACCOUNTING_STATE_ID = "ledger"


async def _ensure_vending_accounting_state() -> Dict[str, Any]:
    """Migra una sola volta l'ultima fotografia nel nuovo registro incrementale."""
    state = await db.vending_accounting_state.find_one(
        {"id": VENDING_ACCOUNTING_STATE_ID}, {"_id": 0}
    )
    if not state:
        previous = await _latest_vending_accounting_snapshot()
        now = datetime.now(timezone.utc).isoformat()
        initial = {
            "id": VENDING_ACCOUNTING_STATE_ID,
            "created_at": now,
            "ledger_started_at": (previous or {}).get("created_at") or now,
            "updated_at": now,
            "baseline_importi_pagamenti": dict((previous or {}).get("importi_pagamenti") or {}),
            "baseline_righe": int((previous or {}).get("righe") or 0),
            "baseline_data_da": (previous or {}).get("data_da"),
            "baseline_data_a": (previous or {}).get("data_a"),
            # La vecchia fotografia contiene al massimo le 1.000 chiavi dell'ultimo
            # export. Servono solo a non reinserire quel blocco durante la migrazione.
            "baseline_event_keys": list((previous or {}).get("csv_event_keys") or []),
            "baseline_accounting_keys": list((previous or {}).get("csv_accounting_keys") or []),
            "accounting_identity_version": 2,
            "migrated_from_snapshot_id": (previous or {}).get("id"),
            "last_file_name": (previous or {}).get("file_name"),
            "last_file_sha256": (previous or {}).get("sha256"),
            "last_file_rows": int((previous or {}).get("righe") or 0),
            "last_data_da": (previous or {}).get("data_da"),
            "last_data_a": (previous or {}).get("data_a"),
            "last_aligned_at": (previous or {}).get("created_at"),
        }
        await db.vending_accounting_state.update_one(
            {"id": VENDING_ACCOUNTING_STATE_ID},
            {"$setOnInsert": initial},
            upsert=True,
        )
        state = await db.vending_accounting_state.find_one(
            {"id": VENDING_ACCOUNTING_STATE_ID}, {"_id": 0}
        )

    if not state.get("legacy_sales_seeded_at"):
        baseline_keys = set(state.get("baseline_event_keys") or [])
        legacy_sales = await db.vendite.find(
            {"sorgente": "CSV_VENDING", "csv_event_key": {"$exists": True}},
            {
                "_id": 0, "csv_event_key": 1, "data": 1, "importo": 1,
                "pagamento": 1, "batch_id": 1, "created_at": 1, "csv_file_sha256": 1,
                "csv_accounting_key": 1, "csv_accounting_occurrence": 1,
                "csv_event_compatibility_key": 1, "codice": 1, "descrizione": 1,
                "colonna": 1,
            },
        ).to_list(None)
        generated_accounting_rows = [
            {
                **sale,
                "prezzo": sale.get("importo"),
                "nome": sale.get("descrizione"),
            }
            for sale in legacy_sales
        ]
        if any(not sale.get("csv_accounting_key") for sale in legacy_sales):
            generated_accounting_rows.sort(
                key=lambda row: (str(row.get("data") or ""), str(row.get("csv_event_key") or "")),
                reverse=True,
            )
            _assign_csv_vending_accounting_keys(generated_accounting_rows)
            generated_by_exact_key = {
                row.get("csv_event_key"): row for row in generated_accounting_rows
            }
            for sale in legacy_sales:
                if sale.get("csv_accounting_key"):
                    continue
                generated = generated_by_exact_key.get(sale.get("csv_event_key"))
                if not generated:
                    continue
                sale["csv_event_compatibility_key"] = generated["csv_event_compatibility_key"]
                sale["csv_accounting_occurrence"] = generated["csv_accounting_occurrence"]
                sale["csv_accounting_key"] = generated["csv_accounting_key"]
        baseline_accounting_keys = set(state.get("baseline_accounting_keys") or [])
        operations = [
            UpdateOne(
                {"$or": [
                    {"csv_accounting_key": sale["csv_accounting_key"]},
                    {"csv_event_key": sale["csv_event_key"]},
                ]},
                {"$setOnInsert": {
                    "id": str(uuid.uuid4()),
                    "csv_event_key": sale["csv_event_key"],
                    "csv_event_compatibility_key": sale.get("csv_event_compatibility_key"),
                    "csv_accounting_occurrence": sale.get("csv_accounting_occurrence"),
                    "csv_accounting_key": sale["csv_accounting_key"],
                    "data": sale.get("data"),
                    "importo": money(sale.get("importo") or 0),
                    "pagamento": _vending_payment_bucket(sale.get("pagamento")),
                    "source_batch_id": sale.get("batch_id"),
                    "first_seen_at": sale.get("created_at") or state.get("created_at"),
                    "file_sha256": sale.get("csv_file_sha256"),
                    "migrated_from_vendite": True,
                }},
                upsert=True,
            )
            for sale in legacy_sales
            if sale.get("csv_event_key") not in baseline_keys
            and sale.get("csv_accounting_key") not in baseline_accounting_keys
        ]
        seed_result = await _bulk_upsert(db.vending_accounting_events, operations, batch_size=500)
        if seed_result["errori"]:
            raise HTTPException(500, "Migrazione del registro contabile CSV incompleta")
        seeded_at = datetime.now(timezone.utc).isoformat()
        await db.vending_accounting_state.update_one(
            {"id": VENDING_ACCOUNTING_STATE_ID},
            {"$set": {
                "legacy_sales_seeded_at": seeded_at,
                "legacy_sales_seeded": seed_result["inseriti"],
            }},
        )
        state["legacy_sales_seeded_at"] = seeded_at
        state["legacy_sales_seeded"] = seed_result["inseriti"]
    return state


async def _vending_accounting_ledger_summary() -> Optional[Dict[str, Any]]:
    state_collection = getattr(db, "vending_accounting_state", None)
    event_collection = getattr(db, "vending_accounting_events", None)
    if state_collection is None or event_collection is None:
        return None
    state = await state_collection.find_one(
        {"id": VENDING_ACCOUNTING_STATE_ID}, {"_id": 0}
    )
    if not state:
        return None
    amounts = {
        str(bucket): money_decimal(amount or 0)
        for bucket, amount in (state.get("baseline_importi_pagamenti") or {}).items()
    }
    dates = [value for value in [state.get("baseline_data_da"), state.get("baseline_data_a")] if value]
    event_count = 0
    async for event in event_collection.find({}, {"_id": 0, "pagamento": 1, "importo": 1, "data": 1}):
        bucket = _vending_payment_bucket(event.get("pagamento"))
        amounts[bucket] = amounts.get(bucket, Decimal(0)) + money_decimal(event.get("importo") or 0)
        event_count += 1
        if event.get("data"):
            dates.append(event["data"])
    amounts = {bucket: money(amount) for bucket, amount in amounts.items()}
    cash = money(amounts.get("CONTANTI", 0))
    electronic = money_sum(
        value for bucket, value in amounts.items() if bucket != "CONTANTI"
    )
    return {
        "id": VENDING_ACCOUNTING_STATE_ID,
        "status": "active",
        "created_at": state.get("last_aligned_at") or state.get("updated_at") or state.get("created_at"),
        "file_name": state.get("last_file_name"),
        "righe": int(state.get("baseline_righe") or 0) + event_count,
        "eventi_incrementali": event_count,
        "importi_pagamenti": amounts,
        "totale_contanti": cash,
        "totale_elettronici": electronic,
        "totale": money_sum([cash, electronic]),
        "data_da": min(dates, default=None),
        "data_a": max(dates, default=None),
        "last_file_rows": int(state.get("last_file_rows") or 0),
        "baseline_event_keys": list(state.get("baseline_event_keys") or []),
        "baseline_accounting_keys": list(state.get("baseline_accounting_keys") or []),
        "accounting_identity_version": int(state.get("accounting_identity_version") or 1),
        "migrated_from_snapshot_id": state.get("migrated_from_snapshot_id"),
        "ledger_started_at": state.get("ledger_started_at") or state.get("created_at"),
    }


async def _store_vending_accounting_snapshot(
    content: bytes,
    file_name: str,
    *,
    source_batch_id: Optional[str] = None,
    backup_reason: Optional[str] = None,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Accumula soltanto gli eventi nuovi del CSV, senza perdere quelli usciti dalla finestra."""
    parsed = _parse_csv_vending(content.decode("utf-8-sig", errors="replace"))
    _assign_csv_vending_event_keys(parsed["righe"])
    summary = _summarize_vending_csv(parsed)
    if not summary["righe"]:
        raise HTTPException(422, "Il CSV non contiene vendite valide")
    if summary["errori"]:
        raise HTTPException(
            422,
            f"Il CSV contiene {len(summary['errori'])} righe non valide: correggile prima dell'allineamento",
        )

    backup = await create_backup_snapshot(
        backup_reason,
        "pre-allineamento-contabile-vending",
        [
            "vending_accounting_snapshots",
            "vending_accounting_state",
            "vending_accounting_events",
        ],
    ) if backup_reason else None
    created_at = datetime.now(timezone.utc).isoformat()
    state = await _ensure_vending_accounting_state()
    baseline_keys = set(state.get("baseline_event_keys") or [])
    baseline_accounting_keys = set(state.get("baseline_accounting_keys") or [])
    exact_keys = [row["csv_event_key"] for row in parsed["righe"]]
    accounting_keys = [row["csv_accounting_key"] for row in parsed["righe"]]
    existing_events = await db.vending_accounting_events.find(
        {"$or": [
            {"csv_event_key": {"$in": exact_keys}},
            {"csv_accounting_key": {"$in": accounting_keys}},
        ]},
        {"_id": 0, "csv_event_key": 1, "csv_accounting_key": 1},
    ).to_list(None)
    existing_exact_keys = {
        str(event.get("csv_event_key") or "") for event in existing_events
        if event.get("csv_event_key")
    }
    existing_accounting_keys = {
        str(event.get("csv_accounting_key") or "") for event in existing_events
        if event.get("csv_accounting_key")
    }
    operations = []
    for row in parsed["righe"]:
        if (
            row["csv_event_key"] in baseline_keys
            or row["csv_accounting_key"] in baseline_accounting_keys
            or row["csv_event_key"] in existing_exact_keys
            or row["csv_accounting_key"] in existing_accounting_keys
        ):
            continue
        operations.append(UpdateOne(
            {"csv_accounting_key": row["csv_accounting_key"]},
            {"$setOnInsert": {
                "id": str(uuid.uuid4()),
                "csv_event_key": row["csv_event_key"],
                "csv_event_compatibility_key": row["csv_event_compatibility_key"],
                "csv_accounting_occurrence": row["csv_accounting_occurrence"],
                "csv_accounting_key": row["csv_accounting_key"],
                "data": row.get("data"),
                "importo": money(row.get("prezzo") or 0),
                "pagamento": _vending_payment_bucket(row.get("pagamento")),
                "source_batch_id": source_batch_id,
                "first_seen_at": created_at,
                "file_sha256": hashlib.sha256(content).hexdigest(),
            }},
            upsert=True,
        ))
    write_result = await _bulk_upsert(db.vending_accounting_events, operations, batch_size=500)
    if write_result["errori"]:
        raise HTTPException(500, "Registro contabile CSV incompleto: riprova")
    await db.vending_accounting_state.update_one(
        {"id": VENDING_ACCOUNTING_STATE_ID},
        {"$set": {
            "updated_at": created_at,
            "last_aligned_at": created_at,
            "last_file_name": Path(file_name or "vendite-vending.csv").name,
            "last_file_sha256": hashlib.sha256(content).hexdigest(),
            "last_file_rows": int(summary.get("righe") or 0),
            "last_data_da": summary.get("data_da"),
            "last_data_a": summary.get("data_a"),
            "accounting_identity_version": 2,
        }},
    )
    document = await _vending_accounting_ledger_summary()
    document["eventi_nuovi"] = write_result["inseriti"]
    return document, backup


@api.post("/contabilita/vending/preview-csv")
async def preview_vending_accounting_csv(file: UploadFile = File(...)):
    """Controlla l'intero export vending destinato alla riconciliazione contabile."""
    content = await _read_capped(file)
    parsed = _parse_csv_vending(content.decode("utf-8-sig", errors="replace"))
    return _summarize_vending_csv(parsed)


@api.post("/contabilita/vending/allinea-csv")
async def align_vending_accounting_csv(file: UploadFile = File(...)):
    """Rende il CSV fonte contabile senza modificare giacenze o venduti."""
    content = await _read_capped(file)
    document, backup = await _store_vending_accounting_snapshot(
        content,
        file.filename or "vendite-vending.csv",
        backup_reason="Prima dell'allineamento contabile al CSV vending",
    )
    response = _public_vending_accounting_snapshot(document)
    response["backup_id"] = (backup or {}).get("id")
    return response


@api.get("/contabilita/vending/fonte")
async def vending_accounting_source():
    ledger = await _vending_accounting_ledger_summary()
    if ledger:
        return _public_vending_accounting_snapshot(ledger)
    snapshot = await _latest_vending_accounting_snapshot()
    return _public_vending_accounting_snapshot(snapshot) if snapshot else None


@api.get("/contabilita/riconciliazione")
async def accounting_reconciliation(date_from: Optional[str] = None, date_to: Optional[str] = None):
    for value, label in ((date_from, "date_from"), (date_to, "date_to")):
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise HTTPException(422, f"{label} deve avere formato YYYY-MM-DD")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from non può essere successiva a date_to")
    return await reconcile(db, _vending_payment_bucket, date_from=date_from, date_to=date_to)


VENDING_CASH_STATE_ID = "saldo"


async def _vending_cash_balance(session=None) -> float:
    """Restituisce la giacenza cash, inizializzandola dai dati legacy una volta."""
    kwargs = _session_kwargs(session)
    state = await db.cassa_vending_stato.find_one(
        {"id": VENDING_CASH_STATE_ID}, {"_id": 0}, **kwargs
    )
    if state:
        return money(state.get("giacenza") or 0)

    legacy_docs = await db.prelievi_vending.find(
        {}, {"_id": 0, "importo": 1}, **kwargs
    ).to_list(None)
    legacy_balance = money_sum(doc.get("importo") or 0 for doc in legacy_docs)
    now = datetime.now(timezone.utc).isoformat()
    await db.cassa_vending_stato.update_one(
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
    state = await db.cassa_vending_stato.find_one(
        {"id": VENDING_CASH_STATE_ID}, {"_id": 0}, **kwargs
    )
    return money((state or {}).get("giacenza") or 0)


async def _adjust_vending_cash_balance(amount: float, session=None) -> float:
    kwargs = _session_kwargs(session)
    delta = money_decimal(amount)
    await _vending_cash_balance(session)
    # Compare-and-set preserves concurrent increments without binary $inc drift.
    for _ in range(20):
        state = await db.cassa_vending_stato.find_one({"id": VENDING_CASH_STATE_ID}, **kwargs)
        previous = state.get("giacenza")
        updated = money(money_decimal(previous or 0) + delta)
        result = await db.cassa_vending_stato.update_one(
            {"id": VENDING_CASH_STATE_ID, "giacenza": previous},
            {"$set": {"giacenza": updated, "updated_at": datetime.now(timezone.utc).isoformat()}},
            **kwargs,
        )
        if result.matched_count:
            return updated
    raise HTTPException(409, "Cassa vending modificata contemporaneamente: riprovare")


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




# ------------------------- Vending -------------------------
@api.post("/vending")
async def create_vending_column(payload: VendingCreateIn):
    colonna = payload.colonna.strip().upper()
    codice = _product_code_text(payload.codice)
    if not re.fullmatch(r"[A-Z]\d+", colonna):
        raise HTTPException(422, "La colonna deve avere un formato come A01")
    if payload.capacita_max <= 0:
        raise HTTPException(422, "La capacità deve essere maggiore di zero")
    if payload.soglia_minima < 0 or payload.soglia_minima > payload.capacita_max:
        raise HTTPException(422, "La soglia deve essere compresa tra zero e la capacità")
    if payload.giacenza_iniziale < 0 or payload.giacenza_iniziale > payload.capacita_max:
        raise HTTPException(422, "La giacenza iniziale deve essere compresa tra zero e la capacità")

    existing, prodotto, params = await asyncio.gather(
        db.vending.find_one({"colonna": colonna}),
        db.prodotti.find_one({"codice": codice}),
        get_params(),
    )
    if existing:
        raise HTTPException(409, f"Colonna vending già presente: {colonna}")
    if not prodotto:
        raise HTTPException(404, f"Prodotto non trovato: {codice}")

    scorta_minima = max(0, int(_param(params, "SCORTA_MINIMA_NEGOZIO_VENDING", 2)))
    disponibile = max(0, int(prodotto.get("giacenza_negozio", 0) or 0))
    caricabile = max(0, disponibile - scorta_minima)
    if payload.giacenza_iniziale > caricabile:
        raise HTTPException(
            409,
            f"Scorta negozio protetta: giacenza iniziale {payload.giacenza_iniziale}, "
            f"caricabili {caricabile} (disponibili {disponibile}, riserva {scorta_minima})",
        )

    now = datetime.now(timezone.utc).isoformat()
    document = VendingColonna(
        colonna=colonna,
        codice=codice,
        descrizione=str(prodotto.get("descrizione") or codice),
        giacenza=payload.giacenza_iniziale,
        capacita_max=payload.capacita_max,
        soglia_minima=payload.soglia_minima,
    ).model_dump()
    document.update({
        "created_at": now,
        "giacenza_aggiornata_il": now,
        "giacenza_sorgente": "INSERIMENTO_MANUALE",
    })

    product_updated = False
    try:
        if payload.giacenza_iniziale:
            result = await db.prodotti.update_one(
                {
                    "codice": codice,
                    "giacenza_negozio": {"$gte": payload.giacenza_iniziale + scorta_minima},
                },
                {"$inc": {
                    "giacenza_negozio": -payload.giacenza_iniziale,
                    "giacenza_vending": payload.giacenza_iniziale,
                }},
            )
            if result.matched_count == 0:
                raise RuntimeError("la disponibilità negozio è cambiata")
            product_updated = True
        if await db.vending.find_one({"colonna": colonna}):
            raise RuntimeError(f"la colonna {colonna} è stata creata nel frattempo")
        # PyMongo aggiunge `_id` al dizionario ricevuto: inseriamo una copia per
        # mantenere la risposta API priva di ObjectId e quindi serializzabile.
        await db.vending.insert_one(dict(document))
    except Exception as exc:
        if product_updated:
            await db.prodotti.update_one(
                {"codice": codice},
                {"$inc": {
                    "giacenza_negozio": payload.giacenza_iniziale,
                    "giacenza_vending": -payload.giacenza_iniziale,
                }},
            )
        raise HTTPException(409, f"Inserimento vending annullato: {exc}")

    return {
        **document,
        "giacenza_magazzino": disponibile - payload.giacenza_iniziale,
        "scorta_minima_negozio": scorta_minima,
        "giacenza_caricabile": caricabile - payload.giacenza_iniziale,
    }


@api.get("/vending")
async def list_vending():
    docs, prodotti, params = await asyncio.gather(
        db.vending.find({}, {"_id": 0}).sort("colonna", 1).to_list(500),
        db.prodotti.find({}, {"_id": 0, "codice": 1, "giacenza_negozio": 1}).to_list(5000),
        get_params(),
    )
    scorta_minima = max(0, int(_param(params, "SCORTA_MINIMA_NEGOZIO_VENDING", 2)))
    disponibilita_per_codice = {
        p.get("codice"): max(0, int(p.get("giacenza_negozio", 0) or 0))
        for p in prodotti
        if p.get("codice")
    }
    caricabile_residuo_per_codice = {
        codice: max(0, disponibile - scorta_minima)
        for codice, disponibile in disponibilita_per_codice.items()
    }
    # arricchisci con esito/proposta
    # Invariante ricarica vending:
    # - una proposta automatica prova a portare la colonna fino alla capacità massima;
    # - il magazzino usato qui è la giacenza_negozio fisica libera, già al netto
    #   delle vendite/import e delle quantità presenti in vending;
    # - la ricarica scatta quando la colonna è alla/sotto soglia minima;
    # - la scorta minima negozio non può mai essere trasferita alla vending;
    # - se lo stock eccedente non basta a riempire la colonna, proponiamo soltanto
    #   la quantità sicura; per codici condivisi la disponibilità viene assegnata
    #   una sola volta, in ordine di colonna.
    out = []
    for d in docs:
        cap = d.get("capacita_max")
        cap = 5 if cap is None else int(cap)
        giac = d.get("giacenza", 0) or 0
        soglia = d.get("soglia_minima")
        soglia = 2 if soglia is None else int(soglia)
        fabbisogno = max(0, cap - giac)
        sotto_soglia = giac <= soglia
        codice = d.get("codice")
        disponibile = disponibilita_per_codice.get(codice, 0)
        caricabile_totale = max(0, disponibile - scorta_minima)
        caricabile_residuo = caricabile_residuo_per_codice.get(codice, 0)
        proposta = min(fabbisogno, caricabile_residuo) if sotto_soglia and fabbisogno > 0 else 0
        if proposta:
            caricabile_residuo_per_codice[codice] = caricabile_residuo - proposta
        # esito
        if giac >= cap:
            esito = "PIENO" if giac == cap else "OLTRE CAPACITA"
        elif sotto_soglia and fabbisogno > 0:
            if proposta <= 0:
                esito = "SCORTA NEGOZIO"
            elif proposta < fabbisogno:
                esito = "CARICO PARZIALE"
            else:
                esito = "DA CARICARE"
        else:
            esito = "OK"
        d["giacenza_magazzino"] = disponibile
        d["scorta_minima_negozio"] = scorta_minima
        d["giacenza_caricabile"] = caricabile_totale
        d["fabbisogno"] = fabbisogno
        d["proposta"] = proposta
        d["esito"] = esito
        out.append(d)
    return out


async def _sync_product_vending_stock(codice: str, session=None) -> None:
    """Allinea il totale prodotto alla somma delle colonne vending reali."""
    if not codice:
        return
    kwargs = _session_kwargs(session)
    columns = await db.vending.find(
        {"codice": codice},
        {"_id": 0, "giacenza": 1},
        **kwargs,
    ).to_list(500)
    totale = sum(max(0, int(row.get("giacenza", 0) or 0)) for row in columns)
    await db.prodotti.update_one(
        {"codice": codice}, {"$set": {"giacenza_vending": totale}}, **kwargs
    )


@api.put("/vending/{v_id}/giacenza")
async def update_vending_giacenza(v_id: str, body: Dict[str, Any]):
    """Corregge la fotografia fisica della vending senza muovere il magazzino."""
    raw_value = body.get("giacenza")
    try:
        giacenza = int(raw_value)
    except (TypeError, ValueError):
        raise HTTPException(422, "Giacenza non valida")
    if isinstance(raw_value, bool) or isinstance(raw_value, float) and not raw_value.is_integer():
        raise HTTPException(422, "La giacenza deve essere un numero intero")
    if isinstance(raw_value, str) and not re.fullmatch(r"[+-]?\d+", raw_value.strip()):
        raise HTTPException(422, "La giacenza deve essere un numero intero")
    if giacenza < 0:
        raise HTTPException(422, "La giacenza non può essere negativa")

    current = await db.vending.find_one({"id": v_id})
    if not current:
        raise HTTPException(404, "Colonna vending non trovata")
    capacita = int(current.get("capacita_max", 0) or 0)
    if capacita > 0 and giacenza > capacita:
        raise HTTPException(422, f"La giacenza supera la capacità della colonna ({capacita})")

    old_value = int(current.get("giacenza", 0) or 0)
    result = await db.vending.update_one(
        {"id": v_id, "giacenza": current.get("giacenza", 0)},
        {"$set": {
            "giacenza": giacenza,
            "giacenza_aggiornata_il": datetime.now(timezone.utc).isoformat(),
            "giacenza_sorgente": "CORREZIONE_MANUALE",
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(409, "La giacenza è cambiata nel frattempo: aggiorna la pagina e riprova")

    await _sync_product_vending_stock(str(current.get("codice") or ""))
    return {
        "ok": True,
        "id": v_id,
        "colonna": current.get("colonna"),
        "giacenza_precedente": old_value,
        "giacenza": giacenza,
    }


@api.put("/vending/{v_id}")
async def update_vending(v_id: str, body: Dict[str, Any]):
    if "giacenza" in body:
        raise HTTPException(422, "Per correggere la giacenza usa l'endpoint dedicato /giacenza")
    if "codice" in body:
        raise HTTPException(422, "Il codice prodotto della colonna può essere modificato solo tramite import controllato")
    allowed = {k: body[k] for k in ("descrizione", "capacita_max", "soglia_minima") if k in body}
    current = await db.vending.find_one({"id": v_id})
    if not current:
        raise HTTPException(404, "not found")
    raw_capacita = allowed.get("capacita_max", current.get("capacita_max", 5))
    raw_soglia = allowed.get("soglia_minima", current.get("soglia_minima", 2))
    try:
        capacita = int(raw_capacita)
        soglia = int(raw_soglia)
    except (TypeError, ValueError):
        raise HTTPException(422, "Capacità e soglia devono essere numeri interi")
    for value in (raw_capacita, raw_soglia):
        if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
            raise HTTPException(422, "Capacità e soglia devono essere numeri interi")
        if isinstance(value, str) and not re.fullmatch(r"[+-]?\d+", value.strip()):
            raise HTTPException(422, "Capacità e soglia devono essere numeri interi")
    if capacita <= 0:
        raise HTTPException(422, "La capacità deve essere maggiore di zero")
    if soglia < 0 or soglia > capacita:
        raise HTTPException(422, "La soglia deve essere compresa tra zero e la capacità")
    if int(current.get("giacenza", 0) or 0) > capacita:
        raise HTTPException(422, "La capacità non può essere inferiore alla giacenza attuale")
    if "capacita_max" in allowed:
        allowed["capacita_max"] = capacita
    if "soglia_minima" in allowed:
        allowed["soglia_minima"] = soglia
    r = await db.vending.update_one({"id": v_id}, {"$set": allowed})
    if r.matched_count == 0:
        raise HTTPException(404, "not found")
    doc = await db.vending.find_one({"id": v_id}, {"_id": 0})
    return doc


@api.post("/vending/{v_id}/ricarica")
async def ricarica_vending(v_id: str, body: Dict[str, Any]):
    raw_qta = body.get("quantita", 0)
    try:
        qta = int(raw_qta)
    except (TypeError, ValueError):
        raise HTTPException(422, "quantita non valida")
    if isinstance(raw_qta, bool) or isinstance(raw_qta, float) and not raw_qta.is_integer():
        raise HTTPException(422, "La quantità deve essere un numero intero")
    if isinstance(raw_qta, str) and not re.fullmatch(r"[+-]?\d+", raw_qta.strip()):
        raise HTTPException(422, "La quantità deve essere un numero intero")
    if qta <= 0:
        raise HTTPException(422, "quantita deve essere maggiore di zero")
    v, params = await asyncio.gather(db.vending.find_one({"id": v_id}), get_params())
    if not v:
        raise HTTPException(404, "not found")
    scorta_minima = max(0, int(_param(params, "SCORTA_MINIMA_NEGOZIO_VENDING", 2)))
    giacenza = int(v.get("giacenza", 0) or 0)
    capacita = int(v.get("capacita_max", 0) or 0)
    codice = v.get("codice")
    prodotto = await db.prodotti.find_one({"codice": codice}) if codice else None
    disponibile = max(0, int((prodotto or {}).get("giacenza_negozio", 0) or 0))
    fabbisogno = max(0, capacita - giacenza)
    if fabbisogno <= 0:
        raise HTTPException(409, "Colonna già alla capacità massima")
    if qta > fabbisogno:
        raise HTTPException(
            422,
            f"La quantità supera la capacità della colonna: puoi caricare al massimo {fabbisogno} pezzi",
        )
    caricabile = max(0, disponibile - scorta_minima)
    if caricabile <= 0:
        raise HTTPException(409, f"Scorta negozio protetta: devono restare almeno {scorta_minima} pezzi")
    if qta > caricabile:
        raise HTTPException(
            409,
            f"Scorta negozio protetta: richiesti {qta} pezzi, caricabili {caricabile} "
            f"(disponibili {disponibile}, riserva {scorta_minima})",
        )
    qta_caricata = qta
    nuovo = giacenza + qta_caricata
    stock_updated_at = datetime.now(timezone.utc).isoformat()
    vending_result = await db.vending.update_one(
        {"id": v_id, "giacenza": v.get("giacenza", 0)},
        {
            "$inc": {"giacenza": qta},
            "$set": {
                "giacenza_aggiornata_il": stock_updated_at,
                "giacenza_sorgente": "RICARICA",
            },
        },
    )
    if vending_result.matched_count == 0:
        raise HTTPException(409, "La colonna è cambiata nel frattempo: aggiorna la pagina e riprova")
    try:
        product_result = await db.prodotti.update_one(
            {"codice": codice, "giacenza_negozio": {"$gte": qta + scorta_minima}},
            {"$inc": {"giacenza_negozio": -qta, "giacenza_vending": qta}},
        )
        if product_result.matched_count == 0:
            raise RuntimeError("la disponibilità negozio è cambiata")
    except Exception as exc:
        await db.vending.update_one(
            {"id": v_id, "giacenza": nuovo},
            {
                "$inc": {"giacenza": -qta},
                "$set": {
                    "giacenza_aggiornata_il": v.get("giacenza_aggiornata_il"),
                    "giacenza_sorgente": v.get("giacenza_sorgente"),
                },
            },
        )
        raise HTTPException(409, f"Ricarica annullata: {exc}")
    return {
        "ok": True,
        "colonna": v["colonna"],
        "nuova_giacenza": nuovo,
        "quantita_caricata": qta_caricata,
        "giacenza_magazzino_residua": disponibile - qta_caricata,
        "scorta_minima_negozio": scorta_minima,
    }


@api.post("/vending/ricarica-completa")
async def ricarica_vending_completa(body: Dict[str, Any]):
    righe_input = body.get("righe")
    if not isinstance(righe_input, list) or not righe_input:
        raise HTTPException(422, "Nessuna riga da caricare")
    if len(righe_input) > 500:
        raise HTTPException(422, "Sono consentite al massimo 500 righe per caricamento")

    quantita_per_id: Dict[str, int] = {}
    for item in righe_input:
        if not isinstance(item, dict):
            raise HTTPException(422, "Formato riga non valido")
        v_id = str(item.get("id") or "").strip()
        if not v_id:
            raise HTTPException(422, "ID colonna mancante")
        if v_id in quantita_per_id:
            raise HTTPException(422, f"Colonna duplicata nel caricamento: {v_id}")
        raw_quantita = item.get("quantita", 0)
        try:
            quantita = int(raw_quantita)
        except (TypeError, ValueError):
            raise HTTPException(422, f"Quantità non valida per la colonna {v_id}")
        if isinstance(raw_quantita, bool) or isinstance(raw_quantita, float) and not raw_quantita.is_integer():
            raise HTTPException(422, f"La quantità della colonna {v_id} deve essere un numero intero")
        if isinstance(raw_quantita, str) and not re.fullmatch(r"[+-]?\d+", raw_quantita.strip()):
            raise HTTPException(422, f"La quantità della colonna {v_id} deve essere un numero intero")
        if quantita <= 0:
            raise HTTPException(422, f"La quantità della colonna {v_id} deve essere maggiore di zero")
        quantita_per_id[v_id] = quantita

    ids = list(quantita_per_id)
    vending_docs, params = await asyncio.gather(
        db.vending.find({"id": {"$in": ids}}).to_list(500),
        get_params(),
    )
    scorta_minima = max(0, int(_param(params, "SCORTA_MINIMA_NEGOZIO_VENDING", 2)))
    vending_per_id = {str(doc.get("id")): doc for doc in vending_docs}
    mancanti = [v_id for v_id in ids if v_id not in vending_per_id]
    if mancanti:
        raise HTTPException(404, f"Colonne vending non trovate: {', '.join(mancanti)}")

    codici = sorted({doc.get("codice") for doc in vending_docs if doc.get("codice")})
    prodotti = await db.prodotti.find({"codice": {"$in": codici}}).to_list(5000)
    prodotti_per_codice = {str(doc.get("codice")): doc for doc in prodotti}
    preparate = []
    richiesto_per_codice: Dict[str, int] = {}

    for v_id in ids:
        v = vending_per_id[v_id]
        quantita = quantita_per_id[v_id]
        giacenza = int(v.get("giacenza", 0) or 0)
        capacita = int(v.get("capacita_max", 0) or 0)
        fabbisogno = max(0, capacita - giacenza)
        codice = str(v.get("codice") or "")
        colonna = str(v.get("colonna") or v_id)
        if fabbisogno <= 0:
            raise HTTPException(409, f"Colonna {colonna} già alla capacità massima")
        if quantita > fabbisogno:
            raise HTTPException(
                422,
                f"Colonna {colonna}: puoi caricare al massimo {fabbisogno} pezzi",
            )
        if not codice or codice not in prodotti_per_codice:
            raise HTTPException(409, f"Prodotto non trovato per la colonna {colonna}")
        preparate.append({
            "id": v_id,
            "colonna": colonna,
            "codice": codice,
            "giacenza": giacenza,
            "quantita": quantita,
        })
        richiesto_per_codice[codice] = richiesto_per_codice.get(codice, 0) + quantita

    for codice, richiesto in richiesto_per_codice.items():
        disponibile = max(0, int(prodotti_per_codice[codice].get("giacenza_negozio", 0) or 0))
        caricabile = max(0, disponibile - scorta_minima)
        if richiesto > caricabile:
            raise HTTPException(
                409,
                f"Scorta negozio protetta per {codice}: richiesti {richiesto} pezzi, "
                f"caricabili {caricabile} (disponibili {disponibile}, riserva {scorta_minima})",
            )

    vending_applicate = []
    prodotti_applicati = []
    stock_updated_at = datetime.now(timezone.utc).isoformat()
    try:
        for item in preparate:
            original = vending_per_id[item["id"]]
            item["giacenza_aggiornata_il"] = original.get("giacenza_aggiornata_il")
            item["giacenza_sorgente"] = original.get("giacenza_sorgente")
            result = await db.vending.update_one(
                {"id": item["id"], "giacenza": item["giacenza"]},
                {
                    "$inc": {"giacenza": item["quantita"]},
                    "$set": {
                        "giacenza_aggiornata_il": stock_updated_at,
                        "giacenza_sorgente": "RICARICA_COMPLETA",
                    },
                },
            )
            if result.matched_count == 0:
                raise RuntimeError(f"La colonna {item['colonna']} è cambiata durante il caricamento")
            vending_applicate.append(item)

        for codice, quantita in richiesto_per_codice.items():
            result = await db.prodotti.update_one(
                {"codice": codice, "giacenza_negozio": {"$gte": quantita + scorta_minima}},
                {"$inc": {"giacenza_negozio": -quantita, "giacenza_vending": quantita}},
            )
            if result.matched_count == 0:
                raise RuntimeError(f"La disponibilità di {codice} è cambiata durante il caricamento")
            prodotti_applicati.append((codice, quantita))
    except Exception as exc:
        for codice, quantita in reversed(prodotti_applicati):
            await db.prodotti.update_one(
                {"codice": codice},
                {"$inc": {"giacenza_negozio": quantita, "giacenza_vending": -quantita}},
            )
        for item in reversed(vending_applicate):
            await db.vending.update_one(
                {"id": item["id"]},
                {
                    "$inc": {"giacenza": -item["quantita"]},
                    "$set": {
                        "giacenza_aggiornata_il": item.get("giacenza_aggiornata_il"),
                        "giacenza_sorgente": item.get("giacenza_sorgente"),
                    },
                },
            )
        raise HTTPException(409, f"Caricamento annullato: {exc}")

    return {
        "ok": True,
        "colonne_caricate": len(preparate),
        "pezzi_caricati": sum(item["quantita"] for item in preparate),
        "scorta_minima_negozio": scorta_minima,
        "righe": [
            {
                "id": item["id"],
                "colonna": item["colonna"],
                "quantita_caricata": item["quantita"],
                "nuova_giacenza": item["giacenza"] + item["quantita"],
            }
            for item in preparate
        ],
    }


@api.get("/vending/ricarica-pdf")
async def vending_ricarica_pdf():
    """Genera un PDF con le proposte sicure, inclusi i carichi parziali."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    rows = await list_vending()
    da_caricare = [r for r in rows if r.get("proposta", 0) > 0]
    da_caricare.sort(key=lambda r: r["colonna"])
    tot_pezzi = sum(r.get("proposta", 0) for r in da_caricare)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15*mm, rightMargin=15*mm, topMargin=15*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    title_s = ParagraphStyle('t', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=18, textColor=colors.HexColor('#0F172A'), spaceAfter=2)
    sub_s = ParagraphStyle('s', parent=styles['Normal'], fontName='Helvetica', fontSize=9, textColor=colors.HexColor('#64748B'), spaceAfter=12)

    story = [Paragraph("RICARICA VENDING — DA CARICARE", title_s)]
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M")
    story.append(Paragraph(f"Data: <b>{now}</b> &nbsp;·&nbsp; Colonne da caricare: <b>{len(da_caricare)}</b> &nbsp;·&nbsp; Pezzi totali: <b>{tot_pezzi}</b>", sub_s))

    if not da_caricare:
        story.append(Paragraph("Nessuna colonna necessita ricarica.", styles['Normal']))
    else:
        data = [["COLONNA", "CODICE", "ARTICOLO", "VENDING", "NEGOZIO (RIS.)", "CAPACITÀ", "DA CARICARE"]]
        for r in da_caricare:
            data.append([
                r["colonna"],
                r.get("codice", ""),
                (r.get("descrizione") or "")[:45],
                str(r.get("giacenza", 0)),
                f"{r.get('giacenza_magazzino', 0)} ({r.get('scorta_minima_negozio', 0)})",
                str(r.get("capacita_max", 0)),
                str(r.get("proposta", 0)),
            ])
        data.append(["", "", "", "", "", "TOTALE", str(tot_pezzi)])
        tbl = Table(data, colWidths=[20*mm, 25*mm, 60*mm, 20*mm, 22*mm, 20*mm, 23*mm], repeatRows=1)
        tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0F172A')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 8),
            ('ALIGN', (0,0), (-1,0), 'LEFT'),
            ('ALIGN', (3,1), (-1,-1), 'RIGHT'),
            ('FONTNAME', (0,1), (-1,-2), 'Helvetica'),
            ('FONTSIZE', (0,1), (-1,-1), 9),
            ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.white, colors.HexColor('#F8FAFC')]),
            ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#F1F5F9')),
            ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
            ('LEFTPADDING', (0,0), (-1,-1), 6),
            ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ]))
        story.append(tbl)

    doc.build(story)
    buf.seek(0)
    fname = f"ricarica_vending_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(buf, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={fname}"})



# ------------------------- Giacenze reali -------------------------
def _plan_vending_inventory(columns: List[Dict[str, Any]], target: int) -> List[Dict[str, Any]]:
    """Distribuisce una rettifica aggregata sulle colonne fisiche esistenti."""
    ordered = sorted(columns, key=lambda row: str(row.get("colonna") or ""))
    current = sum(max(0, int(row.get("giacenza", 0) or 0)) for row in ordered)
    capacity = sum(max(0, int(row.get("capacita_max", 0) or 0)) for row in ordered)
    if target < 0:
        raise HTTPException(422, "La giacenza vending non può essere negativa")
    if not ordered and target:
        raise HTTPException(422, "Il prodotto non è assegnato ad alcuna colonna vending")
    if target > capacity and target > current:
        raise HTTPException(422, f"La giacenza vending supera la capacità complessiva ({capacity})")

    values = {str(row["id"]): max(0, int(row.get("giacenza", 0) or 0)) for row in ordered}
    delta = target - current
    if delta > 0:
        for row in ordered:
            row_id = str(row["id"])
            room = max(0, int(row.get("capacita_max", 0) or 0) - values[row_id])
            added = min(room, delta)
            values[row_id] += added
            delta -= added
            if delta == 0:
                break
    elif delta < 0:
        remaining = -delta
        for row in reversed(ordered):
            row_id = str(row["id"])
            removed = min(values[row_id], remaining)
            values[row_id] -= removed
            remaining -= removed
            if remaining == 0:
                break

    return [
        {**row, "giacenza_precedente": int(row.get("giacenza", 0) or 0), "giacenza_nuova": values[str(row["id"])]}
        for row in ordered
        if values[str(row["id"])] != int(row.get("giacenza", 0) or 0)
    ]


@api.get("/categorie-prodotti")
async def list_categorie_prodotti():
    """Restituisce le categorie distinte realmente presenti nell'anagrafica prodotti."""
    raw = await db.prodotti.distinct("categoria")
    cats = sorted(set(str(c or "").strip() for c in raw if str(c or "").strip()))
    return cats

@api.get("/giacenze-reali")
async def list_giacenze_reali():
    products, columns = await asyncio.gather(
        db.prodotti.find({}, {"_id": 0}).to_list(5000),
        db.vending.find({}, {"_id": 0}).to_list(5000),
    )
    vending_by_code: Dict[str, List[Dict[str, Any]]] = {}
    for column in columns:
        code = str(column.get("codice") or "")
        if code:
            vending_by_code.setdefault(code, []).append(column)

    rows = []
    for product in products:
        code = str(product.get("codice") or "")
        product_columns = vending_by_code.get(code, [])
        shop = max(0, int(product.get("giacenza_negozio", 0) or 0))
        vending = sum(max(0, int(row.get("giacenza", 0) or 0)) for row in product_columns)
        rows.append({
            "id": product.get("id"),
            "codice": code,
            "descrizione": str(product.get("descrizione") or code),
            "categoria": str(product.get("categoria") or ""),
            "giacenza_negozio": shop,
            "giacenza_vending": vending,
            "giacenza_totale": shop + vending,
            "colonne_vending": len(product_columns),
            "capacita_vending": sum(max(0, int(row.get("capacita_max", 0) or 0)) for row in product_columns),
        })
    return sorted(rows, key=lambda row: (row["descrizione"].casefold(), row["codice"]))


@api.put("/giacenze-reali/{codice}")
async def update_giacenza_reale(codice: str, body: GiacenzaRealeIn, request: Request = None):
    code = _product_code_text(codice)
    for value in (
        body.giacenza_negozio,
        body.giacenza_vending,
        body.attesa_giacenza_negozio,
        body.attesa_giacenza_vending,
    ):
        if value < 0:
            raise HTTPException(422, "Le giacenze devono essere numeri interi non negativi")
    reason = str(body.motivazione or "").strip()
    if len(reason) > 500:
        raise HTTPException(422, "La motivazione non può superare 500 caratteri")
    operator = _request_operator(request)

    async def persist_adjustment(session):
        kwargs = _session_kwargs(session)
        product = await db.prodotti.find_one({"codice": code}, **kwargs)
        if not product:
            raise HTTPException(404, "Prodotto non trovato")
        columns = await db.vending.find({"codice": code}, {"_id": 0}, **kwargs).to_list(500)
        old_shop = int(product.get("giacenza_negozio", 0) or 0)
        old_vending = sum(max(0, int(row.get("giacenza", 0) or 0)) for row in columns)
        if old_shop != body.attesa_giacenza_negozio or old_vending != body.attesa_giacenza_vending:
            raise HTTPException(409, "Le giacenze sono cambiate: aggiorna la pagina prima di salvare")

        vending_plan = _plan_vending_inventory(columns, body.giacenza_vending)
        now = datetime.now(timezone.utc).isoformat()
        product_updated = False
        applied_columns = []
        adjustment_ids = []
        try:
            product_result = await db.prodotti.update_one(
                {"codice": code, "giacenza_negozio": product.get("giacenza_negozio", 0)},
                {"$set": {
                    "giacenza_negozio": body.giacenza_negozio,
                    "giacenza_vending": body.giacenza_vending,
                    "giacenza_reale_aggiornata_il": now,
                }},
                **kwargs,
            )
            if product_result.matched_count != 1:
                raise HTTPException(409, "La giacenza negozio è cambiata durante il salvataggio")
            product_updated = True

            for planned in vending_plan:
                column_result = await db.vending.update_one(
                    {"id": planned["id"], "giacenza": planned["giacenza_precedente"]},
                    {"$set": {
                        "giacenza": planned["giacenza_nuova"],
                        "giacenza_aggiornata_il": now,
                        "giacenza_sorgente": "RETTIFICA_INVENTARIO",
                    }},
                    **kwargs,
                )
                if column_result.matched_count != 1:
                    raise HTTPException(409, f"La colonna {planned.get('colonna')} è cambiata durante il salvataggio")
                applied_columns.append(planned)

            adjustments = []
            if old_shop != body.giacenza_negozio:
                adjustments.append({
                    "id": str(uuid.uuid4()),
                    "codice": code,
                    "descrizione": str(product.get("descrizione") or code),
                    "ubicazione": "NEGOZIO",
                    "quantita_precedente": old_shop,
                    "quantita_nuova": body.giacenza_negozio,
                    "differenza": body.giacenza_negozio - old_shop,
                    "motivazione": reason,
                    "operatore": operator,
                    "created_at": now,
                })
            if old_vending != body.giacenza_vending:
                adjustments.append({
                    "id": str(uuid.uuid4()),
                    "codice": code,
                    "descrizione": str(product.get("descrizione") or code),
                    "ubicazione": "VENDING",
                    "quantita_precedente": old_vending,
                    "quantita_nuova": body.giacenza_vending,
                    "differenza": body.giacenza_vending - old_vending,
                    "motivazione": reason,
                    "operatore": operator,
                    "dettaglio_colonne": [
                        {
                            "id": row["id"],
                            "colonna": row.get("colonna"),
                            "quantita_precedente": row["giacenza_precedente"],
                            "quantita_nuova": row["giacenza_nuova"],
                        }
                        for row in vending_plan
                    ],
                    "created_at": now,
                })
            if adjustments:
                adjustment_ids = [adjustment["id"] for adjustment in adjustments]
                # PyMongo aggiunge `_id` ai dizionari ricevuti: persistiamo
                # copie per mantenere la risposta JSON priva di ObjectId.
                await db.rettifiche_inventario.insert_many(
                    [dict(adjustment) for adjustment in adjustments],
                    **kwargs,
                )

            return {
                "ok": True,
                "codice": code,
                "descrizione": str(product.get("descrizione") or code),
                "giacenza_negozio": body.giacenza_negozio,
                "giacenza_vending": body.giacenza_vending,
                "giacenza_totale": body.giacenza_negozio + body.giacenza_vending,
                "rettifiche": adjustments,
                "updated_at": now,
            }
        except Exception:
            if session is None:
                if adjustment_ids:
                    await db.rettifiche_inventario.delete_many({"id": {"$in": adjustment_ids}})
                for planned in reversed(applied_columns):
                    await db.vending.update_one(
                        {"id": planned["id"], "giacenza": planned["giacenza_nuova"]},
                        {"$set": {
                            "giacenza": planned["giacenza_precedente"],
                            "giacenza_sorgente": planned.get("giacenza_sorgente"),
                            "giacenza_aggiornata_il": planned.get("giacenza_aggiornata_il"),
                        }},
                    )
                if product_updated:
                    await db.prodotti.update_one(
                        {"codice": code, "giacenza_negozio": body.giacenza_negozio},
                        {"$set": {
                            "giacenza_negozio": old_shop,
                            "giacenza_vending": old_vending,
                            "giacenza_reale_aggiornata_il": product.get("giacenza_reale_aggiornata_il"),
                        }},
                    )
            raise

    result = await _run_transactionally(persist_adjustment)
    if result.get("rettifiche"):
        await _record_audit_event(
            "RETTIFICATO", "GIACENZA_REALE", code,
            dopo={"rettifiche": result["rettifiche"], "giacenza_negozio": result["giacenza_negozio"], "giacenza_vending": result["giacenza_vending"]},
            operatore=operator,
        )
    return result


@api.get("/giacenze-reali/rettifiche/storico")
async def storico_rettifiche_inventario(limit: int = 200):
    capped_limit = _cap(limit)
    return await db.rettifiche_inventario.find({}, {"_id": 0}).sort("created_at", -1).limit(capped_limit).to_list(capped_limit)


# ------------------------- Storico ordini -------------------------
@api.get("/ordini")
async def list_ordini(limit: int = 1000):
    capped_limit = _cap(limit)
    docs = await db.storico_ordini.find({}, {"_id": 0}).sort("data", -1).limit(capped_limit).to_list(capped_limit)
    return docs


@api.post("/ordini")
async def add_ordine(o: OrdineIn, request: Request = None):
    if o.quantita <= 0:
        raise HTTPException(422, "La quantità deve essere maggiore di zero")
    smart_venue_applicabile = bool(await db.smart_venue.find_one({"codice": o.codice}, {"_id": 1}))
    oo = OrdineStorico(
        **o.model_dump(),
        smart_venue_applicabile=smart_venue_applicabile,
        caricato_smart_venue=False if smart_venue_applicabile else None,
        operatore=_request_operator(request),
    )
    await db.storico_ordini.insert_one(oo.model_dump())
    # aggiorna giacenza / acquisti. La conferma esterna resta separata.
    if o.codice:
        await db.prodotti.update_one({"codice": o.codice}, {"$inc": {"giacenza_negozio": o.quantita, "acquistati": o.quantita}})
        await _increment_smart_venue_for_receipt(o.codice, o.quantita)
    return oo.model_dump()


@api.post("/ordini/{o_id}/conferma-smart-venue")
async def conferma_carico_smart_venue(o_id: str, request: Request = None):
    """Conferma soltanto l'operazione esterna, senza toccare alcuna giacenza."""
    current = await db.storico_ordini.find_one({"id": o_id}, {"_id": 0})
    if not current:
        raise HTTPException(404, "Movimento di carico non trovato")
    if not current.get("smart_venue_applicabile"):
        raise HTTPException(422, "Il prodotto di questo carico non è gestito in Smart Venue")
    if current.get("caricato_smart_venue") is True:
        return {**current, "ok": True, "gia_confermato": True}

    now = datetime.now(timezone.utc).isoformat()
    result = await db.storico_ordini.update_one(
        {"id": o_id, "caricato_smart_venue": {"$ne": True}},
        {"$set": {
            "caricato_smart_venue": True,
            "caricato_smart_venue_il": now,
            "caricato_smart_venue_da": _request_operator(request),
        }},
    )
    if result.modified_count != 1:
        latest = await db.storico_ordini.find_one({"id": o_id}, {"_id": 0})
        if latest and latest.get("caricato_smart_venue") is True:
            return {**latest, "ok": True, "gia_confermato": True}
        raise HTTPException(409, "Il movimento è cambiato nel frattempo")
    updated = await db.storico_ordini.find_one({"id": o_id}, {"_id": 0})
    return {**updated, "ok": True, "gia_confermato": False}


@api.delete("/ordini/{o_id}")
async def del_ordine(o_id: str):
    await db.storico_ordini.delete_one({"id": o_id})
    return {"ok": True}


class BulkOrdineIn(BaseModel):
    file_sorgente: str = "carico manuale"
    data: Optional[str] = None
    idempotency_key: Optional[str] = None
    righe: List[Dict[str, Any]]  # {codice, descrizione, quantita, prezzo}


async def _increment_smart_venue_for_receipt(codice: str, quantita: int, session=None) -> bool:
    """Mantiene il saldo operativo Smart Venue allineato a un carico merce.

    SMART VENUE e il gestionale partono dalla stessa disponibilita fisica. Il
    carico deve quindi incrementare entrambi; le vendite successive tornano a
    emergere come differenza positiva da confermare. I prodotti non gestiti da
    Smart Venue restano esclusi: questa funzione non crea nuove righe.
    """
    if not codice or quantita <= 0:
        return False
    result = await db.smart_venue.update_one(
        {"codice": codice},
        {
            "$inc": {"smart_venue": quantita},
            "$set": {
                "ultimo_carico_smart_venue": quantita,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        },
        **_session_kwargs(session),
    )
    return bool(result.matched_count)


SMART_VENUE_AUTO_ORDER_REPAIR_ID = "smart-venue-auto-order-2026-10-12-v1"
SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED = {
    "AMMS338": 150,
    "AMMS9": 60,
    "AMMS478": 40,
    "AMMS20155": 40,
    "AMMS20154": 40,
    "AMMS536": 60,
    "AMMS1784": 30,
    "AMMS20659": 60,
    "AMMS2982": 30,
    "AMMS3422": 20,
    "AMMS134": 20,
    "AMMS3906": 10,
    "AMMS3907": 30,
    "AMMS91": 30,
    "AMMS20041": 10,
    "AMMS20151": 20,
    "AMMS21037": 20,
    "AMMS3221": 20,
    "AMMS3458": 10,
    "AMMS951": 10,
    "AMMS81": 10,
    "AMMS177": 10,
    "AMMS3688": 20,
    "AMMS1733": 20,
    "AMMS20153": 20,
    "AMMS21460": 10,
    "AMMS245": 10,
}


def _receipt_quantities_by_code(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    quantities: Dict[str, int] = {}
    for row in rows:
        code = str(row.get("codice") or "").strip()
        quantity = int(row.get("quantita", 0) or 0)
        if code and quantity > 0:
            quantities[code] = quantities.get(code, 0) + quantity
    return quantities


async def repair_smart_venue_auto_order_2026_10_12() -> Dict[str, Any]:
    """Ripara una sola volta il carico Auto-Order che non aggiorno Smart Venue.

    La riparazione si rifiuta di partire se lo storico reale non coincide con
    le 27 righe gia verificate. Ogni riga Smart Venue conserva inoltre un
    marcatore idempotente, cosi un'interruzione a meta non puo raddoppiare le
    quantita al tentativo successivo.
    """
    migration = await db.app_migrations.find_one(
        {"id": SMART_VENUE_AUTO_ORDER_REPAIR_ID},
        {"_id": 0},
    )
    if migration:
        return {"ok": True, "gia_applicata": True, **migration}

    rows = await db.storico_ordini.find(
        {"file_sorgente": "da auto-order (modificato)"},
        {"_id": 0, "data": 1, "codice": 1, "quantita": 1},
    ).to_list(1000)
    rows = [row for row in rows if str(row.get("data") or "").startswith("2026-10-12")]
    if not rows:
        return {"ok": True, "non_applicabile": True}
    actual = _receipt_quantities_by_code(rows)
    if actual != SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED:
        raise RuntimeError(
            "Riparazione annullata: il carico storico del 12/10/2026 non "
            f"coincide con quello verificato (atteso {SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED}, "
            f"trovato {actual})."
        )

    codes = list(SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED)
    smart_rows = await db.smart_venue.find(
        {"codice": {"$in": codes}},
        {"_id": 0, "codice": 1, "riparazioni_applicate": 1},
    ).to_list(1000)
    by_code = {row["codice"]: row for row in smart_rows}
    missing = sorted(set(codes) - set(by_code))
    if missing:
        raise RuntimeError(
            "Riparazione annullata: mancano righe Smart Venue per " + ", ".join(missing)
        )

    backup = await create_backup_snapshot(
        "Prima riparazione Smart Venue carico Auto-Order 12/10/2026",
        "riparazione-smart-venue-carico",
        ["smart_venue", "storico_ordini"],
    )
    now = datetime.now(timezone.utc).isoformat()
    operations = [
        UpdateOne(
            {
                "codice": code,
                "riparazioni_applicate": {"$ne": SMART_VENUE_AUTO_ORDER_REPAIR_ID},
            },
            {
                "$inc": {"smart_venue": quantity},
                "$addToSet": {"riparazioni_applicate": SMART_VENUE_AUTO_ORDER_REPAIR_ID},
                "$set": {"updated_at": now},
            },
        )
        for code, quantity in SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED.items()
    ]
    if operations:
        await db.smart_venue.bulk_write(operations, ordered=True)

    repaired = await db.smart_venue.count_documents({
        "codice": {"$in": codes},
        "riparazioni_applicate": SMART_VENUE_AUTO_ORDER_REPAIR_ID,
    })
    if repaired != len(codes):
        raise RuntimeError(
            f"Riparazione incompleta: marcate {repaired} righe Smart Venue su {len(codes)}."
        )

    migration = {
        "id": SMART_VENUE_AUTO_ORDER_REPAIR_ID,
        "created_at": now,
        "backup_id": backup["id"],
        "righe_riparate": len(codes),
        "pezzi_ripristinati": sum(SMART_VENUE_AUTO_ORDER_REPAIR_EXPECTED.values()),
    }
    await db.app_migrations.insert_one(migration.copy())
    return {"ok": True, "gia_applicata": False, **migration}


@api.post("/ordini/bulk")
async def bulk_carico(body: BulkOrdineIn, request: Request = None):
    """Ricezione merce: registra il ricevuto e incrementa scorte/acquistati.

    Un batch Auto-Order è solo un ordine fornitore; passa da questo endpoint
    esclusivamente quando la merce è fisicamente arrivata. Una chiave
    idempotente rende innocui doppio clic e retry di rete.
    """
    data = body.data or datetime.now(timezone.utc).isoformat()
    idempotency_key = str(body.idempotency_key or uuid.uuid4()).strip()
    if len(idempotency_key) > 200:
        raise HTTPException(422, "Chiave idempotente troppo lunga")

    existing_batch = await db.ricezioni_merce.find_one(
        {"idempotency_key": idempotency_key},
        {"_id": 0},
    )
    if existing_batch:
        if existing_batch.get("status") == "completed" and existing_batch.get("response"):
            return {**existing_batch["response"], "gia_registrato": True}
        raise HTTPException(409, "Questo carico è già in elaborazione: attendi e aggiorna la pagina")

    prepared = []
    errors = []
    for i, r in enumerate(body.righe):
        try:
            codice = str(r.get("codice") or "").strip()
            raw_quantity = r.get("quantita")
            numeric_quantity = float(raw_quantity)
            if not numeric_quantity.is_integer():
                raise ValueError("la quantità deve essere intera")
            qta = int(numeric_quantity)
            if not codice and qta == 0:
                continue
            if not codice:
                raise ValueError("codice mancante")
            if qta <= 0:
                raise ValueError("la quantità deve essere maggiore di zero")
            prezzo = nonnegative_money(r.get("prezzo", 0))
            if prezzo < 0:
                raise ValueError("il prezzo non può essere negativo")
            desc = str(r.get("descrizione") or "")
            prepared.append({
                "index": i,
                "codice": codice,
                "quantita": qta,
                "prezzo": prezzo,
                "descrizione": desc,
            })
        except Exception as e:
            errors.append({"riga": i + 1, "errore": str(e)})
    if errors:
        raise HTTPException(422, {"message": "Correggi le righe non valide", "errori": errors})
    if not prepared:
        raise HTTPException(422, "Nessuna riga valida da caricare")

    batch_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    operator = _request_operator(request)

    async def persist_receipt(session):
        kwargs = _session_kwargs(session)
        await db.ricezioni_merce.insert_one({
            "id": batch_id,
            "idempotency_key": idempotency_key,
            "status": "processing",
            "created_at": created_at,
            "data": data,
            "file_sorgente": body.file_sorgente,
            "operatore": operator,
        }, **kwargs)

        inserted = 0
        creati_prodotti = 0
        movimenti = []
        rollback_actions = []
        try:
            for row in prepared:
                codice = row["codice"]
                qta = row["quantita"]
                prod = await db.prodotti.find_one({"codice": codice}, session=session) if session else await db.prodotti.find_one({"codice": codice})
                smart_row = await db.smart_venue.find_one({"codice": codice}, {"_id": 1}, **kwargs)
                smart_applicable = bool(smart_row)
                movement = OrdineStorico(
                    id=f"{batch_id}:{row['index']}",
                    batch_id=batch_id,
                    data=data,
                    file_sorgente=body.file_sorgente,
                    codice=codice,
                    descrizione=row["descrizione"] or str((prod or {}).get("descrizione") or codice),
                    quantita=qta,
                    prezzo=row["prezzo"],
                    smart_venue_applicabile=smart_applicable,
                    caricato_smart_venue=False if smart_applicable else None,
                    operatore=operator,
                    created_at=created_at,
                ).model_dump()
                await db.storico_ordini.insert_one(dict(movement), **kwargs)

                if prod:
                    update = await db.prodotti.update_one(
                        {"codice": codice},
                        {"$inc": {"giacenza_negozio": qta, "acquistati": qta}},
                        **kwargs,
                    )
                    if update.matched_count != 1:
                        raise RuntimeError(f"prodotto {codice} cambiato durante il carico")
                    rollback_actions.append(("existing_product", codice, qta))
                    if smart_applicable:
                        updated = await _increment_smart_venue_for_receipt(codice, qta, session=session)
                        if not updated:
                            raise RuntimeError(f"riga Smart Venue {codice} cambiata durante il carico")
                        rollback_actions.append(("smart_venue", codice, qta))
                else:
                    new_product = Prodotto(
                        codice=codice,
                        descrizione=row["descrizione"] or codice,
                        categoria=_cat_from_desc(row["descrizione"], codice),
                        prezzo=row["prezzo"],
                        acquistati=qta,
                        giacenza_negozio=qta,
                    ).model_dump()
                    await db.prodotti.insert_one(dict(new_product), **kwargs)
                    rollback_actions.append(("new_product", codice, new_product["id"]))
                    creati_prodotti += 1
                inserted += 1
                movimenti.append(movement)

            response = {
                "caricate": inserted,
                "prodotti_nuovi": creati_prodotti,
                "errori": [],
                "batch_id": batch_id,
                "idempotency_key": idempotency_key,
                "movimenti": movimenti,
                "gia_registrato": False,
            }
            completed = await db.ricezioni_merce.update_one(
                {"id": batch_id, "status": "processing"},
                {"$set": {"status": "completed", "completed_at": datetime.now(timezone.utc).isoformat(), "response": response}},
                **kwargs,
            )
            if completed.matched_count != 1:
                raise RuntimeError("stato del carico cambiato durante il salvataggio")
            return response
        except Exception:
            if session is None:
                # Compensazione per Mongo standalone: annulla in ordine
                # inverso ogni effetto già applicato, mantenendo la chiave
                # occupata per evitare un retry ambiguo.
                for kind, code, value in reversed(rollback_actions):
                    if kind == "smart_venue":
                        await db.smart_venue.update_one(
                            {"codice": code},
                            {"$inc": {"smart_venue": -value}},
                        )
                    elif kind == "existing_product":
                        await db.prodotti.update_one(
                            {"codice": code},
                            {"$inc": {"giacenza_negozio": -value, "acquistati": -value}},
                        )
                    elif kind == "new_product":
                        await db.prodotti.delete_one({"codice": code, "id": value})
                await db.storico_ordini.delete_many({"batch_id": batch_id})
                await db.ricezioni_merce.update_one(
                    {"id": batch_id},
                    {"$set": {"status": "failed", "failed_at": datetime.now(timezone.utc).isoformat()}},
                )
            raise

    try:
        return await _run_transactionally(persist_receipt)
    except DuplicateKeyError:
        existing_batch = await db.ricezioni_merce.find_one(
            {"idempotency_key": idempotency_key},
            {"_id": 0},
        )
        if existing_batch and existing_batch.get("status") == "completed" and existing_batch.get("response"):
            return {**existing_batch["response"], "gia_registrato": True}
        raise HTTPException(409, "Questo carico è già stato inviato")




# ------------------------- Versamenti / Prelievi vending / Cassa -------------------------
async def _collection_money_total(collection) -> float:
    total = Decimal(0)
    async for document in collection.find({}, {"_id": 0, "importo": 1}):
        total += money_decimal(document.get("importo") or 0)
    return money(total)


async def _versamenti_summary(limit: int = 500) -> Dict[str, Any]:
    capped_limit = _cap(limit)
    docs = await db.versamenti.find({}, {"_id": 0}).sort("data", -1).limit(capped_limit).to_list(capped_limit)
    totale = await _collection_money_total(db.versamenti)
    return {"movimenti": docs, "totale": totale}


@api.get("/versamenti")
async def list_versamenti(limit: int = 500):
    return await _versamenti_summary(limit)


@api.get("/audit/eventi")
async def list_audit_events(limit: int = 100, entita: Optional[str] = None, entita_id: Optional[str] = None):
    capped_limit = min(max(int(limit or 100), 1), 500)
    query = {}
    if entita:
        query["entita"] = str(entita).strip()
    if entita_id:
        query["entita_id"] = str(entita_id).strip()
    return await db.audit_events.find(query, {"_id": 0}).sort("created_at", -1).limit(capped_limit).to_list(capped_limit)


@api.post("/versamenti")
async def add_versamento(m: VersamentoIn):
    if m.importo <= 0:
        raise HTTPException(422, "L'importo del versamento deve essere positivo")
    key = str(m.idempotency_key or "").strip() or None
    if key and not 10 <= len(key) <= 160:
        raise HTTPException(422, "idempotency_key non valido")
    if key:
        existing = await db.versamenti.find_one({"idempotency_key": key}, {"_id": 0})
        if existing:
            return existing
    versamento = Versamento(**{k: v for k, v in m.model_dump().items() if k != "idempotency_key"})
    if key:
        versamento_doc = {**versamento.model_dump(), "idempotency_key": key}
    else:
        versamento_doc = versamento.model_dump()
    response_doc = dict(versamento_doc)
    try:
        await db.versamenti.insert_one(versamento_doc)
    except DuplicateKeyError:
        return await db.versamenti.find_one({"idempotency_key": key}, {"_id": 0})
    await _record_audit_event("CREATO", "VERSAMENTO", versamento_doc["id"], dopo=response_doc, idempotency_key=key)
    return response_doc


@api.delete("/versamenti/{m_id}")
async def del_versamento(m_id: str):
    existing = await db.versamenti.find_one({"id": m_id}, {"_id": 0})
    await db.versamenti.delete_one({"id": m_id})
    if existing:
        await _record_audit_event("ELIMINATO", "VERSAMENTO", m_id, prima=existing)
    return {"ok": True}


async def _prelievi_vending_summary(limit: int = 500) -> Dict[str, Any]:
    capped_limit = _cap(limit)
    docs = await db.prelievi_vending.find({}, {"_id": 0}).sort("data", -1).limit(capped_limit).to_list(capped_limit)
    totale = await _collection_money_total(db.prelievi_vending)
    return {"movimenti": docs, "totale": totale}


@api.get("/prelievi-vending")
async def list_prelievi_vending(limit: int = 500):
    return await _prelievi_vending_summary(limit)


@api.post("/prelievi-vending")
async def add_prelievo_vending(m: PrelievoVendingIn):
    if m.importo <= 0:
        raise HTTPException(422, "L'importo del prelievo deve essere positivo")
    key = str(m.idempotency_key or "").strip() or None
    if key and not 10 <= len(key) <= 160:
        raise HTTPException(422, "idempotency_key non valido")
    if key:
        existing = await db.prelievi_vending.find_one({"idempotency_key": key}, {"_id": 0})
        if existing:
            return existing
    await _vending_cash_balance()
    prelievo = PrelievoVending(**{k: v for k, v in m.model_dump().items() if k != "idempotency_key"})
    doc = {**prelievo.model_dump(), **({"idempotency_key": key} if key else {})}
    response_doc = dict(doc)
    await db.prelievi_vending.insert_one(doc)
    try:
        await _adjust_vending_cash_balance(-m.importo)
    except Exception:
        await db.prelievi_vending.delete_one({"id": prelievo.id})
        raise
    await _record_audit_event("CREATO", "PRELIEVO_VENDING", prelievo.id, dopo=response_doc, idempotency_key=key)
    return response_doc


@api.delete("/prelievi-vending/{m_id}")
async def del_prelievo_vending(m_id: str):
    await _vending_cash_balance()
    prelievo = await db.prelievi_vending.find_one({"id": m_id}, {"_id": 0})
    await db.prelievi_vending.delete_one({"id": m_id})
    if prelievo:
        await _adjust_vending_cash_balance(float(prelievo.get("importo") or 0))
        await _record_audit_event("ELIMINATO", "PRELIEVO_VENDING", m_id, prima=prelievo)
    return {"ok": True}


async def _scontrini_vending_summary(limit: int = 500) -> Dict[str, Any]:
    capped_limit = _cap(limit)
    docs = await db.scontrini_vending.find({}, {"_id": 0}).sort("data", -1).limit(capped_limit).to_list(capped_limit)
    totale = await _collection_money_total(db.scontrini_vending)
    return {"movimenti": docs, "totale": totale}


@api.get("/scontrini-vending")
async def list_scontrini_vending(limit: int = 500):
    return await _scontrini_vending_summary(limit)


@api.post("/scontrini-vending")
async def add_scontrino_vending(m: ScontrinoVendingIn):
    if m.importo <= 0:
        raise HTTPException(422, "L'importo dello scontrino deve essere positivo")
    key = str(m.idempotency_key or "").strip() or None
    if key and not 10 <= len(key) <= 160:
        raise HTTPException(422, "idempotency_key non valido")
    if key:
        existing = await db.scontrini_vending.find_one({"idempotency_key": key}, {"_id": 0})
        if existing:
            return existing
    scontrino = ScontrinoVending(**{k: v for k, v in m.model_dump().items() if k != "idempotency_key"})
    doc = {**scontrino.model_dump(), **({"idempotency_key": key} if key else {})}
    response_doc = dict(doc)
    try:
        await db.scontrini_vending.insert_one(doc)
    except DuplicateKeyError:
        return await db.scontrini_vending.find_one({"idempotency_key": key}, {"_id": 0})
    await _record_audit_event("CREATO", "SCONTRINO_VENDING", scontrino.id, dopo=response_doc, idempotency_key=key)
    return response_doc


@api.delete("/scontrini-vending/{m_id}")
async def del_scontrino_vending(m_id: str):
    existing = await db.scontrini_vending.find_one({"id": m_id}, {"_id": 0})
    await db.scontrini_vending.delete_one({"id": m_id})
    if existing:
        await _record_audit_event("ELIMINATO", "SCONTRINO_VENDING", m_id, prima=existing)
    return {"ok": True}


@api.get("/cassa")
async def list_cassa(limit: int = 500):
    capped_limit = _cap(limit)
    docs = await db.cassa.find({}, {"_id": 0}).sort("data", -1).limit(capped_limit).to_list(capped_limit)
    saldo = Decimal(0)
    async for d in db.cassa.find({}, {"_id": 0, "tipo": 1, "importo": 1}):
        if d["tipo"] in ("ENTRATA", "SALDO_INIZIALE"):
            saldo += money_decimal(d["importo"])
        else:
            saldo -= money_decimal(d["importo"])
    return {"movimenti": docs, "saldo": money(saldo)}


@api.post("/cassa")
async def add_cassa(m: MovimentoIn):
    if m.tipo not in {"ENTRATA", "USCITA", "SALDO_INIZIALE"}:
        raise HTTPException(422, "Tipo movimento cassa non valido")
    if m.tipo != "SALDO_INIZIALE" and m.importo <= 0:
        raise HTTPException(422, "Entrate e uscite richiedono un importo positivo")
    key = str(m.idempotency_key or "").strip() or None
    if key and not 10 <= len(key) <= 160:
        raise HTTPException(422, "idempotency_key non valido")
    if key:
        existing = await db.cassa.find_one({"idempotency_key": key}, {"_id": 0})
        if existing:
            return existing
    mm = MovimentoCassa(**{k: v for k, v in m.model_dump().items() if k != "idempotency_key"})
    doc = {**mm.model_dump(), **({"idempotency_key": key} if key else {})}
    response_doc = dict(doc)
    try:
        await db.cassa.insert_one(doc)
    except DuplicateKeyError:
        return await db.cassa.find_one({"idempotency_key": key}, {"_id": 0})
    await _record_audit_event("CREATO", "MOVIMENTO_CASSA", mm.id, dopo=response_doc, idempotency_key=key)
    return response_doc


@api.delete("/cassa/{m_id}")
async def del_cassa(m_id: str):
    existing = await db.cassa.find_one({"id": m_id}, {"_id": 0})
    await db.cassa.delete_one({"id": m_id})
    if existing:
        await _record_audit_event("ELIMINATO", "MOVIMENTO_CASSA", m_id, prima=existing)
    return {"ok": True}


# ------------------------- Parametri -------------------------
@api.get("/parametri")
async def list_parametri():
    docs = await db.parametri.find({}, {"_id": 0}).sort("nome", 1).to_list(200)
    return docs


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


@api.put("/parametri/{nome}")
async def update_parametro(nome: str, body: ParametroIn):
    try:
        value = nonnegative_money(body.valore) if nome == "GIACENZA_INIZIALE_CONTANTI_VENDING" else body.valore
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    bounds = PARAM_BOUNDS.get(nome)
    if bounds is not None:
        lo, hi, msg = bounds
        if not (lo <= value < hi):
            raise HTTPException(422, f"{nome} fuori range consentito ({msg}). Ricevuto: {value}")
    if nome == "SCORTA_MINIMA_NEGOZIO_VENDING" and not float(body.valore).is_integer():
        raise HTTPException(422, "SCORTA_MINIMA_NEGOZIO_VENDING deve essere un numero intero")
    previous = await db.parametri.find_one({"nome": nome}, {"_id": 0, "valore": 1})
    previous_value = float((previous or {}).get("valore") or 0)
    r = await db.parametri.update_one({"nome": nome}, {"$set": {"valore": value}})
    if r.matched_count == 0:
        await db.parametri.insert_one(Parametro(nome=nome, valore=value).model_dump())
    if nome == "GIACENZA_INIZIALE_CONTANTI_VENDING":
        delta = money_sum([value, -previous_value])
        if delta:
            await _adjust_vending_cash_balance(delta)
    doc = await db.parametri.find_one({"nome": nome}, {"_id": 0})
    if previous_value != float(value):
        await _record_audit_event(
            "MODIFICATO", "PARAMETRO", nome,
            prima={"valore": previous_value}, dopo={"valore": float(value)},
        )
    return doc


# ------------------------- Auto-Order -------------------------
@api.get("/auto-order")
async def auto_order():
    params = await get_params()
    finestra_breve = max(3, min(30, int(_param(params, "AUTO_ORDER_FINESTRA_BREVE_GG", 10))))
    finestra_lunga = max(finestra_breve, min(90, int(_param(params, "AUTO_ORDER_FINESTRA_LUNGA_GG", 30))))
    peso_breve = max(0.0, min(1.0, float(_param(params, "AUTO_ORDER_PESO_BREVE", 0.70))))
    peso_lungo = 1.0 - peso_breve
    min_venduto_breve = max(0, int(_param(params, "AUTO_ORDER_MIN_VENDUTO_BREVE", 2)))
    min_venduto_lungo = max(0, int(_param(params, "AUTO_ORDER_MIN_VENDUTO_LUNGO", 4)))

    # La finestra segue l'ultima giornata realmente disponibile nei dati. Un file
    # importato qualche giorno dopo la chiusura contabile non deve produrre zero
    # riordini solo perché il calendario del computer è più avanti.
    latest_app, latest_imported = await asyncio.gather(
        db.vendite.find_one({}, {"_id": 0, "data": 1}, sort=[("data", -1)]),
        db.db_storico_vend.find_one({}, {"_id": 0, "data": 1}, sort=[("data", -1)]),
    )

    def parsed_date(document: Optional[Dict[str, Any]]) -> Optional[datetime]:
        raw = (document or {}).get("data")
        if not raw:
            return None
        if isinstance(raw, datetime):
            return raw
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None

    available_dates = [d for d in (parsed_date(latest_app), parsed_date(latest_imported)) if d]
    reference_date = max(available_dates, key=lambda d: d.date()) if available_dates else datetime.now(timezone.utc)
    reference_day = reference_date.date()

    def sales_pipeline(days: int) -> List[Dict[str, Any]]:
        first_day = reference_day - timedelta(days=days - 1)
        since = datetime.combine(first_day, datetime.min.time()).isoformat()
        return [
            {"$match": {"data": {"$gte": since}}},
            {"$group": {"_id": "$codice", "tot": {"$sum": "$quantita"}}},
        ]

    pipeline_breve = sales_pipeline(finestra_breve)
    pipeline_lunga = sales_pipeline(finestra_lunga)
    storico_pipeline = [
        {
            "$group": {
                "_id": "$codice",
                "n_ord": {"$sum": 1},
                "tot_quantita": {"$sum": "$quantita"},
            }
        }
    ]

    # Le letture sono indipendenti e aggregate: nessuna query per-prodotto.
    (
        prodotti,
        vendite_app_breve,
        vendite_app_lunga,
        vendite_importate_breve,
        vendite_importate_lunga,
        storico_ordini,
        vending_totals,
    ) = await asyncio.gather(
        db.prodotti.find({}, {"_id": 0}).to_list(5000),
        db.vendite.aggregate(pipeline_breve).to_list(5000),
        db.vendite.aggregate(pipeline_lunga).to_list(5000),
        db.db_storico_vend.aggregate(pipeline_breve).to_list(5000),
        db.db_storico_vend.aggregate(pipeline_lunga).to_list(5000),
        db.storico_ordini.aggregate(storico_pipeline).to_list(5000),
        _vending_stock_by_code(),
    )
    _apply_vending_stock(prodotti, vending_totals)

    def totals(rows: List[Dict[str, Any]]) -> Dict[str, int]:
        return {r["_id"]: int(r.get("tot", 0) or 0) for r in rows if r.get("_id") is not None}

    vendite_app_breve_per_codice = totals(vendite_app_breve)
    vendite_app_lunga_per_codice = totals(vendite_app_lunga)
    vendite_importate_breve_per_codice = totals(vendite_importate_breve)
    vendite_importate_lunga_per_codice = totals(vendite_importate_lunga)
    storico_per_codice = {
        r["_id"]: r
        for r in storico_ordini
        if r.get("_id") is not None
    }

    fatt = max(1.0, float(_param(params, "AUTO_ORDER_FATTORE_SICUREZZA", 1.15)))
    gg_min = max(1.0, float(_param(params, "GIORNI_COPERTURA_MIN", 7)))
    gg_target = max(gg_min, float(_param(params, "GIORNI_COPERTURA_TARGET", 14)))

    proposte = []
    esclusi = []
    for p in prodotti:
        codice = p["codice"]
        giac_negozio = int(p.get("giacenza_negozio", 0) or 0)
        giac_vending = int(p.get("giacenza_vending", 0) or 0)
        venduti_vending = int(p.get("venduti_vending", 0) or 0)
        giac_tot = giac_negozio + giac_vending
        # giacenza_negozio è già stock fisico libero: l'import Excel normalizza
        # RIMANENZE una sola volta, quindi Auto-Order non sottrae più la vending.
        magazzino_reale_lordo = giac_negozio
        magazzino_reale = max(0, magazzino_reale_lordo)
        anomalia = giac_negozio < 0

        # DB_STORICO_VEND è la fonte giornaliera importata; le vendite registrate
        # nell'app sono il fallback. Non si sommano per evitare doppi conteggi.
        if codice in vendite_importate_lunga_per_codice:
            venduto_breve = vendite_importate_breve_per_codice.get(codice, 0)
            venduto_lungo = vendite_importate_lunga_per_codice.get(codice, 0)
            fonte_domanda = "DB_STORICO_VEND"
        else:
            venduto_breve = vendite_app_breve_per_codice.get(codice, 0)
            venduto_lungo = vendite_app_lunga_per_codice.get(codice, 0)
            fonte_domanda = "VENDITE_APP"

        # Storico già aggregato per tutti i prodotti in un'unica query.
        storico = storico_per_codice.get(codice, {})
        n_ord = int(storico.get("n_ord", 0) or 0)
        tot_quantita = storico.get("tot_quantita", 0) or 0
        media_ord = round(tot_quantita / n_ord, 2) if n_ord else 0

        domanda_breve_gg = venduto_breve / float(finestra_breve)
        domanda_lunga_gg = venduto_lungo / float(finestra_lunga)
        domanda_gg = (domanda_breve_gg * peso_breve) + (domanda_lunga_gg * peso_lungo)
        lotto = lotto_for(p.get("categoria", "ACCESSORI"), params)
        copertura_gg = magazzino_reale / domanda_gg if domanda_gg > 0 else None
        movimento_sufficiente = venduto_breve >= min_venduto_breve or venduto_lungo >= min_venduto_lungo
        target_scorta = math.ceil(domanda_gg * gg_target * fatt) if domanda_gg > 0 else 0
        fabbisogno_grezzo = max(0, target_scorta - magazzino_reale)

        stato = "NESSUN ORDINE"
        motivo = "NESSUNA VENDITA RECENTE"
        qta = 0
        if anomalia:
            stato = "ANOMALIA"
            motivo = f"STOCK NEGOZIO FISICO NEGATIVO ({giac_negozio}) · VERIFICARE IMPORT/MOVIMENTI"
            qta = 0
        elif not movimento_sufficiente:
            if magazzino_reale == 0 and venduto_lungo > 0:
                stato = "CONTROLLO MANUALE"
                motivo = f"SCORTA ZERO MA MOVIMENTO BASSO ({venduto_breve}/{venduto_lungo} in {finestra_breve}/{finestra_lunga}gg)"
            elif venduto_lungo > 0:
                motivo = f"MOVIMENTO BASSO ({venduto_breve}/{venduto_lungo} in {finestra_breve}/{finestra_lunga}gg)"
        elif copertura_gg is not None and copertura_gg < gg_min:
            stato = "ORDINA ORA"
            motivo = f"COPERTURA {copertura_gg:.2f}gg < {int(gg_min)}gg"
            qta = max(lotto, fabbisogno_grezzo)
            qta = ((qta + lotto - 1) // lotto) * lotto
        elif copertura_gg is not None and copertura_gg < gg_target:
            stato = "MONITORA"
            motivo = f"COPERTURA {copertura_gg:.2f}gg TRA {int(gg_min)} E {int(gg_target)}gg"
        elif copertura_gg is not None:
            motivo = f"COPERTURA SUFFICIENTE ({copertura_gg:.2f}gg)"

        row = {
            "codice": codice,
            "descrizione": p.get("descrizione", ""),
            "categoria": p.get("categoria", ""),
            "stato": stato,
            "anomalia": anomalia,
            "qta_da_ordinare": qta,
            "giacenza": giac_tot,
            "giacenza_negozio": giac_negozio,
            "venduti_vending": venduti_vending,
            "giacenza_vending": giac_vending,
            "magazzino_reale_lordo": magazzino_reale_lordo,
            "magazzino_reale": magazzino_reale,
            "venduto_periodo": venduto_breve,
            "finestra_domanda_gg": finestra_breve,
            "venduto_breve": venduto_breve,
            "venduto_lungo": venduto_lungo,
            "venduto_10gg": venduto_breve,
            "venduto_30gg": venduto_lungo,
            "fonte_domanda": fonte_domanda,
            "domanda_gg_10": round(domanda_breve_gg, 3),
            "domanda_gg_30": round(domanda_lunga_gg, 3),
            "domanda_gg": round(domanda_gg, 3),
            "copertura_gg": round(copertura_gg, 2) if copertura_gg is not None else None,
            "target_scorta": target_scorta,
            "fabbisogno_grezzo": fabbisogno_grezzo,
            "media_ordini_storico": media_ord,
            "n_ordini_storici": n_ord,
            "lotto_ordine": lotto,
            "motivo": motivo,
            "prezzo": p.get("prezzo", 0),
            "totale": money_product(p.get("prezzo") or 0, qta),
        }
        if stato == "ORDINA ORA" and not anomalia and qta > 0:
            proposte.append(row)
        else:
            esclusi.append(row)

    proposte.sort(key=lambda x: (-x["totale"], x["codice"]))
    ordine_stati = {"ANOMALIA": 0, "CONTROLLO MANUALE": 1, "MONITORA": 2, "NESSUN ORDINE": 3}
    esclusi.sort(key=lambda x: (ordine_stati.get(x["stato"], 9), x["descrizione"], x["codice"]))
    tot = money_sum(x["totale"] for x in proposte)
    riepilogo_stati = {"ORDINA ORA": len(proposte)}
    for row in esclusi:
        riepilogo_stati[row["stato"]] = riepilogo_stati.get(row["stato"], 0) + 1
    n_anomalie_stock = sum(1 for row in [*proposte, *esclusi] if row["anomalia"])
    response = {
        "righe": proposte,
        "esclusi": esclusi,
        "totale": tot,
        "n_righe": len(proposte),
        "n_anomalie_stock": n_anomalie_stock,
        "riepilogo_stati": riepilogo_stati,
        "parametri": params,
        "finestra_domanda_gg": finestra_breve,
        "finestra_breve_gg": finestra_breve,
        "finestra_lunga_gg": finestra_lunga,
        "peso_breve": peso_breve,
        "peso_lungo": peso_lungo,
        "fattore_sicurezza": fatt,
        "data_riferimento_domanda": reference_day.isoformat(),
        "giorni_ritardo_dati": max(0, (datetime.now(timezone.utc).date() - reference_day).days),
    }
    response["snapshot_key"] = _auto_order_snapshot_key(response)
    return response


def _auto_order_selected_rows(
    ao: Dict[str, Any], requested: Optional[List[AutoOrderRigaIn]] = None
) -> List[Dict[str, Any]]:
    """Applica una selezione utente usando descrizioni e prezzi calcolati dal server."""
    if requested is None:
        return [
            dict(row) for row in ao["righe"]
            if not row.get("anomalia")
            and int(row.get("qta_da_ordinare") or 0) > 0
        ]
    if not requested:
        raise HTTPException(422, "Aggiungi almeno un articolo all'ordine")

    available = {
        row["codice"]: row
        for row in [*ao.get("righe", []), *ao.get("esclusi", [])]
        if row.get("codice") and not row.get("anomalia")
    }
    seen = set()
    selected = []
    for item in requested:
        codice = item.codice.strip()
        if codice in seen:
            raise HTTPException(422, f"Articolo duplicato nella selezione: {codice}")
        row = available.get(codice)
        if not row:
            raise HTTPException(422, f"Articolo non ordinabile: {codice}")
        seen.add(codice)
        quantity = int(item.quantita)
        selected.append({
            **row,
            "stato": "ORDINA ORA",
            "qta_da_ordinare": quantity,
            "totale": money_product(row.get("prezzo") or 0, quantity),
        })
    return selected


def _auto_order_rows_from_query(ao: Dict[str, Any], selection: Optional[str]) -> List[Dict[str, Any]]:
    if not selection:
        return _auto_order_selected_rows(ao)
    try:
        raw = json.loads(selection)
        requested = [AutoOrderRigaIn.model_validate(item) for item in raw]
    except Exception as exc:
        raise HTTPException(422, "Selezione Auto-Order non valida") from exc
    return _auto_order_selected_rows(ao, requested)


@api.post("/auto-order/conferma")
async def conferma_auto_order(
    body: Optional[AutoOrderConfermaIn] = None,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Crea un batch ordine fornitore idempotente senza caricare magazzino.

    La ricezione fisica della merce resta su POST /api/ordini/bulk, che registra
    storico_ordini e incrementa giacenza/acquistati quando il carico arriva.
    """
    ao = await auto_order()
    requested_rows = body.righe if body else None
    righe = _auto_order_selected_rows(ao, requested_rows)
    selection_key = hashlib.sha256(json.dumps(
        [{"codice": r["codice"], "quantita": r["qta_da_ordinare"]} for r in righe],
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")).hexdigest()[:24]
    header_key = idempotency_key if isinstance(idempotency_key, str) else None
    default_key = f"{ao['snapshot_key']}:selezione:{selection_key}" if requested_rows is not None else ao["snapshot_key"]
    batch_key = (header_key or (body.idempotency_key if body else None) or (body.batch_key if body else None) or default_key).strip()
    if not batch_key:
        batch_key = ao["snapshot_key"]
    existing = await db.ordini_fornitore.find_one({"batch_key": batch_key}, {"_id": 0})
    if existing:
        return {**existing, "duplicate": True}

    oggi = datetime.now(timezone.utc).isoformat()
    batch = {
        "id": str(uuid.uuid4()),
        "batch_key": batch_key,
        "snapshot_key": ao["snapshot_key"],
        "data": oggi,
        "stato": "CREATO",
        "origine": "AUTO-ORDER",
        "ordinati": len(righe),
        "totale": money_sum(r.get("totale", 0) or 0 for r in righe),
        "data_riferimento_domanda": ao.get("data_riferimento_domanda"),
    }
    inserted_ids = []
    try:
        try:
            # Motor aggiunge ``_id`` al dizionario ricevuto. Inseriamo una copia
            # per mantenere serializzabile l'oggetto restituito dalla API.
            await db.ordini_fornitore.insert_one(batch.copy())
        except DuplicateKeyError:
            existing = await db.ordini_fornitore.find_one({"batch_key": batch_key}, {"_id": 0})
            if existing:
                return {**existing, "duplicate": True}
            raise
        for index, r in enumerate(righe, start=1):
            doc = {
                "id": str(uuid.uuid4()),
                "batch_key": batch_key,
                "riga": index,
                "codice": r["codice"],
                "descrizione": r.get("descrizione", ""),
                "categoria": r.get("categoria", ""),
                "quantita": int(r["qta_da_ordinare"]),
                "prezzo": money(r.get("prezzo") or 0),
                "totale": money(r.get("totale") or 0),
                "motivo": r.get("motivo", ""),
            }
            await db.ordini_fornitore_righe.insert_one(doc)
            inserted_ids.append(doc["id"])
    except Exception as exc:
        try:
            if inserted_ids:
                await db.ordini_fornitore_righe.delete_many({"id": {"$in": inserted_ids}})
            await db.ordini_fornitore.delete_one({"batch_key": batch_key})
        except Exception:
            logging.exception("auto-order rollback failed for batch %s", batch_key)
        raise HTTPException(500, f"Creazione batch Auto-Order annullata: {exc}")

    return {"ok": True, **batch, "duplicate": False}


# ------------------------- Auto-Order export -------------------------
@api.get("/auto-order/pdf")
async def auto_order_pdf(
    fornitore: Optional[str] = "Fornitore",
    categoria: Optional[str] = None,
    selezione: Optional[str] = None,
):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle
    from xml.sax.saxutils import escape as xml_escape

    # sanifica input utente per la markup di reportlab
    fornitore_safe = xml_escape((fornitore or "Fornitore")[:120])
    categoria_filtro = (categoria or "").strip()[:120]
    categoria_safe = xml_escape(categoria_filtro)

    ao = await auto_order()
    righe = _auto_order_rows_from_query(ao, selezione)
    if categoria_filtro:
        righe = [r for r in righe if r.get("categoria") == categoria_filtro]
    totale = money_sum(r.get("totale", 0) or 0 for r in righe)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15*mm, rightMargin=15*mm, topMargin=15*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    title_s = ParagraphStyle('t', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=18, textColor=colors.HexColor('#0F172A'), spaceAfter=2)
    sub_s = ParagraphStyle('s', parent=styles['Normal'], fontName='Helvetica', fontSize=9, textColor=colors.HexColor('#64748B'), spaceAfter=12)
    cell_s = ParagraphStyle('cell', parent=styles['Normal'], fontName='Helvetica', fontSize=7, leading=8, textColor=colors.HexColor('#0F172A'))

    story = []
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    story.append(Paragraph("ORDINE FORNITORE - GOD SERVICES", title_s))
    filtro_pdf = f" &nbsp;·&nbsp; Selezione: <b>{categoria_safe}</b>" if categoria_filtro else ""
    story.append(Paragraph(f"Destinatario: <b>{fornitore_safe}</b> &nbsp;·&nbsp; Data: <b>{now}</b>{filtro_pdf} &nbsp;·&nbsp; Righe: <b>{len(righe)}</b> &nbsp;·&nbsp; Totale: <b>€ {totale:.2f}</b>", sub_s))

    # Table
    header = ["CODICE", "ARTICOLO", "QTA", "TOTALE"]
    data = [header]
    for r in righe:
        data.append([
            r["codice"],
            Paragraph(xml_escape(r["descrizione"] or ""), cell_s),
            str(r["qta_da_ordinare"]),
            f"€ {r['totale']:.2f}",
        ])
    data.append(["", "", "TOTALE", f"€ {totale:.2f}"])

    col_widths = [25*mm, 105*mm, 15*mm, 35*mm]
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0F172A')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 8),
        ('ALIGN', (0,0), (-1,0), 'LEFT'),
        ('ALIGN', (2,1), (3,-1), 'RIGHT'),
        ('FONTNAME', (0,1), (-1,-2), 'Helvetica'),
        ('FONTSIZE', (0,1), (-1,-1), 8),
        ('TEXTCOLOR', (0,1), (-1,-1), colors.HexColor('#0F172A')),
        ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.white, colors.HexColor('#F8FAFC')]),
        ('LINEBELOW', (0,0), (-1,0), 0.8, colors.HexColor('#0F172A')),
        ('LINEBELOW', (0,-2), (-1,-2), 0.5, colors.HexColor('#E2E8F0')),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#F1F5F9')),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,-1), (-1,-1), 9),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(tbl)
    footer = Paragraph(
        "Calcolo Auto-Order: domanda ponderata {peso_breve:.0f}% ultimi {breve}gg + {peso_lungo:.0f}% ultimi {lungo}gg fino al {riferimento}; giacenza negozio = stock fisico libero gia normalizzato dall'import; ordine sotto {copertura}gg verso target {target}gg con sicurezza x{fattore:.2f}. Il PDF contiene solo ORDINA ORA.".format(
            peso_breve=float(ao.get("peso_breve", 0.70)) * 100,
            peso_lungo=float(ao.get("peso_lungo", 0.30)) * 100,
            breve=int(ao.get("finestra_breve_gg", 10)),
            lungo=int(ao.get("finestra_lunga_gg", 30)),
            riferimento=datetime.fromisoformat(ao["data_riferimento_domanda"]).strftime("%d/%m/%Y"),
            copertura=int(ao["parametri"].get("GIORNI_COPERTURA_MIN", 7)),
            target=int(ao["parametri"].get("GIORNI_COPERTURA_TARGET", 14)),
            fattore=float(ao.get("fattore_sicurezza", 1.15)),
        ),
        sub_s,
    )

    def draw_footer(canvas, document):
        width, height = footer.wrap(document.width, 14*mm)
        footer.drawOn(canvas, document.leftMargin, 4*mm)

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    buf.seek(0)
    categoria_slug = re.sub(r"[^a-z0-9]+", "_", categoria_filtro.lower()).strip("_")
    suffisso = f"_{categoria_slug}" if categoria_slug else ""
    fname = f"ordine{suffisso}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={fname}",
            "X-Auto-Order-Category": categoria_filtro or "TUTTE",
            "X-Auto-Order-Rows": str(len(righe)),
        },
    )


@api.get("/auto-order/excel")
async def auto_order_excel(
    fornitore: Optional[str] = "Fornitore",
    categoria: Optional[str] = None,
    selezione: Optional[str] = None,
):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    fornitore_testo = (fornitore or "Fornitore").strip()[:120] or "Fornitore"
    categoria_filtro = (categoria or "").strip()[:120]

    ao = await auto_order()
    righe = _auto_order_rows_from_query(ao, selezione)
    if categoria_filtro:
        righe = [r for r in righe if r.get("categoria") == categoria_filtro]
    totale = money_sum(r.get("totale", 0) or 0 for r in righe)

    def excel_text(value: Any) -> str:
        """Mantiene i campi testuali come testo anche con prefissi da formula."""
        text = str(value or "")
        return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text

    wb = Workbook()
    ws = wb.active
    ws.title = "Ordine fornitore"
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A5"

    dark_fill = PatternFill("solid", fgColor="0F172A")
    light_fill = PatternFill("solid", fgColor="F1F5F9")
    white_bold = Font(color="FFFFFF", bold=True)

    ws.merge_cells("A1:D1")
    ws["A1"] = "ORDINE FORNITORE - GOD SERVICES"
    ws["A1"].font = Font(size=16, bold=True, color="0F172A")
    ws["A1"].alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 26

    data_ordine = datetime.now(timezone.utc).date()
    ws["A2"] = "Destinatario"
    ws["B2"] = excel_text(fornitore_testo)
    ws["C2"] = "Data"
    ws["D2"] = data_ordine
    ws["D2"].number_format = "dd/mm/yyyy"
    ws["A3"] = "Selezione"
    ws["B3"] = excel_text(categoria_filtro or "Tutte le categorie")
    ws["C3"] = "Righe"
    ws["D3"] = len(righe)
    for cell in (ws["A2"], ws["C2"], ws["A3"], ws["C3"]):
        cell.font = Font(bold=True, color="475569")

    header_row = 4
    headers = ["CODICE", "ARTICOLO", "QTA", "TOTALE"]
    for column, label in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=column, value=label)
        cell.fill = dark_fill
        cell.font = white_bold
        cell.alignment = Alignment(horizontal="right" if column >= 3 else "left")

    first_data_row = header_row + 1
    for row_index, row in enumerate(righe, start=first_data_row):
        ws.cell(row=row_index, column=1, value=excel_text(row.get("codice")))
        ws.cell(row=row_index, column=2, value=excel_text(row.get("descrizione")))
        ws.cell(row=row_index, column=3, value=int(row.get("qta_da_ordinare") or 0))
        total_cell = ws.cell(row=row_index, column=4, value=float(row.get("totale") or 0))
        total_cell.number_format = '€ #,##0.00'
        ws.cell(row=row_index, column=3).alignment = Alignment(horizontal="right")
        total_cell.alignment = Alignment(horizontal="right")

    total_row = first_data_row + len(righe)
    ws.cell(row=total_row, column=3, value="TOTALE")
    ws.cell(row=total_row, column=4, value=totale)
    for column in range(1, 5):
        cell = ws.cell(row=total_row, column=column)
        cell.fill = light_fill
        cell.font = Font(bold=True, color="0F172A")
    ws.cell(row=total_row, column=4).number_format = '€ #,##0.00'
    ws.cell(row=total_row, column=3).alignment = Alignment(horizontal="right")
    ws.cell(row=total_row, column=4).alignment = Alignment(horizontal="right")

    last_filter_row = max(header_row, total_row - 1)
    ws.auto_filter.ref = f"A{header_row}:D{last_filter_row}"
    widths = {1: 20, 2: 55, 3: 14, 4: 18}
    for column, width in widths.items():
        ws.column_dimensions[get_column_letter(column)].width = width

    wb.properties.title = "Ordine fornitore"
    wb.properties.subject = categoria_filtro or "Tutte le categorie"
    wb.properties.creator = "God Services Gestionale"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    categoria_slug = re.sub(r"[^a-z0-9]+", "_", categoria_filtro.lower()).strip("_")
    suffisso = f"_{categoria_slug}" if categoria_slug else ""
    fname = f"ordine{suffisso}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename={fname}",
            "X-Auto-Order-Category": categoria_filtro or "TUTTE",
            "X-Auto-Order-Rows": str(len(righe)),
        },
    )


# ------------------------- Prodotti più venduti (per POS) -------------------------
@api.get("/prodotti/top")
async def prodotti_top(limit: int = 40):
    prods = await db.prodotti.find({}, {"_id": 0}).to_list(5000)
    for p in prods:
        p["venduti_totale"] = (p.get("venduti_negozio") or 0) + (p.get("venduti_vending") or 0)
    prods.sort(key=lambda x: -x["venduti_totale"])
    return prods[:_cap(limit)]



# ------------------------- Pivot magazzino -------------------------
@api.get("/pivot")
async def pivot():
    prods, params, vending_totals = await asyncio.gather(
        db.prodotti.find({"presente_ultimo_import": {"$ne": False}}, {"_id": 0}).to_list(5000),
        get_params(),
        _vending_stock_by_code(),
    )
    _apply_vending_stock(prods, vending_totals)
    divisor = params.get("VENDITE_GIORNALIERE_MESE", 6.5)
    aggio = params.get("AGGIO_PCT", 0.10)
    cost_factor = max(Decimal(0), Decimal(1) - Decimal(str(aggio)))
    rows = []
    tot_acq = tot_vend = tot_giac = pezzi_mag = 0
    fermi = negativi = da_riord = lenti = 0
    for p in prods:
        v_neg = p.get("venduti_negozio", 0) or 0
        v_ven = p.get("venduti_vending", 0) or 0
        v_tot = v_neg + v_ven
        g_neg = p.get("giacenza_negozio", 0) or 0
        g_ven = p.get("giacenza_vending", 0) or 0
        g_tot = g_neg + g_ven
        prz = money_decimal(p.get("prezzo", 0) or 0)
        prz_costo = prz * cost_factor
        acq = p.get("acquistati", 0) or 0
        # Round each line, not the discounted unit cost; totals equal displayed lines.
        ta = money_decimal(acq * prz_costo)
        tv = money_decimal(v_tot * prz)
        tg = money_decimal(g_tot * prz_costo)
        vend_mese = v_tot / max(1, divisor) if divisor else 0
        mesi_smalt = round(g_tot / vend_mese, 2) if vend_mese > 0 else 0
        stato = "OK"
        azione = "OK"
        if g_tot <= 0:
            stato = "ESAURITO"
            azione = "RIORDINO"
        elif v_tot == 0 and acq > 0:
            stato = "FERMO"
            azione = "REVISIONE"
            fermi += 1
        elif mesi_smalt > 6:
            stato = "LENTO"
            azione = "REVISIONE"
            lenti += 1
        if g_tot < 0:
            negativi += 1
        if stato == "ESAURITO":
            da_riord += 1
        tot_acq += ta
        tot_vend += tv
        tot_giac += tg
        pezzi_mag += g_tot
        rows.append({
            "codice": p["codice"], "descrizione": p.get("descrizione", ""),
            "categoria": p.get("categoria", ""),
            "acq": acq, "vend_negozio": v_neg, "vend_vending": v_ven, "vend_totale": v_tot,
            "giac_negozio": g_neg, "giac_vending": g_ven, "giac_totale": g_tot,
            "prezzo": money(prz), "tot_acquistato": money(ta), "tot_venduto": money(tv), "tot_giacenza": money(tg),
            "vendite_mese": round(vend_mese, 2), "mesi_smaltimento": mesi_smalt,
            "stato": stato, "azione": azione,
        })
    rows.sort(key=lambda x: -x["tot_giacenza"])
    return {
        "kpi": {
            "valore_acquistato": money(tot_acq),
            "valore_venduto": money(tot_vend),
            "valore_giacenza": money(tot_giac),
            "margine_lordo": money(tot_vend - tot_acq),
            "aggio_pct": round(aggio, 4),
            "pezzi_magazzino": pezzi_mag,
            "prodotti_totali": len(rows),
            "prodotti_fermi": fermi,
            "giacenza_negativa": negativi,
            "da_riordinare": da_riord,
            "lenti_oltre_6_mesi": lenti,
        },
        "righe": rows,
    }


# ------------------------- Dashboard KPIs -------------------------
def _parse_sale_date(value: Any):
    """Normalizza le date vendite ISO e italiane usate dalle due sorgenti."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for date_format in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw[:10], date_format).date()
        except ValueError:
            continue
    return None


def _latest_sale_date(documents: List[Dict[str, Any]]):
    parsed_dates = [_parse_sale_date(document.get("data")) for document in documents]
    return max((parsed for parsed in parsed_dates if parsed), default=None)


def _parse_utc_datetime(value: Any):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_sale_after_excel_import(
    document: Dict[str, Any], latest_imported_sale: Any, latest_import_created_at: Any
) -> bool:
    """Include le registrazioni create dopo l'Excel, anche nello stesso giorno."""
    import_cutoff = _parse_utc_datetime(latest_import_created_at)
    created_at = _parse_utc_datetime(document.get("created_at"))
    if import_cutoff and created_at:
        return created_at > import_cutoff
    sale_cutoff = _parse_sale_date(latest_imported_sale)
    sale_day = _parse_sale_date(document.get("data"))
    return not sale_cutoff or bool(sale_day and sale_day > sale_cutoff)


def _is_cash_payment(payment_method: Any) -> bool:
    normalized = unicodedata.normalize("NFKD", str(payment_method or "")).encode("ascii", "ignore").decode().upper()
    return not normalized or "CONTANT" in normalized


def _vending_payment_bucket(payment_method: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(payment_method or "")).encode("ascii", "ignore").decode().upper()
    normalized = re.sub(r"[^A-Z0-9]+", "", normalized)
    if not normalized or "CONTANT" in normalized:
        return "CONTANTI"
    if "PAGOBANCOMAT" in normalized or normalized in {"BANCOMAT", "PAGOBANCO"}:
        return "PAGOBANCOMAT"
    if "CART" in normalized:
        return "CARTE"
    return "ALTRO_ELETTRONICO"


def _vending_payment_values(documents: List[Dict[str, Any]], legacy_raw: bool) -> List[tuple]:
    """Estrae importo e pagamento dalle due sorgenti vending compatibili."""
    values = []
    for document in documents:
        if legacy_raw:
            raw = document.get("raw") or []
            if len(raw) < 12:
                continue
            contabilizzata = str(raw[15] if len(raw) > 15 else "").strip().upper()
            esito = str(raw[16] if len(raw) > 16 else "").strip().upper()
            if contabilizzata and contabilizzata not in {"SI", "SÌ", "YES", "1", "TRUE"}:
                continue
            if "ANNULL" in esito:
                continue
            importo = raw[7]
            pagamento = raw[11]
        else:
            importo = document.get("importo")
            pagamento = document.get("pagamento")

        try:
            amount = money(importo or 0)
        except (TypeError, ValueError):
            continue
        values.append((amount, str(pagamento or "").strip()))
    return values


def _latest_historical_vending_date(documents: List[Dict[str, Any]]):
    """Restituisce l'ultima data coperta dallo storico vending Excel."""
    parsed_dates = []
    for document in documents:
        raw = document.get("raw") or []
        # DB_STORICO_VENDING_EXT: DATA è la terza colonna; DATA_ORA è il
        # fallback per file legacy che non valorizzano la colonna DATA.
        value = raw[2] if len(raw) > 2 and raw[2] else (raw[1] if len(raw) > 1 else None)
        parsed = _parse_sale_date(value)
        if parsed:
            parsed_dates.append(parsed)
    return max(parsed_dates, default=None)


def _calculate_dashboard_balances(
    vending_payments: List[tuple],
    saldo_cassa: float,
    totale_prelievi_vending: float = 0,
    totale_scontrini: float = 0,
    giacenza_iniziale_vending: float = 0,
) -> Dict[str, float]:
    """Calcola in un solo punto i saldi monetari esposti dalla dashboard.

    I prelievi registrati sono la fonte autorevole per la giacenza fisica. La
    giacenza è la giacenza iniziale più le vendite cash, meno quei prelievi.
    Il prelievo contabile netto e il saldo combinato sottraggono invece gli
    scontrini vending dal totale lordo prelevato.
    """
    cash = cards = pagobancomat = other_electronic = Decimal(0)
    for amount, payment_method in vending_payments:
        amount = money_decimal(amount)
        bucket = _vending_payment_bucket(payment_method)
        if bucket == "CONTANTI":
            cash += amount
        elif bucket == "CARTE":
            cards += amount
        elif bucket == "PAGOBANCOMAT":
            pagobancomat += amount
        else:
            other_electronic += amount

    cash, cards, pagobancomat, other_electronic = map(money, (cash, cards, pagobancomat, other_electronic))
    electronic = money_sum([cards, pagobancomat, other_electronic])
    total = money_sum([cash, electronic])
    saldo_cassa = money(saldo_cassa or 0)
    prelievo_vending_lordo = money(totale_prelievi_vending or 0)
    totale_scontrini = money(totale_scontrini or 0)
    prelievo_vending = money_sum([prelievo_vending_lordo, -totale_scontrini])
    giacenza_iniziale_vending = money(giacenza_iniziale_vending or 0)
    giacenza_vending = money_sum([giacenza_iniziale_vending, cash, -prelievo_vending_lordo])
    saldo_casse = money_sum([prelievo_vending, saldo_cassa])
    return {
        "saldoVendingTotale": total,
        "venditeVendingContanti": cash,
        "giacenzaInizialeVendingContanti": giacenza_iniziale_vending,
        "giacenzaVendingContanti": giacenza_vending,
        "prelievoVendingLordo": prelievo_vending_lordo,
        "prelievoVendingNetto": prelievo_vending,
        "prelievoVending": prelievo_vending,
        "scontriniVending": totale_scontrini,
        "prelievoDaVending": prelievo_vending,
        "prelieviContantiDaVending": prelievo_vending,
        "prelieviContantiCassaVending": prelievo_vending,
        "cassaVending": giacenza_vending,
        "giacenzaAttualeCassaVending": giacenza_vending,
        "prelieviVending": prelievo_vending,
        "saldoCassaNegozioEVending": saldo_casse,
        # Alias mantenuti per compatibilità con client meno recenti.
        "saldoVendingContanti": cash,
        "saldoVendingElettronico": electronic,
        "venditeVendingCarte": cards,
        "venditeVendingPagoBancomat": pagobancomat,
        "venditeVendingAltroElettronico": other_electronic,
        "saldoCassa": saldo_cassa,
        "totalePrelievi": prelievo_vending,
        "differenzaCassaVendingContanti": saldo_casse,
    }


async def _dashboard_balances(
    saldo_cassa: float,
    totale_prelievi_vending: float = 0,
    totale_scontrini: float = 0,
    latest_import_created_at: Any = None,
    giacenza_iniziale_vending: float = 0,
) -> Dict[str, float]:
    # DB_STORICO_VENDING_EXT è la fonte primaria perché conserva il metodo di
    # pagamento. Le vendite dell'app successive all'ultima data importata si
    # aggiungono allo storico; il filtro temporale impedisce doppi conteggi al
    # successivo import Excel.
    historical_docs, app_docs, accounting_ledger, accounting_snapshot = await asyncio.gather(
        db.db_storico_vending_ext.find({}, {"_id": 0, "raw": 1}).to_list(None),
        db.vendite.find(
            {"canale": {"$regex": "^VENDING$", "$options": "i"}},
            {
                "_id": 0, "data": 1, "created_at": 1, "importo": 1,
                "pagamento": 1, "sorgente": 1, "csv_event_key": 1,
                "csv_file_sha256": 1,
            },
        ).to_list(None),
        _vending_accounting_ledger_summary(),
        _latest_vending_accounting_snapshot(),
    )
    accounting_source = accounting_ledger or accounting_snapshot
    if accounting_source:
        amounts = accounting_source.get("importi_pagamenti") or {}
        payments = [(float(amount or 0), bucket) for bucket, amount in amounts.items()]
        aligned_at = _parse_utc_datetime(
            accounting_source.get("ledger_started_at") or accounting_source.get("created_at")
        )
        snapshot_event_keys = set(
            accounting_source.get("csv_event_keys")
            or [
                *(accounting_source.get("baseline_event_keys") or []),
                *(accounting_source.get("event_keys") or []),
            ]
        )
        snapshot_file_sha256 = str(accounting_source.get("sha256") or "")
        for document in app_docs:
            if accounting_ledger and str(document.get("sorgente") or "").upper() == "CSV_VENDING":
                # Ogni import CSV alimenta già il registro incrementale.
                continue
            # La fotografia CSV contiene gia' questi importi. Il confronto per
            # evento copre anche CSV cumulativi; lo SHA mantiene compatibili le
            # fotografie create prima dell'introduzione delle chiavi evento.
            event_key = str(document.get("csv_event_key") or "")
            file_sha256 = str(document.get("csv_file_sha256") or "")
            if event_key and event_key in snapshot_event_keys:
                continue
            if snapshot_file_sha256 and file_sha256 == snapshot_file_sha256:
                continue
            created_at = _parse_utc_datetime(document.get("created_at"))
            if not aligned_at or not created_at or created_at <= aligned_at:
                continue
            payments.extend(_vending_payment_values([document], legacy_raw=False))
        result = _calculate_dashboard_balances(
            payments,
            saldo_cassa,
            totale_prelievi_vending,
            totale_scontrini,
            giacenza_iniziale_vending,
        )
        result.update({
            "fonteVendingContabile": "CSV_VENDING",
            "fonteVendingFile": accounting_source.get("file_name"),
            "fonteVendingRighe": accounting_source.get("righe"),
            "fonteVendingDataDa": accounting_source.get("data_da"),
            "fonteVendingDataA": accounting_source.get("data_a"),
            "fonteVendingAllineataIl": accounting_source.get("created_at"),
        })
        return result

    payments = _vending_payment_values(historical_docs, legacy_raw=True)
    cutoff = _latest_historical_vending_date(historical_docs)
    supplemental_app_docs = []
    for document in app_docs:
        if historical_docs and cutoff is None and not latest_import_created_at:
            continue
        if not _is_sale_after_excel_import(document, cutoff, latest_import_created_at):
            continue
        supplemental_app_docs.append(document)
    payments.extend(_vending_payment_values(supplemental_app_docs, legacy_raw=False))
    return _calculate_dashboard_balances(
        payments,
        saldo_cassa,
        totale_prelievi_vending,
        totale_scontrini,
        giacenza_iniziale_vending,
    )


async def _supplemental_store_cash_sales(
    latest_imported_sale: Any, latest_import_created_at: Any = None
) -> float:
    """Somma gli incassi negozio registrati dopo lo storico incluso nell'Excel.

    RIEP_VENDITA contiene un totale cumulativo: il timestamp di registrazione
    separa le nuove vendite da quelle già comprese nell'ultimo import Excel.
    """
    cutoff = _parse_sale_date(latest_imported_sale)
    documents = await db.vendite.find(
        {}, {"_id": 0, "data": 1, "created_at": 1, "canale": 1, "pagamento": 1, "importo": 1}
    ).to_list(None)
    total = Decimal(0)
    for document in documents:
        channel = str(document.get("canale") or "NEGOZIO").strip().upper()
        if channel == "VENDING" or not _is_cash_payment(document.get("pagamento")):
            continue
        if not _is_sale_after_excel_import(document, cutoff, latest_import_created_at):
            continue
        try:
            total += money_decimal(document.get("importo") or 0)
        except (TypeError, ValueError):
            continue
    return money(total)


async def _shop_sold_accounting_corrections(latest_import_created_at: Any = None) -> float:
    """Somma le rettifiche monetarie successive all'ultima fotografia Excel.

    Un nuovo import contabile incorpora nuovamente il totale cumulativo dei
    venduti; escludere le rettifiche precedenti evita di contarle due volte.
    Le rettifiche legacy prive di importo non alterano retroattivamente i saldi.
    """
    cutoff = _parse_utc_datetime(latest_import_created_at)
    documents = await db.rettifiche_venduti.find(
        {"campo": "venduti_negozio"},
        {"_id": 0, "created_at": 1, "importo_contabile": 1},
    ).to_list(None)
    total = Decimal(0)
    for document in documents:
        created_at = _parse_utc_datetime(document.get("created_at"))
        if cutoff and (not created_at or created_at <= cutoff):
            continue
        try:
            total += money_decimal(document.get("importo_contabile") or 0)
        except (TypeError, ValueError):
            continue
    return money(total)


async def _dashboard_sales_trend(days: int = 30) -> Dict[str, Any]:
    """Restituisce l'andamento fino all'ultimo giorno di vendita disponibile."""
    # Il raggruppamento applicativo mantiene lo stesso risultato su MongoDB e
    # sul database locale mongomock, che non implementa $substrBytes.
    app_sales, imported_sales = await asyncio.gather(
        db.vendite.find({}, {"_id": 0, "data": 1, "importo": 1}).to_list(None),
        db.db_storico_vend.find({}, {"_id": 0, "data": 1, "importo": 1}).to_list(None),
    )

    totals_by_date: Dict[Any, Decimal] = {}
    for sale in [*imported_sales, *app_sales]:
        parsed_day = _parse_sale_date(sale.get("data"))
        if parsed_day is None:
            continue
        try:
            amount = money_decimal(sale.get("importo") or 0)
        except (TypeError, ValueError):
            continue
        totals_by_date[parsed_day] = totals_by_date.get(parsed_day, 0) + amount

    if not totals_by_date:
        return {
            "ultimo_giorno": None,
            "totale_ultimo_giorno": 0,
            "variazione_pct": None,
            "totale_periodo": 0,
            "serie": [],
        }

    end_date = max(totals_by_date)
    start_date = end_date - timedelta(days=days - 1)

    series = []
    for offset in range(days):
        current_date = start_date + timedelta(days=offset)
        series.append({"data": current_date.isoformat(), "importo": money(totals_by_date.get(current_date, 0))})

    latest_total = money(totals_by_date.get(end_date, 0))
    previous_values = [point["importo"] for point in series[:-1]]
    previous_average = sum(previous_values) / len(previous_values) if previous_values else 0
    variation = round(((latest_total - previous_average) / previous_average) * 100, 1) if previous_average else None
    return {
        "ultimo_giorno": end_date.isoformat(),
        "totale_ultimo_giorno": latest_total,
        "variazione_pct": variation,
        "totale_periodo": money_sum(point["importo"] for point in series),
        "serie": series,
    }


@api.get("/dashboard")
async def dashboard():
    pv = await pivot()
    ultimo_import_contabile, date_vendite_importate = await asyncio.gather(
        _latest_accounting_import(),
        db.db_storico_vend.find({}, {"_id": 0, "data": 1}).to_list(None),
    )
    ultima_vendita_importata = _latest_sale_date(date_vendite_importate)
    import_totali = (ultimo_import_contabile or {}).get("totali", {})
    venduto_negozio_excel = money(import_totali.get("valore_venduto_negozio_excel") or 0)
    venduto_vending_excel = money(import_totali.get("valore_venduto_vending_excel") or 0)
    venduto_totale_excel = money(import_totali.get("valore_venduto_totale_excel") or 0)
    if venduto_totale_excel:
        pv["kpi"]["valore_venduto"] = round(venduto_totale_excel, 2)
        pv["kpi"]["valore_venduto_negozio_excel"] = round(venduto_negozio_excel, 2)
        pv["kpi"]["valore_venduto_vending_excel"] = round(venduto_vending_excel, 2)
        pv["kpi"]["valore_venduto_totale_excel"] = round(venduto_totale_excel, 2)
        pv["kpi"]["margine_lordo"] = money_sum([venduto_totale_excel, -(pv["kpi"].get("valore_acquistato") or 0)])
    # vendite oggi
    oggi = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    v_oggi = {"tot": Decimal(0), "pezzi": 0}
    async for sale in db.vendite.find({"data": {"$regex": f"^{oggi}"}}, {"importo": 1, "quantita": 1}):
        v_oggi["tot"] += money_decimal(sale.get("importo") or 0)
        v_oggi["pezzi"] += sale.get("quantita") or 0
    versamenti, prelievi, scontrini, giacenza_iniziale_doc, venduto_negozio_app_contanti, rettifiche_venduto_negozio = await asyncio.gather(
        _versamenti_summary(),
        _prelievi_vending_summary(),
        _scontrini_vending_summary(),
        db.parametri.find_one(
            {"nome": "GIACENZA_INIZIALE_CONTANTI_VENDING"},
            {"_id": 0, "valore": 1},
        ),
        _supplemental_store_cash_sales(
            ultima_vendita_importata,
            (ultimo_import_contabile or {}).get("created_at"),
        ),
        _shop_sold_accounting_corrections(
            (ultimo_import_contabile or {}).get("created_at"),
        ),
    )
    giacenza_iniziale_vending = money((giacenza_iniziale_doc or {}).get("valore") or 0)
    venduto_negozio_contabilizzato = money_sum([
        venduto_negozio_excel, venduto_negozio_app_contanti, rettifiche_venduto_negozio,
    ])
    liquidita_residua = money_sum([venduto_negozio_contabilizzato, -versamenti["totale"]])
    saldi, andamento_vendite = await asyncio.gather(
        _dashboard_balances(
            liquidita_residua,
            prelievi["totale"],
            scontrini["totale"],
            (ultimo_import_contabile or {}).get("created_at"),
            giacenza_iniziale_vending,
        ),
        _dashboard_sales_trend(),
    )
    # vending
    vending = await list_vending()
    vend_da_caricare = sum(1 for v in vending if (v.get("proposta") or 0) > 0)
    return {
        "kpi": pv["kpi"],
        "vendite_oggi": {"importo": money(v_oggi.get("tot") or 0), "pezzi": v_oggi.get("pezzi") or 0},
        "totale_versamenti": versamenti["totale"],
        "totale_prelievi": prelievi["totale"],
        "totale_scontrini_vending": scontrini["totale"],
        "giacenza_vending_contanti": saldi["giacenzaVendingContanti"],
        "giacenza_iniziale_vending_contanti": giacenza_iniziale_vending,
        "venduto_negozio_excel": round(venduto_negozio_excel, 2),
        "venduto_negozio_app_contanti": venduto_negozio_app_contanti,
        "rettifiche_venduto_negozio": rettifiche_venduto_negozio,
        "venduto_negozio_contabilizzato": venduto_negozio_contabilizzato,
        "liquidita_residua": liquidita_residua,
        "saldo_cassa": liquidita_residua,
        "saldi": saldi,
        "andamento_vendite": andamento_vendite,
        "vending_da_caricare": vend_da_caricare,
        "vending_totale": len(vending),
    }


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
    operations = [UpdateOne({"codice": code}, {"$set": data}, upsert=True) for code, data in products_by_code.items()]
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

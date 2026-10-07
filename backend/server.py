from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, Request, Header
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateMany, UpdateOne
from pymongo.errors import BulkWriteError, DuplicateKeyError
import os, json, logging, uuid, io, re, asyncio, math, unicodedata, hashlib, gc, tempfile
from contextlib import asynccontextmanager
from pathlib import Path
import base64
import secrets
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # on_start è risolto quando il lifespan viene eseguito, dopo il caricamento
    # completo del modulo.
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

    return await call_next(request)


# ------------------------- Models -------------------------
class Prodotto(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codice: str
    descrizione: str
    categoria: str = "ACCESSORI"
    prezzo: float = 0
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
    prezzo: float = 0
    acquistati: int = 0
    venduti_negozio: int = 0
    venduti_vending: int = 0
    giacenza_negozio: int = 0
    giacenza_vending: int = 0
    colonna_vending: Optional[str] = None


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
    prezzo: float = 0
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
    prezzo: float = 0
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
    importo: float = 0
    canale: str = "NEGOZIO"  # NEGOZIO / VENDING
    pagamento: str = "CONTANTI"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sorgente: str = "MANUALE"
    batch_id: Optional[str] = None
    undo_meta: Dict[str, Any] = Field(default_factory=dict)


class VenditaIn(BaseModel):
    data: str
    codice: str
    descrizione: Optional[str] = ""
    quantita: int = 1
    importo: float = 0
    canale: str = "NEGOZIO"
    pagamento: str = "CONTANTI"


class OrdineStorico(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: str
    file_sorgente: str = ""
    codice: str
    descrizione: str = ""
    quantita: int
    prezzo: float = 0


class OrdineIn(BaseModel):
    data: str
    codice: str
    descrizione: Optional[str] = ""
    quantita: int
    prezzo: float = 0
    file_sorgente: Optional[str] = "manuale"


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
    importo: float
    descrizione: str = ""
    operatore: str = ""


class MovimentoIn(BaseModel):
    data: str
    tipo: str
    importo: float
    descrizione: Optional[str] = ""
    operatore: Optional[str] = ""


class Versamento(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: str
    importo: float
    descrizione: str = ""
    operatore: str = ""


class VersamentoIn(BaseModel):
    data: str
    importo: float
    descrizione: Optional[str] = ""
    operatore: Optional[str] = ""


class PrelievoVending(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: str
    importo: float
    descrizione: str = ""
    operatore: str = ""


class PrelievoVendingIn(BaseModel):
    data: str
    importo: float
    descrizione: Optional[str] = ""
    operatore: Optional[str] = ""


class ScontrinoVending(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    data: str
    importo: float
    descrizione: str = ""
    operatore: str = ""


class ScontrinoVendingIn(BaseModel):
    data: str
    importo: float
    descrizione: Optional[str] = ""
    operatore: Optional[str] = ""


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
    "cassa",
    "ordini_fornitore",
    "ordini_fornitore_righe",
    "import_history",
    "adm_sync",
    "anomalie_ignorate",
    "smart_venue",
    "smart_venue_hidden",
    "smart_venue_values",
]

BACKUP_FILE_FORMAT = "gestionale-tabacchi-backup"
BACKUP_FILE_VERSION = 1
OPTIONAL_BACKUP_COLLECTIONS = {
    "vendite_bulk_imports",
    "scontrini_vending",
    "cassa_vending_stato",
    "anomalie_ignorate",
    "smart_venue",
    "smart_venue_hidden",
    "smart_venue_values",
}


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
            db.db_storico_vend.create_index([("data", 1), ("codice", 1)]),
            db.storico_ordini.create_index("codice"),
            db.ordini_fornitore.create_index("batch_key", unique=True),
            db.ordini_fornitore_righe.create_index("batch_key"),
            db.backup_snapshots.create_index("created_at"),
            db.backup_snapshot_items.create_index([("snapshot_id", 1), ("collection", 1)]),
            db.import_history.create_index("created_at"),
            db.import_history.create_index("id"),
            db.prodotti.create_index("ultimo_import_id"),
            db.versamenti.create_index("data"),
            db.prelievi_vending.create_index("data"),
            db.scontrini_vending.create_index("data"),
            db.cassa_vending_stato.create_index("id", unique=True),
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
    except Exception as e:
        logging.exception("seed failed: %s", e)


# ------------------------- Helpers -------------------------
def strip_id(d):
    if d and "_id" in d:
        d.pop("_id", None)
    return d


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
    docs = await db.prodotti.find(filt, {"_id": 0}).sort("codice", 1).limit(capped_limit).to_list(capped_limit)
    return docs


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
    if await db.prodotti.find_one({"codice": payload["codice"]}):
        raise HTTPException(409, f"Codice prodotto già presente: {payload['codice']}")
    prod = Prodotto(**payload)
    await db.prodotti.insert_one(prod.model_dump())
    return prod.model_dump()


@api.put("/prodotti/{prod_id}")
async def update_prodotto(prod_id: str, p: ProdottoIn):
    payload = await _canonical_manual_product_payload(p)
    collision = await db.prodotti.find_one({"codice": payload["codice"], "id": {"$ne": prod_id}})
    if collision:
        raise HTTPException(409, f"Codice prodotto già presente: {payload['codice']}")
    r = await db.prodotti.update_one({"id": prod_id}, {"$set": payload})
    if r.matched_count == 0:
        raise HTTPException(404, "not found")
    doc = await db.prodotti.find_one({"id": prod_id}, {"_id": 0})
    return doc


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
async def add_vendita(v: VenditaIn):
    vv = VenditaGiornaliera(**v.model_dump())
    # aggiorna giacenze del prodotto
    prod = await db.prodotti.find_one({"codice": v.codice})
    if prod:
        vv.descrizione = vv.descrizione or prod.get("descrizione", "")
        field = "giacenza_vending" if v.canale == "VENDING" else "giacenza_negozio"
        vend_field = "venduti_vending" if v.canale == "VENDING" else "venduti_negozio"
        await db.prodotti.update_one({"codice": v.codice}, {"$inc": {field: -v.quantita, vend_field: v.quantita}})
    await db.vendite.insert_one(vv.model_dump())
    return vv.model_dump()


@api.delete("/vendite/{v_id}")
async def del_vendita(v_id: str):
    v = await db.vendite.find_one({"id": v_id})
    if v:
        field = "giacenza_vending" if v.get("canale") == "VENDING" else "giacenza_negozio"
        vend_field = "venduti_vending" if v.get("canale") == "VENDING" else "venduti_negozio"
        await db.prodotti.update_one({"codice": v["codice"]}, {"$inc": {field: v["quantita"], vend_field: -v["quantita"]}})
    await db.vendite.delete_one({"id": v_id})
    return {"ok": True}


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
    field = "giacenza_vending" if sale.get("canale") == "VENDING" else "giacenza_negozio"
    vend_field = "venduti_vending" if sale.get("canale") == "VENDING" else "venduti_negozio"
    quantity = int(sale.get("quantita") or 0)
    stock_update = await db.prodotti.update_one(
        {"codice": sale.get("codice")},
        {"$inc": {field: quantity, vend_field: -quantity}},
    )
    deletion = await db.vendite.delete_one({"id": sale_id, "sorgente": "MANUALE"})
    if deletion.deleted_count != 1:
        if stock_update.modified_count == 1:
            await db.prodotti.update_one(
                {"codice": sale.get("codice")},
                {"$inc": {field: -quantity, vend_field: quantity}},
            )
        raise HTTPException(409, "La vendita è cambiata durante l'annullamento")
    return {"ok": True, "rimossi": 1, "id": sale_id}


class BulkVenditaIn(BaseModel):
    canale: str = "NEGOZIO"
    pagamento: str = "CONTANTI"
    righe: List[Dict[str, Any]]  # {data, codice, descrizione, quantita, importo}


@api.post("/vendite/bulk")
async def bulk_vendite(body: BulkVenditaIn):
    """Bulk paste da Excel: rows with data, codice, descrizione, quantita, importo."""
    batch_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    inserted = 0
    skipped = 0
    errors = []
    for i, r in enumerate(body.righe):
        try:
            codice = str(r.get("codice") or "").strip()
            if not codice:
                skipped += 1
                continue
            qta = int(float(r.get("quantita") or 0))
            imp = float(r.get("importo") or 0)
            if qta <= 0:
                skipped += 1
                continue
            data = str(r.get("data") or datetime.now(timezone.utc).isoformat())
            desc = str(r.get("descrizione") or "")
            prod = await db.prodotti.find_one({"codice": codice})
            if prod:
                desc = desc or prod.get("descrizione", "")
                field = "giacenza_vending" if body.canale == "VENDING" else "giacenza_negozio"
                vend_field = "venduti_vending" if body.canale == "VENDING" else "venduti_negozio"
                await db.prodotti.update_one({"codice": codice}, {"$inc": {field: -qta, vend_field: qta}})
            v = VenditaGiornaliera(
                data=data,
                codice=codice,
                descrizione=desc,
                quantita=qta,
                importo=imp,
                canale=body.canale,
                pagamento=body.pagamento,
                sorgente="BULK",
                batch_id=batch_id,
            )
            await db.vendite.insert_one(v.model_dump())
            inserted += 1
        except Exception as e:
            errors.append({"riga": i + 1, "errore": str(e)})
    if inserted:
        await db.vendite_bulk_imports.insert_one({
            "id": batch_id,
            "created_at": created_at,
            "sorgente": "BULK",
            "canale": body.canale,
            "pagamento": body.pagamento,
            "inseriti": inserted,
            "saltati": skipped,
            "status": "active",
        })
    return {
        "inseriti": inserted,
        "saltati": skipped,
        "errori": errors,
        "batch_id": batch_id if inserted else None,
        "created_at": created_at if inserted else None,
        "canale": body.canale,
        "pagamento": body.pagamento,
    }


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

    removed = 0
    try:
        sales = await db.vendite.find(
            {"batch_id": batch_id, "sorgente": "BULK"},
            {"_id": 0},
        ).to_list(None)
        for sale in sales:
            field = "giacenza_vending" if sale.get("canale") == "VENDING" else "giacenza_negozio"
            vend_field = "venduti_vending" if sale.get("canale") == "VENDING" else "venduti_negozio"
            quantity = int(sale.get("quantita") or 0)
            stock_update = await db.prodotti.update_one(
                {"codice": sale.get("codice")},
                {"$inc": {field: quantity, vend_field: -quantity}},
            )
            deletion = await db.vendite.delete_one({"id": sale.get("id"), "batch_id": batch_id})
            if deletion.deleted_count != 1:
                if stock_update.modified_count == 1:
                    await db.prodotti.update_one(
                        {"codice": sale.get("codice")},
                        {"$inc": {field: -quantity, vend_field: quantity}},
                    )
                raise RuntimeError("Una vendita del caricamento non è stata eliminata")
            removed += 1

        await db.vendite_bulk_imports.update_one(
            {"id": batch_id},
            {"$set": {
                "status": "annulled",
                "annulled_at": datetime.now(timezone.utc).isoformat(),
                "rimossi": removed,
            }},
        )
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


async def create_backup_snapshot(label: str, reason: str = "manuale") -> Dict[str, Any]:
    snapshot_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    counts: Dict[str, int] = {}
    await db.backup_snapshot_items.delete_many({"snapshot_id": snapshot_id})
    for collection_name in BACKUP_COLLECTIONS:
        collection = db[collection_name]
        docs = await collection.find({}, {"_id": 0}).to_list(25000)
        counts[collection_name] = len(docs)
        if docs:
            await db.backup_snapshot_items.insert_many([
                {"snapshot_id": snapshot_id, "collection": collection_name, "doc": doc}
                for doc in docs
            ])
    total_docs = sum(counts.values())
    meta = {
        "id": snapshot_id,
        "label": label[:180],
        "reason": reason[:80],
        "created_at": created_at,
        "counts": counts,
        "total_docs": total_docs,
        "status": "READY",
    }
    # Motor muta il dizionario passato a insert_one aggiungendo ``_id``.
    # Manteniamo pulito l'oggetto restituito all'API: ObjectId non e' JSON
    # serializzabile e faceva apparire fallito un backup gia' creato.
    await db.backup_snapshots.insert_one(meta.copy())
    return meta


async def _backup_documents(
    snapshot_id: str, collection_names: Optional[List[str]] = None
) -> Dict[str, List[Dict[str, Any]]]:
    collections: Dict[str, List[Dict[str, Any]]] = {}
    names = BACKUP_COLLECTIONS if collection_names is None else collection_names
    for collection_name in names:
        items = await db.backup_snapshot_items.find(
            {"snapshot_id": snapshot_id, "collection": collection_name},
            {"_id": 0, "doc": 1},
        ).to_list(25000)
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
    for collection_name, docs in collections.items():
        if not isinstance(docs, list) or len(docs) > 25000:
            raise HTTPException(422, f"Collezione {collection_name} non valida o troppo grande")
        if any(not isinstance(doc, dict) or "_id" in doc for doc in docs):
            raise HTTPException(422, f"Documento non valido nella collezione {collection_name}")
    return collections


async def _restore_collections(collections: Dict[str, List[Dict[str, Any]]]):
    for collection_name in BACKUP_COLLECTIONS:
        if collection_name not in collections:
            continue
        await db[collection_name].delete_many({})
        docs = collections[collection_name]
        if docs:
            await db[collection_name].insert_many(docs)


@api.post("/backup/create")
async def backup_create(body: Optional[Dict[str, Any]] = None):
    label = str((body or {}).get("label") or f"Backup {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}")
    reason = str((body or {}).get("reason") or "manuale")
    return await create_backup_snapshot(label, reason)


@api.get("/backup")
async def backup_list(limit: int = 20):
    capped_limit = max(1, min(int(limit or 20), 100))
    docs = await db.backup_snapshots.find({}, {"_id": 0}).sort("created_at", -1).limit(capped_limit).to_list(capped_limit)
    return docs


@api.get("/backup/{snapshot_id}/download")
async def backup_download(snapshot_id: str):
    meta = await db.backup_snapshots.find_one({"id": snapshot_id}, {"_id": 0})
    if not meta:
        raise HTTPException(404, "backup non trovato")
    backed_up_names = list((meta.get("counts") or {}).keys())
    missing = [
        name for name in BACKUP_COLLECTIONS
        if name not in backed_up_names and name not in OPTIONAL_BACKUP_COLLECTIONS
    ]
    if missing:
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
    meta = await db.backup_snapshots.find_one({"id": snapshot_id}, {"_id": 0})
    if not meta:
        raise HTTPException(404, "backup non trovato")
    restore_backup = await create_backup_snapshot(
        f"Prima del ripristino {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}",
        "pre-restore",
    )
    # I vecchi snapshot non contenevano tutte le collezioni operative: in quel
    # caso ripristiniamo soltanto ciò che era stato effettivamente salvato.
    backed_up_names = [name for name in (meta.get("counts") or {}) if name in BACKUP_COLLECTIONS]
    await _restore_collections(await _backup_documents(snapshot_id, backed_up_names))
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
    restore_backup = await create_backup_snapshot(
        f"Prima del ripristino file {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}",
        "pre-restore-file",
    )
    await _restore_collections(collections)
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
        db.backup_snapshots.find_one({}, {"_id": 0}, sort=[("created_at", -1)]),
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
    products = await db.prodotti.find(
        {"$or": [{"codice": rx}, {"descrizione": rx}]},
        {"_id": 0},
    ).sort("codice", 1).limit(capped).to_list(capped)
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
        "%d/%m/%Y", "%d-%m-%Y",
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
            prezzo_f = float(str(prezzo).replace(",", ".")) if prezzo not in (None, "") else 0.0
            data_iso = _csv_vending_datetime(data)
            rows.append({
                "riga": i + 2,
                "data": data_iso,
                "codice": codice,
                "nome": nome,
                "prezzo": prezzo_f,
                "colonna": colonna,
                "categoria": categoria,
                "pagamento": pag,
            })
        except Exception as exc:
            errors.append({"riga": i + 2, "errore": str(exc)})
    return {"righe": rows, "saltati": skipped, "errori": errors, "delimitatore": delim}


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
        "%d/%m/%Y", "%d-%m-%Y",
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


async def _prepare_csv_vending_import(raw: str, pagamento: str) -> tuple[Dict[str, Any], Any, int]:
    parsed = _parse_csv_vending(raw, pagamento)
    historical_documents = await db.db_storico_vending_ext.find(
        {}, {"_id": 0, "raw": 1}
    ).to_list(None)
    all_rows = parsed["righe"]
    parsed["righe"], cutoff = _filter_new_csv_vending_rows(all_rows, historical_documents)
    return parsed, cutoff, len(all_rows)


VENDING_CASH_STATE_ID = "saldo"


async def _vending_cash_balance() -> float:
    """Restituisce la giacenza cash, inizializzandola dai dati legacy una volta."""
    state = await db.cassa_vending_stato.find_one({"id": VENDING_CASH_STATE_ID}, {"_id": 0})
    if state:
        return round(float(state.get("giacenza") or 0), 2)

    legacy_docs = await db.prelievi_vending.find({}, {"_id": 0, "importo": 1}).to_list(10000)
    legacy_balance = round(sum(float(doc.get("importo") or 0) for doc in legacy_docs), 2)
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
    )
    state = await db.cassa_vending_stato.find_one({"id": VENDING_CASH_STATE_ID}, {"_id": 0})
    return round(float((state or {}).get("giacenza") or 0), 2)


async def _adjust_vending_cash_balance(amount: float) -> float:
    await _vending_cash_balance()
    await db.cassa_vending_stato.update_one(
        {"id": VENDING_CASH_STATE_ID},
        {
            "$inc": {"giacenza": round(float(amount or 0), 2)},
            "$set": {"updated_at": datetime.now(timezone.utc).isoformat()},
        },
    )
    return await _vending_cash_balance()


@api.post("/vendite/preview-csv-vending")
async def preview_csv_vending(file: UploadFile = File(...), pagamento: str = "CONTANTI"):
    """Mostra solo le vendite successive allo storico vending importato da Excel."""
    raw = (await _read_capped(file)).decode("utf-8-sig", errors="replace")
    parsed, cutoff, file_rows = await _prepare_csv_vending_import(raw, pagamento)
    payment_counts: Dict[str, int] = {}
    total = 0.0
    for row in parsed["righe"]:
        method = row["pagamento"]
        payment_counts[method] = payment_counts.get(method, 0) + 1
        total += row["prezzo"]
    dates = [row["data"][:10] for row in parsed["righe"]]
    return {
        "righe": len(parsed["righe"]),
        "saltati": parsed["saltati"],
        "errori": parsed["errori"],
        "delimitatore": parsed["delimitatore"],
        "pagamenti": payment_counts,
        "totale": round(total, 2),
        "data_da": min(dates, default=None),
        "data_a": max(dates, default=None),
        "righe_file": file_rows,
        "righe_gia_presenti": file_rows - len(parsed["righe"]),
        "ultima_vendita_excel": cutoff.isoformat() if cutoff else None,
        "vendite": parsed["righe"],
    }


@api.post("/vendite/import-csv-vending")
async def import_csv_vending(file: UploadFile = File(...), pagamento: str = "CONTANTI"):
    """Importa il CSV gia' validato dall'anteprima della UI."""
    raw = (await _read_capped(file)).decode("utf-8-sig", errors="replace")
    parsed, cutoff, file_rows = await _prepare_csv_vending_import(raw, pagamento)
    batch_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    inserted = 0
    imported_cash = 0.0
    errors = list(parsed["errori"])
    payment_counts: Dict[str, int] = {}
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
                # crea un placeholder
                slug = re.sub(r"\s+", "_", nome.strip().lower())[:20]
                codice = f"CSV-{slug}"

            # aggiorna vending column giacenza se colonna presente
            if colonna:
                col = await db.vending.find_one({"colonna": colonna})
                if col:
                    old_g = int(col.get("giacenza") or 0)
                    new_g = max(0, old_g - 1)
                    await db.vending.update_one({"id": col["id"]}, {"$set": {"giacenza": new_g}})
                    undo_meta["vending_column_id"] = col["id"]
                    undo_meta["vending_stock_decremented"] = old_g > 0
            # aggiorna prodotto
            prod = prod or await db.prodotti.find_one({"codice": codice})
            if prod:
                await db.prodotti.update_one({"codice": codice}, {"$inc": {"giacenza_vending": -1, "venduti_vending": 1}})
                undo_meta["product_stock_adjusted"] = True
            else:
                # crea prodotto minimale
                product_doc = Prodotto(
                    codice=codice, descrizione=nome or codice,
                    categoria=(categoria.upper() or "SIGARETTE") if categoria else "SIGARETTE",
                    prezzo=prezzo_f, venduti_vending=1,
                ).model_dump()
                product_doc["created_from_csv_batch"] = batch_id
                await db.prodotti.insert_one(product_doc)
                undo_meta["product_created"] = True
                undo_meta["product_id"] = product_doc["id"]

            # ogni riga CSV = 1 pezzo venduto (formato tipico distributore)
            v = VenditaGiornaliera(
                data=data_iso, codice=codice, descrizione=nome, quantita=1, importo=prezzo_f,
                canale="VENDING", pagamento=pag, sorgente="CSV_VENDING",
                batch_id=batch_id, undo_meta=undo_meta,
            )
            await db.vendite.insert_one(v.model_dump())
            inserted += 1
            payment_counts[pag] = payment_counts.get(pag, 0) + 1
            if _is_cash_payment(pag):
                imported_cash += prezzo_f
        except Exception as exc:
            if undo_meta.get("product_stock_adjusted"):
                await db.prodotti.update_one(
                    {"codice": codice},
                    {"$inc": {"giacenza_vending": 1, "venduti_vending": -1}},
                )
            elif undo_meta.get("product_created"):
                await db.prodotti.delete_one({"id": undo_meta.get("product_id"), "created_from_csv_batch": batch_id})
            if undo_meta.get("vending_stock_decremented"):
                await db.vending.update_one(
                    {"id": undo_meta.get("vending_column_id")},
                    {"$inc": {"giacenza": 1}},
                )
            errors.append({"riga": csv_row["riga"], "errore": str(exc)})
    if imported_cash:
        await _adjust_vending_cash_balance(imported_cash)
    if inserted:
        await db.vendite_bulk_imports.insert_one({
            "id": batch_id,
            "created_at": created_at,
            "sorgente": "CSV_VENDING",
            "canale": "VENDING",
            "pagamento": pagamento,
            "inseriti": inserted,
            "saltati": parsed["saltati"],
            "status": "active",
        })
    return {
        "inseriti": inserted,
        "saltati": parsed["saltati"],
        "errori": errors,
        "delimitatore": parsed["delimitatore"],
        "pagamenti": payment_counts,
        "contanti_aggiunti_giacenza": round(imported_cash, 2),
        "righe_file": file_rows,
        "righe_gia_presenti": file_rows - len(parsed["righe"]),
        "ultima_vendita_excel": cutoff.isoformat() if cutoff else None,
        "batch_id": batch_id if inserted else None,
        "created_at": created_at if inserted else None,
        "sorgente": "CSV_VENDING",
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

    removed = 0
    cash_to_restore = 0.0
    try:
        sales = await db.vendite.find(
            {"batch_id": batch_id, "sorgente": "CSV_VENDING"},
            {"_id": 0},
        ).to_list(None)
        for sale in sales:
            undo_meta = sale.get("undo_meta") or {}
            codice = sale.get("codice")
            if undo_meta.get("product_stock_adjusted"):
                await db.prodotti.update_one(
                    {"codice": codice},
                    {"$inc": {"giacenza_vending": 1, "venduti_vending": -1}},
                )
            elif undo_meta.get("product_created"):
                product = await db.prodotti.find_one({"id": undo_meta.get("product_id")})
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
                    await db.prodotti.delete_one({"id": product["id"], "created_from_csv_batch": batch_id})
                else:
                    await db.prodotti.update_one({"codice": codice}, {"$inc": {"venduti_vending": -1}})
            if undo_meta.get("vending_stock_decremented"):
                await db.vending.update_one(
                    {"id": undo_meta.get("vending_column_id")},
                    {"$inc": {"giacenza": 1}},
                )
            if _is_cash_payment(sale.get("pagamento")):
                cash_to_restore += float(sale.get("importo") or 0)
            deletion = await db.vendite.delete_one({"id": sale.get("id"), "batch_id": batch_id})
            if deletion.deleted_count != 1:
                raise RuntimeError("Una vendita CSV non è stata eliminata")
            removed += 1

        created_products = await db.prodotti.find(
            {"created_from_csv_batch": batch_id},
            {"_id": 0},
        ).to_list(None)
        for product in created_products:
            if all(
                int(product.get(field) or 0) == 0
                for field in (
                    "acquistati", "venduti_negozio", "venduti_vending",
                    "giacenza_negozio", "giacenza_vending",
                )
            ):
                await db.prodotti.delete_one({"id": product.get("id"), "created_from_csv_batch": batch_id})

        if cash_to_restore:
            await _adjust_vending_cash_balance(-cash_to_restore)
        await db.vendite_bulk_imports.update_one(
            {"id": batch_id},
            {"$set": {
                "status": "annulled",
                "annulled_at": datetime.now(timezone.utc).isoformat(),
                "rimossi": removed,
            }},
        )
    except Exception as exc:
        await db.vendite_bulk_imports.update_one(
            {"id": batch_id, "status": "annulling"},
            {"$set": {"status": "active"}},
        )
        raise HTTPException(500, f"Impossibile annullare il CSV: {exc}") from exc

    return {
        "ok": True,
        "batch_id": batch_id,
        "rimossi": removed,
        "contanti_rimossi_giacenza": round(cash_to_restore, 2),
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


async def _sync_product_vending_stock(codice: str) -> None:
    """Allinea il totale prodotto alla somma delle colonne vending reali."""
    if not codice:
        return
    columns = await db.vending.find(
        {"codice": codice},
        {"_id": 0, "giacenza": 1},
    ).to_list(500)
    totale = sum(max(0, int(row.get("giacenza", 0) or 0)) for row in columns)
    await db.prodotti.update_one({"codice": codice}, {"$set": {"giacenza_vending": totale}})


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
    vending_result = await db.vending.update_one(
        {"id": v_id, "giacenza": v.get("giacenza", 0)},
        {"$inc": {"giacenza": qta}},
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
            {"$inc": {"giacenza": -qta}},
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
    try:
        for item in preparate:
            result = await db.vending.update_one(
                {"id": item["id"], "giacenza": item["giacenza"]},
                {"$inc": {"giacenza": item["quantita"]}},
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
                {"$inc": {"giacenza": -item["quantita"]}},
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



# ------------------------- Storico ordini -------------------------
@api.get("/ordini")
async def list_ordini(limit: int = 1000):
    capped_limit = _cap(limit)
    docs = await db.storico_ordini.find({}, {"_id": 0}).sort("data", -1).limit(capped_limit).to_list(capped_limit)
    return docs


@api.post("/ordini")
async def add_ordine(o: OrdineIn):
    oo = OrdineStorico(**o.model_dump())
    await db.storico_ordini.insert_one(oo.model_dump())
    # aggiorna giacenza / acquisti
    if o.codice:
        await db.prodotti.update_one({"codice": o.codice}, {"$inc": {"giacenza_negozio": o.quantita, "acquistati": o.quantita}})
    return oo.model_dump()


@api.delete("/ordini/{o_id}")
async def del_ordine(o_id: str):
    await db.storico_ordini.delete_one({"id": o_id})
    return {"ok": True}


class BulkOrdineIn(BaseModel):
    file_sorgente: str = "carico manuale"
    data: Optional[str] = None
    righe: List[Dict[str, Any]]  # {codice, descrizione, quantita, prezzo}


@api.post("/ordini/bulk")
async def bulk_carico(body: BulkOrdineIn):
    """Ricezione merce: registra il ricevuto e incrementa scorte/acquistati.

    Un batch Auto-Order è solo un ordine fornitore; passa da questo endpoint
    esclusivamente quando la merce è fisicamente arrivata.
    """
    data = body.data or datetime.now(timezone.utc).isoformat()
    inserted = 0
    creati_prodotti = 0
    errors = []
    for i, r in enumerate(body.righe):
        try:
            codice = str(r.get("codice") or "").strip()
            qta = int(float(r.get("quantita") or 0))
            if not codice or qta == 0:
                continue
            prezzo = float(r.get("prezzo") or 0)
            desc = str(r.get("descrizione") or "")
            o = OrdineStorico(data=data, file_sorgente=body.file_sorgente, codice=codice, descrizione=desc, quantita=qta, prezzo=prezzo)
            await db.storico_ordini.insert_one(o.model_dump())
            prod = await db.prodotti.find_one({"codice": codice})
            if prod:
                await db.prodotti.update_one(
                    {"codice": codice},
                    {"$inc": {"giacenza_negozio": qta, "acquistati": qta}},
                )
            else:
                # crea prodotto minimale
                await db.prodotti.insert_one(Prodotto(
                    codice=codice, descrizione=desc or codice,
                    categoria=_cat_from_desc(desc, codice),
                    prezzo=prezzo, acquistati=qta, giacenza_negozio=qta,
                ).model_dump())
                creati_prodotti += 1
            inserted += 1
        except Exception as e:
            errors.append({"riga": i + 1, "errore": str(e)})
    return {"caricate": inserted, "prodotti_nuovi": creati_prodotti, "errori": errors}




# ------------------------- Versamenti / Prelievi vending / Cassa -------------------------
async def _versamenti_summary(limit: int = 500) -> Dict[str, Any]:
    capped_limit = _cap(limit)
    docs = await db.versamenti.find({}, {"_id": 0}).sort("data", -1).limit(capped_limit).to_list(capped_limit)
    all_docs = await db.versamenti.find({}, {"_id": 0}).to_list(10000)
    totale = sum(float(d.get("importo") or 0) for d in all_docs)
    return {"movimenti": docs, "totale": round(totale, 2)}


@api.get("/versamenti")
async def list_versamenti(limit: int = 500):
    return await _versamenti_summary(limit)


@api.post("/versamenti")
async def add_versamento(m: VersamentoIn):
    if m.importo <= 0:
        raise HTTPException(422, "L'importo del versamento deve essere positivo")
    versamento = Versamento(**m.model_dump())
    await db.versamenti.insert_one(versamento.model_dump())
    return versamento.model_dump()


@api.delete("/versamenti/{m_id}")
async def del_versamento(m_id: str):
    await db.versamenti.delete_one({"id": m_id})
    return {"ok": True}


async def _prelievi_vending_summary(limit: int = 500) -> Dict[str, Any]:
    capped_limit = _cap(limit)
    docs = await db.prelievi_vending.find({}, {"_id": 0}).sort("data", -1).limit(capped_limit).to_list(capped_limit)
    all_docs = await db.prelievi_vending.find({}, {"_id": 0}).to_list(10000)
    totale = sum(float(d.get("importo") or 0) for d in all_docs)
    return {"movimenti": docs, "totale": round(totale, 2)}


@api.get("/prelievi-vending")
async def list_prelievi_vending(limit: int = 500):
    return await _prelievi_vending_summary(limit)


@api.post("/prelievi-vending")
async def add_prelievo_vending(m: PrelievoVendingIn):
    if m.importo <= 0:
        raise HTTPException(422, "L'importo del prelievo deve essere positivo")
    await _vending_cash_balance()
    prelievo = PrelievoVending(**m.model_dump())
    await db.prelievi_vending.insert_one(prelievo.model_dump())
    await _adjust_vending_cash_balance(-m.importo)
    return prelievo.model_dump()


@api.delete("/prelievi-vending/{m_id}")
async def del_prelievo_vending(m_id: str):
    await _vending_cash_balance()
    prelievo = await db.prelievi_vending.find_one({"id": m_id}, {"_id": 0})
    await db.prelievi_vending.delete_one({"id": m_id})
    if prelievo:
        await _adjust_vending_cash_balance(float(prelievo.get("importo") or 0))
    return {"ok": True}


async def _scontrini_vending_summary(limit: int = 500) -> Dict[str, Any]:
    capped_limit = _cap(limit)
    docs = await db.scontrini_vending.find({}, {"_id": 0}).sort("data", -1).limit(capped_limit).to_list(capped_limit)
    all_docs = await db.scontrini_vending.find({}, {"_id": 0}).to_list(10000)
    totale = sum(float(d.get("importo") or 0) for d in all_docs)
    return {"movimenti": docs, "totale": round(totale, 2)}


@api.get("/scontrini-vending")
async def list_scontrini_vending(limit: int = 500):
    return await _scontrini_vending_summary(limit)


@api.post("/scontrini-vending")
async def add_scontrino_vending(m: ScontrinoVendingIn):
    if m.importo <= 0:
        raise HTTPException(422, "L'importo dello scontrino deve essere positivo")
    scontrino = ScontrinoVending(**m.model_dump())
    await db.scontrini_vending.insert_one(scontrino.model_dump())
    return scontrino.model_dump()


@api.delete("/scontrini-vending/{m_id}")
async def del_scontrino_vending(m_id: str):
    await db.scontrini_vending.delete_one({"id": m_id})
    return {"ok": True}


@api.get("/cassa")
async def list_cassa(limit: int = 500):
    capped_limit = _cap(limit)
    docs = await db.cassa.find({}, {"_id": 0}).sort("data", -1).limit(capped_limit).to_list(capped_limit)
    saldo = 0.0
    all_docs = await db.cassa.find({}, {"_id": 0}).to_list(10000)
    for d in all_docs:
        if d["tipo"] in ("ENTRATA", "SALDO_INIZIALE"):
            saldo += d["importo"]
        else:
            saldo -= d["importo"]
    return {"movimenti": docs, "saldo": round(saldo, 2)}


@api.post("/cassa")
async def add_cassa(m: MovimentoIn):
    mm = MovimentoCassa(**m.model_dump())
    await db.cassa.insert_one(mm.model_dump())
    return mm.model_dump()


@api.delete("/cassa/{m_id}")
async def del_cassa(m_id: str):
    await db.cassa.delete_one({"id": m_id})
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
    bounds = PARAM_BOUNDS.get(nome)
    if bounds is not None:
        lo, hi, msg = bounds
        if not (lo <= body.valore < hi):
            raise HTTPException(422, f"{nome} fuori range consentito ({msg}). Ricevuto: {body.valore}")
    if nome == "SCORTA_MINIMA_NEGOZIO_VENDING" and not float(body.valore).is_integer():
        raise HTTPException(422, "SCORTA_MINIMA_NEGOZIO_VENDING deve essere un numero intero")
    r = await db.parametri.update_one({"nome": nome}, {"$set": {"valore": body.valore}})
    if r.matched_count == 0:
        await db.parametri.insert_one(Parametro(nome=nome, valore=body.valore).model_dump())
    doc = await db.parametri.find_one({"nome": nome}, {"_id": 0})
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
    ) = await asyncio.gather(
        db.prodotti.find({}, {"_id": 0}).to_list(5000),
        db.vendite.aggregate(pipeline_breve).to_list(5000),
        db.vendite.aggregate(pipeline_lunga).to_list(5000),
        db.db_storico_vend.aggregate(pipeline_breve).to_list(5000),
        db.db_storico_vend.aggregate(pipeline_lunga).to_list(5000),
        db.storico_ordini.aggregate(storico_pipeline).to_list(5000),
    )

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
            "totale": round(qta * (p.get("prezzo") or 0), 2),
        }
        if stato == "ORDINA ORA" and not anomalia and qta > 0:
            proposte.append(row)
        else:
            esclusi.append(row)

    proposte.sort(key=lambda x: (-x["totale"], x["codice"]))
    ordine_stati = {"ANOMALIA": 0, "CONTROLLO MANUALE": 1, "MONITORA": 2, "NESSUN ORDINE": 3}
    esclusi.sort(key=lambda x: (ordine_stati.get(x["stato"], 9), x["descrizione"], x["codice"]))
    tot = round(sum(x["totale"] for x in proposte), 2)
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
            "totale": round(quantity * float(row.get("prezzo") or 0), 2),
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
        "totale": round(sum(r.get("totale", 0) or 0 for r in righe), 2),
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
                "prezzo": float(r.get("prezzo") or 0),
                "totale": round(float(r.get("totale") or 0), 2),
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
    totale = round(sum(r.get("totale", 0) or 0 for r in righe), 2)

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
    totale = round(sum(float(r.get("totale", 0) or 0) for r in righe), 2)

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
    prods = await db.prodotti.find({"presente_ultimo_import": {"$ne": False}}, {"_id": 0}).to_list(5000)
    params = await get_params()
    divisor = params.get("VENDITE_GIORNALIERE_MESE", 6.5)
    aggio = params.get("AGGIO_PCT", 0.10)
    cost_factor = max(0.0, 1.0 - aggio)  # costo acquisto = prezzo * cost_factor
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
        prz = p.get("prezzo", 0) or 0
        prz_costo = prz * cost_factor
        acq = p.get("acquistati", 0) or 0
        ta = acq * prz_costo       # valore acquistato al COSTO (netto aggio)
        tv = v_tot * prz           # ricavo vendita al PREZZO retail
        tg = g_tot * prz_costo     # valore giacenza al COSTO (netto aggio)
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
            "prezzo": prz, "tot_acquistato": round(ta, 2), "tot_venduto": round(tv, 2), "tot_giacenza": round(tg, 2),
            "vendite_mese": round(vend_mese, 2), "mesi_smaltimento": mesi_smalt,
            "stato": stato, "azione": azione,
        })
    rows.sort(key=lambda x: -x["tot_giacenza"])
    return {
        "kpi": {
            "valore_acquistato": round(tot_acq, 2),
            "valore_venduto": round(tot_vend, 2),
            "valore_giacenza": round(tot_giac, 2),
            "margine_lordo": round(tot_vend - tot_acq, 2),
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
            amount = float(str(importo or 0).strip().replace(",", "."))
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
    giacenza_vending: float = 0,
    totale_scontrini: float = 0,
) -> Dict[str, float]:
    """Calcola in un solo punto i saldi monetari esposti dalla dashboard.

    Il CSV incrementa sia le vendite cash sia la giacenza fisica. I prelievi
    sono la differenza tra vendite cash e giacenza, più gli scontrini. Nel saldo
    complessivo gli scontrini vengono sottratti di nuovo, perché non devono
    aumentare la disponibilità combinata di negozio e vending.
    """
    cash = 0.0
    electronic = 0.0
    for amount, payment_method in vending_payments:
        if _is_cash_payment(payment_method):
            cash += amount
        else:
            # Il modello prevede due soli bucket: ogni metodo non-contante
            # valorizzato (Carte, PagoBancomat, POS, ecc.) resta elettronico.
            # I valori legacy vuoti seguono il default storico CONTANTI dell'app.
            electronic += amount

    cash = round(cash, 2)
    electronic = round(electronic, 2)
    total = round(cash + electronic, 2)
    saldo_cassa = round(float(saldo_cassa or 0), 2)
    giacenza_vending = round(float(giacenza_vending or 0), 2)
    totale_scontrini = round(float(totale_scontrini or 0), 2)
    prelievo_vending = round(cash - giacenza_vending + totale_scontrini, 2)
    saldo_casse = round(prelievo_vending + saldo_cassa - totale_scontrini, 2)
    return {
        "saldoVendingTotale": total,
        "venditeVendingContanti": cash,
        "giacenzaVendingContanti": giacenza_vending,
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
        "saldoCassa": saldo_cassa,
        "totalePrelievi": prelievo_vending,
        "differenzaCassaVendingContanti": saldo_casse,
    }


async def _dashboard_balances(
    saldo_cassa: float,
    giacenza_vending: float = 0,
    totale_scontrini: float = 0,
    latest_import_created_at: Any = None,
) -> Dict[str, float]:
    # DB_STORICO_VENDING_EXT è la fonte primaria perché conserva il metodo di
    # pagamento. Le vendite dell'app successive all'ultima data importata si
    # aggiungono allo storico; il filtro temporale impedisce doppi conteggi al
    # successivo import Excel.
    historical_docs, app_docs = await asyncio.gather(
        db.db_storico_vending_ext.find({}, {"_id": 0, "raw": 1}).to_list(None),
        db.vendite.find(
            {"canale": {"$regex": "^VENDING$", "$options": "i"}},
            {"_id": 0, "data": 1, "created_at": 1, "importo": 1, "pagamento": 1},
        ).to_list(None),
    )
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
    return _calculate_dashboard_balances(payments, saldo_cassa, giacenza_vending, totale_scontrini)


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
    total = 0.0
    for document in documents:
        channel = str(document.get("canale") or "NEGOZIO").strip().upper()
        if channel == "VENDING" or not _is_cash_payment(document.get("pagamento")):
            continue
        if not _is_sale_after_excel_import(document, cutoff, latest_import_created_at):
            continue
        try:
            total += float(document.get("importo") or 0)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


async def _dashboard_sales_trend(days: int = 30) -> Dict[str, Any]:
    """Restituisce l'andamento fino all'ultimo giorno di vendita disponibile."""
    # Il raggruppamento applicativo mantiene lo stesso risultato su MongoDB e
    # sul database locale mongomock, che non implementa $substrBytes.
    app_sales, imported_sales = await asyncio.gather(
        db.vendite.find({}, {"_id": 0, "data": 1, "importo": 1}).to_list(None),
        db.db_storico_vend.find({}, {"_id": 0, "data": 1, "importo": 1}).to_list(None),
    )

    totals_by_date: Dict[Any, float] = {}
    for sale in [*imported_sales, *app_sales]:
        parsed_day = _parse_sale_date(sale.get("data"))
        if parsed_day is None:
            continue
        try:
            amount = float(sale.get("importo") or 0)
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
        series.append({"data": current_date.isoformat(), "importo": round(totals_by_date.get(current_date, 0), 2)})

    latest_total = round(totals_by_date.get(end_date, 0), 2)
    previous_values = [point["importo"] for point in series[:-1]]
    previous_average = sum(previous_values) / len(previous_values) if previous_values else 0
    variation = round(((latest_total - previous_average) / previous_average) * 100, 1) if previous_average else None
    return {
        "ultimo_giorno": end_date.isoformat(),
        "totale_ultimo_giorno": latest_total,
        "variazione_pct": variation,
        "totale_periodo": round(sum(point["importo"] for point in series), 2),
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
    venduto_negozio_excel = float(import_totali.get("valore_venduto_negozio_excel") or 0)
    venduto_vending_excel = float(import_totali.get("valore_venduto_vending_excel") or 0)
    venduto_totale_excel = float(import_totali.get("valore_venduto_totale_excel") or 0)
    if venduto_totale_excel:
        pv["kpi"]["valore_venduto"] = round(venduto_totale_excel, 2)
        pv["kpi"]["valore_venduto_negozio_excel"] = round(venduto_negozio_excel, 2)
        pv["kpi"]["valore_venduto_vending_excel"] = round(venduto_vending_excel, 2)
        pv["kpi"]["valore_venduto_totale_excel"] = round(venduto_totale_excel, 2)
        pv["kpi"]["margine_lordo"] = round(venduto_totale_excel - (pv["kpi"].get("valore_acquistato") or 0), 2)
    # vendite oggi
    oggi = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    vendite_oggi = await db.vendite.aggregate([
        {"$match": {"data": {"$regex": f"^{oggi}"}}},
        {"$group": {"_id": None, "tot": {"$sum": "$importo"}, "pezzi": {"$sum": "$quantita"}}}
    ]).to_list(1)
    v_oggi = vendite_oggi[0] if vendite_oggi else {"tot": 0, "pezzi": 0}
    versamenti, prelievi, scontrini, giacenza_vending, venduto_negozio_app_contanti = await asyncio.gather(
        _versamenti_summary(),
        _prelievi_vending_summary(),
        _scontrini_vending_summary(),
        _vending_cash_balance(),
        _supplemental_store_cash_sales(
            ultima_vendita_importata,
            (ultimo_import_contabile or {}).get("created_at"),
        ),
    )
    venduto_negozio_contabilizzato = round(venduto_negozio_excel + venduto_negozio_app_contanti, 2)
    liquidita_residua = round(venduto_negozio_contabilizzato - versamenti["totale"], 2)
    saldi, andamento_vendite = await asyncio.gather(
        _dashboard_balances(
            liquidita_residua,
            giacenza_vending,
            scontrini["totale"],
            (ultimo_import_contabile or {}).get("created_at"),
        ),
        _dashboard_sales_trend(),
    )
    # vending
    vending = await list_vending()
    vend_da_caricare = sum(1 for v in vending if (v.get("proposta") or 0) > 0)
    return {
        "kpi": pv["kpi"],
        "vendite_oggi": {"importo": round(v_oggi.get("tot") or 0, 2), "pezzi": v_oggi.get("pezzi") or 0},
        "totale_versamenti": versamenti["totale"],
        "totale_prelievi": prelievi["totale"],
        "totale_scontrini_vending": scontrini["totale"],
        "giacenza_vending_contanti": giacenza_vending,
        "venduto_negozio_excel": round(venduto_negozio_excel, 2),
        "venduto_negozio_app_contanti": venduto_negozio_app_contanti,
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
                "prezzo": float(row[7] or 0),
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
    valore_venduto_negozio_excel = 0.0
    valore_venduto_vending_excel = 0.0
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
            prezzo = float(row[7] or 0)
            venduti_negozio = int(row[3] or 0)
            venduti_vending = int(row[17] or 0)
            valore_venduto_negozio_excel += venduti_negozio * prezzo
            valore_venduto_vending_excel += venduti_vending * prezzo
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
    result["valore_venduto_negozio_excel"] = round(valore_venduto_negozio_excel, 2)
    result["valore_venduto_vending_excel"] = round(valore_venduto_vending_excel, 2)
    result["valore_venduto_totale_excel"] = round(
        valore_venduto_negozio_excel + valore_venduto_vending_excel,
        2,
    )
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
                "prezzo": float(row[2] or 0),
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
    hidden_rows, smart_rows, products = await asyncio.gather(
        db.smart_venue_hidden.find({}, {"_id": 0, "product_id": 1}).to_list(5000),
        db.smart_venue.find({}, {"_id": 0}).sort("codice", 1).to_list(5000),
        db.prodotti.find({}, {"_id": 0}).sort("codice", 1).to_list(5000),
    )
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
        rows.append({
            "id": row_id,
            "codice": code,
            "descrizione": str(smart_row.get("descrizione") or product.get("descrizione") or ""),
            # Acquistati e giacenze appartengono al prodotto reale. Il foglio
            # SMART VENUE fornisce soltanto la sua fotografia di confronto.
            "acquistati": int(product.get("acquistati", 0) or 0),
            "rimanenze": rimanenze,
            "smart_venue": smart_venue,
            "barcode": str(
                smart_row.get("barcode", smart_row.get("codice_smart", "")) or ""
            ),
            "differenza": smart_venue - rimanenze,
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
    for field in ("acquistati", "giacenza_negozio", "giacenza_vending", "smart_venue"):
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
            giacenza_vending=body.giacenza_vending,
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
    await db.smart_venue.insert_one(smart_row)
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
    rimanenze = int(product.get("giacenza_negozio", 0) or 0) + int(product.get("giacenza_vending", 0) or 0)
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
                prezzo=float(row[5] or 0),
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
                "importo": float(value_at(row, "importo", 0) or 0),
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

        if requested.is_file():
            return FileResponse(requested)

        return FileResponse(build_root / "index.html")

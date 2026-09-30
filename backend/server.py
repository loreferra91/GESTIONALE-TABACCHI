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


class AutoOrderConfermaIn(BaseModel):
    idempotency_key: Optional[str] = None
    batch_key: Optional[str] = None


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
]


# ------------------------- Seed -------------------------
DEFAULT_PARAMS = {
    "LOTTO_SIGARETTE": {"valore": 10, "descrizione": "Lotto standard per SIGARETTE"},
    "LOTTO_ELETTRONICHE": {"valore": 5, "descrizione": "Lotto standard per prodotti da inalazione (TEREA e sigarette elettroniche)"},
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
            db.listino_adm.create_index("adm_codice"),
            db.listino_adm.create_index("categoria_adm"),
            db.adm_sync.create_index("created_at"),
        )
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
    if categoria == "SIGARETTE":
        return int(params.get("LOTTO_SIGARETTE", 10))
    if categoria in (
        "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE",
        "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE ELETTRONICA",
    ):
        return int(params.get("LOTTO_ELETTRONICHE", 5))
    return int(params.get("LOTTO_ACCESSORI", 1))


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


@api.post("/prodotti")
async def create_prodotto(p: ProdottoIn):
    prod = Prodotto(**p.model_dump())
    await db.prodotti.insert_one(prod.model_dump())
    return prod.model_dump()


@api.put("/prodotti/{prod_id}")
async def update_prodotto(prod_id: str, p: ProdottoIn):
    r = await db.prodotti.update_one({"id": prod_id}, {"$set": p.model_dump()})
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


class BulkVenditaIn(BaseModel):
    canale: str = "NEGOZIO"
    pagamento: str = "CONTANTI"
    righe: List[Dict[str, Any]]  # {data, codice, descrizione, quantita, importo}


@api.post("/vendite/bulk")
async def bulk_vendite(body: BulkVenditaIn):
    """Bulk paste da Excel: rows with data, codice, descrizione, quantita, importo."""
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
            v = VenditaGiornaliera(data=data, codice=codice, descrizione=desc, quantita=qta, importo=imp, canale=body.canale, pagamento=body.pagamento)
            await db.vendite.insert_one(v.model_dump())
            inserted += 1
        except Exception as e:
            errors.append({"riga": i + 1, "errore": str(e)})
    return {"inseriti": inserted, "saltati": skipped, "errori": errors}


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
    await db.backup_snapshots.insert_one(meta)
    return meta


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


@api.post("/backup/{snapshot_id}/restore")
async def backup_restore(snapshot_id: str):
    meta = await db.backup_snapshots.find_one({"id": snapshot_id}, {"_id": 0})
    if not meta:
        raise HTTPException(404, "backup non trovato")
    restore_backup = await create_backup_snapshot(
        f"Prima del ripristino {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}",
        "pre-restore",
    )
    for collection_name in BACKUP_COLLECTIONS:
        items = await db.backup_snapshot_items.find(
            {"snapshot_id": snapshot_id, "collection": collection_name},
            {"_id": 0, "doc": 1},
        ).to_list(25000)
        await db[collection_name].delete_many({})
        docs = [item["doc"] for item in items]
        if docs:
            await db[collection_name].insert_many(docs)
    await db.backup_snapshots.update_one(
        {"id": snapshot_id},
        {"$set": {"last_restored_at": datetime.now(timezone.utc).isoformat()}},
    )
    return {"ok": True, "restored": snapshot_id, "pre_restore_backup": restore_backup["id"]}


async def record_import_history(file_name: str, report: Dict[str, Any], backup_id: Optional[str] = None):
    totals = report.get("totali", {})
    doc = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "file": file_name,
        "backup_id": backup_id,
        "fogli_trovati": report.get("fogli_trovati", []),
        "fogli_mancanti": report.get("fogli_mancanti", []),
        "totali": totals,
        "errori": sum((v or {}).get("errori", 0) for v in (report.get("dettaglio") or {}).values() if isinstance(v, dict)),
    }
    await db.import_history.insert_one(doc)
    return doc


@api.get("/import/history")
async def import_history(limit: int = 20):
    capped_limit = max(1, min(int(limit or 20), 100))
    return await db.import_history.find({}, {"_id": 0}).sort("created_at", -1).limit(capped_limit).to_list(capped_limit)


async def data_status_payload() -> Dict[str, Any]:
    latest_app, latest_imported, counts, latest_import, latest_backup = await asyncio.gather(
        db.vendite.find_one({}, {"_id": 0, "data": 1}, sort=[("data", -1)]),
        db.db_storico_vend.find_one({}, {"_id": 0, "data": 1}, sort=[("data", -1)]),
        asyncio.gather(*[db[name].count_documents({}) for name in BACKUP_COLLECTIONS]),
        db.import_history.find_one({}, {"_id": 0}, sort=[("created_at", -1)]),
        db.backup_snapshots.find_one({}, {"_id": 0}, sort=[("created_at", -1)]),
    )

    count_map = dict(zip(BACKUP_COLLECTIONS, counts))

    def date_only(raw: Any) -> Optional[str]:
        value = (raw or {}).get("data") if isinstance(raw, dict) else raw
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return str(value)[:10]

    latest_sales_day = max([d for d in [date_only(latest_app), date_only(latest_imported)] if d], default=None)
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
    summary = {
        "alta": sum(1 for i in items if i["severita"] == "alta"),
        "media": sum(1 for i in items if i["severita"] == "media"),
        "bassa": sum(1 for i in items if i["severita"] == "bassa"),
        "totale": len(items),
    }
    return {"summary": summary, "items": items}


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


@api.post("/vendite/import-csv-vending")
async def import_csv_vending(file: UploadFile = File(...), pagamento: str = "CONTANTI"):
    """Import CSV del distributore vending. Colonne accettate (case-insensitive):
       data, prodotto/nome prodotto, prezzo, colonna, codice/codice aams, categoria, pagamento.
       Separatore auto-rilevato (, ; \\t).
    """
    import csv
    raw = (await _read_capped(file)).decode("utf-8-sig", errors="replace")
    # rileva delimitatore
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

    inserted = 0
    skipped = 0
    errors = []
    for i, row in enumerate(reader):
        try:
            codice = str(pick(row, "codice", "codice aams", "cod aams", "cod", "aams") or "").strip()
            nome = str(pick(row, "nome prodotto", "prodotto", "descrizione", "articolo") or "").strip()
            prezzo = pick(row, "prezzo", "importo")
            data = pick(row, "data", "date")
            colonna = str(pick(row, "colonna", "column") or "").strip()
            categoria = str(pick(row, "categoria", "tipo") or "").strip()
            pag = str(pick(row, "pagamento", "payment") or pagamento).strip() or pagamento

            if not codice and not nome:
                skipped += 1
                continue
            prezzo_f = float(str(prezzo).replace(",", ".")) if prezzo not in (None, "") else 0.0
            # data: default oggi se mancante
            data_iso = str(data) if data else datetime.now(timezone.utc).isoformat()
            # se codice manca prova a risolvere dal nome
            if not codice and nome:
                p = await db.prodotti.find_one({"descrizione": {"$regex": f"^{re.escape(nome)}$", "$options": "i"}})
                if p:
                    codice = p["codice"]
            if not codice:
                # crea un placeholder
                codice = f"CSV-{norm(nome)[:20].replace(' ', '_')}"

            # ogni riga CSV = 1 pezzo venduto (formato tipico distributore)
            v = VenditaGiornaliera(
                data=data_iso, codice=codice, descrizione=nome, quantita=1, importo=prezzo_f,
                canale="VENDING", pagamento=pag,
            )
            await db.vendite.insert_one(v.model_dump())

            # aggiorna vending column giacenza se colonna presente
            if colonna:
                col = await db.vending.find_one({"colonna": colonna})
                if col:
                    new_g = max(0, (col.get("giacenza") or 0) - 1)
                    await db.vending.update_one({"colonna": colonna}, {"$set": {"giacenza": new_g}})
            # aggiorna prodotto
            prod = await db.prodotti.find_one({"codice": codice})
            if prod:
                await db.prodotti.update_one({"codice": codice}, {"$inc": {"giacenza_vending": -1, "venduti_vending": 1}})
            else:
                # crea prodotto minimale
                await db.prodotti.insert_one(Prodotto(
                    codice=codice, descrizione=nome or codice,
                    categoria=(categoria.upper() or "SIGARETTE") if categoria else "SIGARETTE",
                    prezzo=prezzo_f, venduti_vending=1,
                ).model_dump())
            inserted += 1
        except Exception as e:
            errors.append({"riga": i + 2, "errore": str(e)})
    return {"inseriti": inserted, "saltati": skipped, "errori": errors, "delimitatore": delim}




# ------------------------- Vending -------------------------
@api.get("/vending")
async def list_vending():
    docs, prodotti = await asyncio.gather(
        db.vending.find({}, {"_id": 0}).sort("colonna", 1).to_list(500),
        db.prodotti.find({}, {"_id": 0, "codice": 1, "giacenza_negozio": 1}).to_list(5000),
    )
    disponibilita_per_codice = {
        p.get("codice"): max(0, int(p.get("giacenza_negozio", 0) or 0))
        for p in prodotti
        if p.get("codice")
    }
    # arricchisci con esito/proposta
    out = []
    for d in docs:
        cap = d.get("capacita_max", 5) or 5
        giac = d.get("giacenza", 0) or 0
        soglia = d.get("soglia_minima", 2) or 2
        fabbisogno = max(0, cap - giac) if giac < soglia else 0
        disponibile = disponibilita_per_codice.get(d.get("codice"), 0)
        proposta = min(fabbisogno, disponibile)
        # esito
        if giac >= cap:
            esito = "PIENO" if giac == cap else "OLTRE CAPACITA"
        elif giac < soglia:
            if disponibile <= 0:
                esito = "MAGAZZINO ESAURITO"
            elif proposta < fabbisogno:
                esito = "DA CARICARE PARZIALE"
            else:
                esito = "DA CARICARE"
        else:
            esito = "OK"
        d["giacenza_magazzino"] = disponibile
        d["fabbisogno"] = fabbisogno
        d["proposta"] = proposta
        d["esito"] = esito
        out.append(d)
    return out


@api.put("/vending/{v_id}")
async def update_vending(v_id: str, body: Dict[str, Any]):
    allowed = {k: body[k] for k in ("codice", "descrizione", "giacenza", "capacita_max", "soglia_minima") if k in body}
    r = await db.vending.update_one({"id": v_id}, {"$set": allowed})
    if r.matched_count == 0:
        raise HTTPException(404, "not found")
    doc = await db.vending.find_one({"id": v_id}, {"_id": 0})
    return doc


@api.post("/vending/{v_id}/ricarica")
async def ricarica_vending(v_id: str, body: Dict[str, Any]):
    try:
        qta = int(body.get("quantita", 0))
    except (TypeError, ValueError):
        raise HTTPException(422, "quantita non valida")
    if qta <= 0:
        raise HTTPException(422, "quantita deve essere maggiore di zero")
    v = await db.vending.find_one({"id": v_id})
    if not v:
        raise HTTPException(404, "not found")
    giacenza = int(v.get("giacenza", 0) or 0)
    capacita = int(v.get("capacita_max", 0) or 0)
    codice = v.get("codice")
    prodotto = await db.prodotti.find_one({"codice": codice}) if codice else None
    disponibile = max(0, int((prodotto or {}).get("giacenza_negozio", 0) or 0))
    if disponibile <= 0:
        raise HTTPException(409, "Magazzino negozio esaurito: impossibile ricaricare la vending")
    qta_caricata = min(qta, max(0, capacita - giacenza), disponibile)
    nuovo = giacenza + qta_caricata
    await db.vending.update_one({"id": v_id}, {"$set": {"giacenza": nuovo}})
    # scala dal magazzino negozio
    if codice and qta_caricata:
        await db.prodotti.update_one({"codice": codice}, {"$inc": {"giacenza_negozio": -qta_caricata, "giacenza_vending": qta_caricata}})
    return {
        "ok": True,
        "colonna": v["colonna"],
        "nuova_giacenza": nuovo,
        "quantita_caricata": qta_caricata,
        "giacenza_magazzino_residua": disponibile - qta_caricata,
    }


@api.get("/vending/ricarica-pdf")
async def vending_ricarica_pdf():
    """Genera un PDF con SOLO le colonne da caricare (esito DA CARICARE)."""
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
        data = [["COLONNA", "CODICE", "ARTICOLO", "VENDING", "MAGAZZINO", "CAPACITÀ", "DA CARICARE"]]
        for r in da_caricare:
            data.append([
                r["colonna"],
                r.get("codice", ""),
                (r.get("descrizione") or "")[:45],
                str(r.get("giacenza", 0)),
                str(r.get("giacenza_magazzino", 0)),
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




# ------------------------- Cassa -------------------------
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
    "LOTTO_SIGARETTE": (1.0, 10000.0, "1 ≤ lotto"),
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
    header_key = idempotency_key if isinstance(idempotency_key, str) else None
    batch_key = (header_key or (body.idempotency_key if body else None) or (body.batch_key if body else None) or ao["snapshot_key"]).strip()
    if not batch_key:
        batch_key = ao["snapshot_key"]
    existing = await db.ordini_fornitore.find_one({"batch_key": batch_key}, {"_id": 0})
    if existing:
        return {**existing, "duplicate": True}

    righe = [
        r for r in ao["righe"]
        if r.get("stato") == "ORDINA ORA" and not r.get("anomalia") and int(r.get("qta_da_ordinare") or 0) > 0
    ]
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
            await db.ordini_fornitore.insert_one(batch)
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


# ------------------------- Auto-Order PDF -------------------------
@api.get("/auto-order/pdf")
async def auto_order_pdf(
    fornitore: Optional[str] = "Fornitore",
    categoria: Optional[str] = None,
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
    righe = ao["righe"]
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
    header = ["CODICE", "ARTICOLO", "TIPO", "MAG.", "V10/30", "COP.", "QTA", "TOTALE", "MOTIVO"]
    data = [header]
    for r in righe:
        data.append([
            r["codice"],
            Paragraph(xml_escape(r["descrizione"] or ""), cell_s),
            (r["categoria"] or "")[:3],
            str(r.get("magazzino_reale_lordo", r.get("magazzino_reale", 0))),
            f"{r.get('venduto_breve', r.get('venduto_10gg', r.get('venduto_periodo', 0)))}/{r.get('venduto_lungo', r.get('venduto_30gg', 0))}",
            "-" if r.get("copertura_gg") is None else f"{r['copertura_gg']:.2f}",
            str(r["qta_da_ordinare"]),
            f"€ {r['totale']:.2f}",
            Paragraph(xml_escape(r["motivo"] or ""), cell_s),
        ])
    data.append(["", "", "", "", "", "", "TOTALE", f"€ {totale:.2f}", ""])

    col_widths = [19*mm, 49*mm, 10*mm, 12*mm, 16*mm, 12*mm, 12*mm, 20*mm, 30*mm]
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0F172A')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 8),
        ('ALIGN', (0,0), (-1,0), 'LEFT'),
        ('ALIGN', (3,1), (7,-1), 'RIGHT'),
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
    prods = await db.prodotti.find({}, {"_id": 0}).to_list(5000)
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
@api.get("/dashboard")
async def dashboard():
    pv = await pivot()
    # vendite oggi
    oggi = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    vendite_oggi = await db.vendite.aggregate([
        {"$match": {"data": {"$regex": f"^{oggi}"}}},
        {"$group": {"_id": None, "tot": {"$sum": "$importo"}, "pezzi": {"$sum": "$quantita"}}}
    ]).to_list(1)
    v_oggi = vendite_oggi[0] if vendite_oggi else {"tot": 0, "pezzi": 0}
    # cassa
    cassa = await list_cassa()
    # vending
    vending = await list_vending()
    vend_da_caricare = sum(1 for v in vending if v["esito"] == "DA CARICARE")
    return {
        "kpi": pv["kpi"],
        "vendite_oggi": {"importo": round(v_oggi.get("tot") or 0, 2), "pezzi": v_oggi.get("pezzi") or 0},
        "saldo_cassa": cassa["saldo"],
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


async def _import_prodotti(ws) -> Dict[str, int]:
    operations = []
    err = 0
    for row in ws.iter_rows(min_row=3, values_only=True):
        try:
            codice = row[0]
            if not codice:
                continue
            codice_s = str(codice).strip()
            desc = str(row[1] or "").strip()
            data_p = {
                "codice": codice_s,
                "descrizione": desc,
                "categoria": _cat_from_desc(desc, codice_s),
                "acquistati": int(row[2] or 0),
                "venduti_negozio": int(row[3] or 0),
                "prezzo": float(row[7] or 0),
                "giacenza_vending": int(row[13] or 0),
                "venduti_vending": int(row[17] or 0),
            }
            data_p["giacenza_negozio"] = physical_shop_stock_from_excel(
                row[6], data_p["giacenza_vending"], data_p["venduti_vending"]
            )
            operations.append(UpdateOne({"codice": codice_s}, {"$set": data_p}, upsert=True))
        except Exception:
            err += 1
    result = await _bulk_upsert(db.prodotti, operations)
    result["errori"] += err
    result["elettroniche"] = await _apply_electronic_inhalation_categories()
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
        except Exception:
            err += 1
    result = await _bulk_upsert(db.vending, operations)
    result["errori"] += err
    return result


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


@api.post("/import/excel-full")
async def import_excel_full(file: UploadFile = File(...)):
    """Import multi-sheet: RIEP_VENDITA + LISTINO ADM + RICARICA VENDING + STORICO_ORDINI + PARAMETRI.
    Excel = fonte di verità (upsert). Righe DB non presenti nell'Excel sono conservate.
    Parametri custom (non presenti nel foglio PARAMETRI) sono preservati.
    """
    try:
        import openpyxl
    except Exception:
        raise HTTPException(500, "openpyxl non installato")
    content = await _read_capped(file)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, keep_vba=False)
    except Exception as e:
        raise HTTPException(422, f"File non leggibile: {e}")

    backup = await create_backup_snapshot(
        f"Prima import {file.filename or 'Excel'}",
        "pre-import",
    )

    fogli_trovati: List[str] = []
    fogli_mancanti: List[str] = []
    report: Dict[str, Any] = {}

    async def _run(name: str, importer):
        if name in wb.sheetnames:
            fogli_trovati.append(name)
            report[name] = await importer(wb[name])
        else:
            fogli_mancanti.append(name)

    await _run("RIEP_VENDITA", _import_prodotti)
    await _run("LISTINO ADM", _import_listino)
    await _run("RICARICA VENDING", _import_vending)
    await _run("STORICO_ORDINI", _import_storico)
    await _run("PARAMETRI", _import_parametri)
    await _run("DB_STORICO_VEND", _import_db_storico_vend)
    await _run("DB_STORICO_VENDING_EXT", _import_db_storico_vending_ext)

    totali = {
        "prodotti_inseriti": report.get("RIEP_VENDITA", {}).get("inseriti", 0),
        "prodotti_aggiornati": report.get("RIEP_VENDITA", {}).get("aggiornati", 0),
        "listino_inseriti": report.get("LISTINO ADM", {}).get("inseriti", 0),
        "listino_aggiornati": report.get("LISTINO ADM", {}).get("aggiornati", 0),
        "vending_inseriti": report.get("RICARICA VENDING", {}).get("inseriti", 0),
        "vending_aggiornati": report.get("RICARICA VENDING", {}).get("aggiornati", 0),
        "storico_ricreato": report.get("STORICO_ORDINI", {}).get("inseriti", 0),
        "parametri_aggiornati": report.get("PARAMETRI", {}).get("aggiornati", 0),
        "parametri_saltati": report.get("PARAMETRI", {}).get("saltati", 0),
        "db_storico_vend_righe": report.get("DB_STORICO_VEND", {}).get("inseriti", 0),
        "db_storico_vending_ext_righe": report.get("DB_STORICO_VENDING_EXT", {}).get("inseriti", 0),
    }
    response = {
        "ok": True,
        "file": file.filename,
        "backup_id": backup["id"],
        "fogli_trovati": fogli_trovati,
        "fogli_mancanti": fogli_mancanti,
        "dettaglio": report,
        "totali": totali,
    }
    await record_import_history(file.filename or "Excel", response, backup["id"])
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

"""ADM price-list discovery, parsing, synchronization, and read routes."""
import asyncio
from datetime import datetime, timezone
import gc
import re
import tempfile
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo import UpdateMany, UpdateOne

try:
    from .money import NonNegativeMoney
except ImportError:  # pragma: no cover - direct module execution compatibility
    from money import NonNegativeMoney


ADM_PREZZI_URL = "https://www.adm.gov.it/portale/monopoli/tabacchi/prezzi/prezzi_pubblico"
ADM_CATEGORY_ALIASES = {
    "SIGARETTE": ["sigarette"],
    "SIGARI": ["sigari"],
    "SIGARETTI": ["sigaretti"],
    "FIUTO E MASTICO": ["fiuto", "mastico"],
    "TRINCIATI PER SIGARETTA": ["trinciati", "ryo"],
    "ALTRI TABACCHI DA FUMO": ["altri tabacchi"],
    "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE": [
        "inalazione senza combustione",
        "prodotti da inalazione",
    ],
}


class ListinoItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codice: str
    descrizione: str
    prezzo: NonNegativeMoney = 0
    confezione: str = ""


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


def fetch_adm_categories_sync() -> List[Dict[str, Any]]:
    import requests
    from bs4 import BeautifulSoup

    response = requests.get(ADM_PREZZI_URL, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    categories: Dict[str, Dict[str, Any]] = {}
    page_text = " ".join(soup.get_text(" ", strip=True).split())
    for category, aliases in ADM_CATEGORY_ALIASES.items():
        for anchor in soup.find_all("a"):
            text = " ".join(anchor.get_text(" ", strip=True).split())
            href = anchor.get("href") or ""
            haystack = f"{text} {href}".lower()
            if not href or ".pdf" not in href.lower():
                continue
            if any(alias.lower() in haystack for alias in aliases):
                updated_match = re.search(
                    rf"{re.escape(text)}.*?aggiornato il\s+(\d{{2}}/\d{{2}}/\d{{4}})",
                    page_text,
                    re.I,
                )
                categories[category] = {
                    "categoria": category,
                    "titolo": text or category.title(),
                    "url": urljoin(ADM_PREZZI_URL, href),
                    "aggiornato_il": updated_match.group(1) if updated_match else None,
                }
                break
    return [categories[key] for key in ADM_CATEGORY_ALIASES if key in categories]


def parse_adm_pdf_sync(category: Dict[str, Any]) -> List[Dict[str, Any]]:
    import pdfplumber
    import requests

    rows: List[Dict[str, Any]] = []
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
                        for table in page.extract_tables() or []:
                            if not table:
                                continue
                            for row in table[1:]:
                                if not row or len(row) < 4:
                                    continue
                                code = adm_numeric_code(row[0])
                                if not code or not code.isdigit():
                                    continue
                                description = " ".join(
                                    str(row[1] or "").replace("\n", " ").split()
                                )
                                packaging = (
                                    " ".join(str(row[2] or "").replace("\n", " ").split())
                                    if len(row) > 2
                                    else ""
                                )
                                if not description:
                                    continue
                                rows.append({
                                    "adm_codice": code,
                                    "codice": adm_local_code(code),
                                    "descrizione": description,
                                    "confezione": packaging,
                                    "prezzo": parse_italian_money(row[-1]),
                                    "categoria_adm": category["categoria"],
                                    "fonte": "ADM",
                                    "adm_pdf_url": category["url"],
                                    "adm_aggiornato_il": category.get("aggiornato_il"),
                                })
                    finally:
                        page.close()
    return rows


def _adm_product_update_operations(rows: List[Dict[str, Any]], product_codes: set[str]):
    """Build ADM product updates only for codes present in the catalogue."""
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


def create_adm_router(
    *,
    get_database,
    cap_limit,
    fetch_categories,
    parse_pdf,
    bulk_upsert,
    bulk_update,
    apply_electronic_inhalation_categories,
):
    router = APIRouter()

    @router.get("/listino")
    async def list_listino(q: Optional[str] = None, limit: int = 300):
        filt: Dict[str, Any] = {}
        if q:
            filt["$or"] = [
                {"codice": {"$regex": re.escape(q[:200]), "$options": "i"}},
                {"descrizione": {"$regex": re.escape(q[:200]), "$options": "i"}},
            ]
        capped_limit = cap_limit(limit)
        database = get_database()
        docs = await database.listino_adm.find(filt, {"_id": 0}).sort(
            "descrizione", 1
        ).limit(capped_limit).to_list(capped_limit)
        total = await database.listino_adm.count_documents(filt)
        return {"items": docs, "total": total}

    @router.get("/adm/categories")
    async def adm_categories():
        categories = await asyncio.to_thread(fetch_categories)
        last_sync = await get_database().adm_sync.find_one(
            {}, {"_id": 0}, sort=[("created_at", -1)]
        )
        return {"source": ADM_PREZZI_URL, "categories": categories, "last_sync": last_sync}

    @router.post("/adm/sync")
    async def adm_sync():
        started = datetime.now(timezone.utc).isoformat()
        categories = await asyncio.to_thread(fetch_categories)
        if not categories:
            raise HTTPException(502, "Nessun PDF ADM trovato nella pagina ufficiale")

        database = get_database()
        total_rows = inserted = updated = product_updates = 0
        detail = []
        product_codes = {
            str(product.get("codice") or "").strip().upper()
            async for product in database.prodotti.find({}, {"_id": 0, "codice": 1})
            if product.get("codice")
        }

        # Sequential extraction keeps memory below the Render Free limit.
        for category in categories:
            rows = await asyncio.to_thread(parse_pdf, category)
            total_rows += len(rows)
            operations = []
            for item in rows:
                insert_defaults = ListinoItem(
                    codice=item["codice"],
                    descrizione=item["descrizione"],
                    prezzo=item["prezzo"],
                    confezione=item["confezione"],
                ).model_dump()
                operations.append(UpdateOne(
                    {"$or": [
                        {"adm_codice": item["adm_codice"]},
                        {"codice": item["codice"]},
                        {"codice": item["adm_codice"]},
                    ]},
                    {"$set": {**item, "updated_at": started}, "$setOnInsert": insert_defaults},
                    upsert=True,
                ))
            result = await bulk_upsert(database.listino_adm, operations)
            inserted += result.get("inseriti", 0)
            updated += result.get("aggiornati", 0)
            product_operations = _adm_product_update_operations(rows, product_codes)
            product_result = await bulk_update(database.prodotti, product_operations)
            product_updates += product_result["modificati"]
            detail.append({
                **category,
                "righe": len(rows),
                **result,
                "prodotti_trovati": product_result["trovati"],
                "prodotti_aggiornati": product_result["modificati"],
            })
            del rows, operations, product_operations
            gc.collect()

        await database.prodotti.update_many(
            {"$or": [
                {"categoria_adm": {"$exists": False}},
                {"categoria_adm": ""},
                {"categoria_adm": None},
            ]},
            {"$set": {"categoria": "ACCESSORI"}},
        )
        await apply_electronic_inhalation_categories()
        linked_products = await database.prodotti.count_documents(
            {"categoria_adm": {"$exists": True, "$ne": ""}}
        )
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
        await database.adm_sync.insert_one(sync_doc.copy())
        return sync_doc

    return router, list_listino, adm_categories, adm_sync

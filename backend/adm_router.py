"""ADM price-list discovery, parsing, synchronization, and read routes."""
import asyncio
from datetime import datetime, timedelta, timezone
import gc
import hashlib
import json
import re
import tempfile
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo import UpdateMany, UpdateOne

try:
    from .fit_tariff import iso_date_from_adm
except ImportError:  # pragma: no cover - direct module execution compatibility
    from fit_tariff import iso_date_from_adm

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


class FitApplyRequest(BaseModel):
    preview_id: str = Field(min_length=1, max_length=100)


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
    for category, aliases in ADM_CATEGORY_ALIASES.items():
        for anchor in soup.find_all("a"):
            text = " ".join(anchor.get_text(" ", strip=True).split())
            href = anchor.get("href") or ""
            haystack = f"{text} {href}".lower()
            if not href or ".pdf" not in href.lower():
                continue
            if any(alias.lower() in haystack for alias in aliases):
                context = " ".join(anchor.parent.get_text(" ", strip=True).split())
                updated_match = re.search(
                    r"aggiornato\s+(?:il|al)\s+(\d{2}/\d{2}/\d{4})",
                    context,
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
                # The ADM retail price is also the price used by the product
                # catalogue and sales workflows; keep both fields aligned.
                "prezzo": item["prezzo"],
                "adm_prezzo": item["prezzo"],
                "adm_aggiornato_il": item.get("adm_aggiornato_il"),
            }},
        ))
    return operations


def _catalogue_adm_code(product: Dict[str, Any]) -> str:
    explicit = str(product.get("adm_codice") or "").strip()
    if explicit:
        return adm_numeric_code(explicit)
    local_code = str(product.get("codice") or "").strip().upper()
    if local_code.startswith("AMMS"):
        return adm_numeric_code(local_code)
    return ""


def _catalogue_snapshot_hash(products: List[Dict[str, Any]]) -> str:
    relevant = [
        {
            "id": str(item.get("id") or ""),
            "codice": str(item.get("codice") or ""),
            "adm_codice": str(item.get("adm_codice") or ""),
            "prezzo": _money_value(item.get("prezzo")),
            "categoria_adm": str(item.get("categoria_adm") or ""),
        }
        for item in products
    ]
    relevant.sort(key=lambda item: (item["id"], item["codice"], item["adm_codice"]))
    encoded = json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _money_value(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


LISTINO_COMPARE_FIELDS = (
    "codice",
    "adm_codice",
    "descrizione",
    "categoria_adm",
    "confezione",
    "fonte",
    "prezzo",
)


def _listino_field_value(field: str, value: Any) -> Any:
    if field == "prezzo":
        return _money_value(value)
    return str(value or "").strip()


def _listino_preview(
    rows: List[Dict[str, Any]],
    existing_listino: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return an inspectable field-by-field diff for the tariff catalogue."""
    existing_by_code: Dict[str, List[Dict[str, Any]]] = {}
    for item in existing_listino:
        code = _catalogue_adm_code(item)
        if code:
            existing_by_code.setdefault(code, []).append(item)

    source_codes = {item["adm_codice"] for item in rows}
    changes = []
    inserted = updated = unchanged = 0
    backfilled_adm_codes = backfilled_categories = backfilled_sources = 0
    duplicate_codes = []

    for item in rows:
        code = item["adm_codice"]
        matches = existing_by_code.get(code, [])
        if len(matches) > 1:
            duplicate_codes.append(code)
        if not matches:
            inserted += 1
            changes.append({
                "stato": "NUOVO",
                "codice": item.get("codice"),
                "adm_codice": code,
                "descrizione": item.get("descrizione"),
                "categoria_adm": item.get("categoria_adm"),
                "campi": [
                    {
                        "campo": field,
                        "prima": None,
                        "dopo": _listino_field_value(field, item.get(field)),
                    }
                    for field in LISTINO_COMPARE_FIELDS
                    if _listino_field_value(field, item.get(field)) not in ("", 0.0)
                ],
            })
            continue

        current = matches[0]
        field_changes = []
        for field in LISTINO_COMPARE_FIELDS:
            before = _listino_field_value(field, current.get(field))
            after = _listino_field_value(field, item.get(field))
            if before == after:
                continue
            field_changes.append({"campo": field, "prima": before, "dopo": after})
            if field == "adm_codice" and not before and after:
                backfilled_adm_codes += 1
            elif field == "categoria_adm" and not before and after:
                backfilled_categories += 1
            elif field == "fonte" and not before and after:
                backfilled_sources += 1

        if not field_changes:
            unchanged += 1
            continue
        updated += 1
        changes.append({
            "stato": "AGGIORNATO",
            "codice": item.get("codice"),
            "adm_codice": code,
            "descrizione": item.get("descrizione"),
            "categoria_adm": item.get("categoria_adm"),
            "campi": field_changes,
        })

    local_only = sum(
        len(matches)
        for code, matches in existing_by_code.items()
        if code not in source_codes
    )
    return {
        "listino_nuovi": inserted,
        "listino_aggiornati": updated,
        "listino_invariati": unchanged,
        "listino_locali_non_presenti": local_only,
        "listino_codici_adm_compilati": backfilled_adm_codes,
        "listino_categorie_compilate": backfilled_categories,
        "listino_fonti_compilate": backfilled_sources,
        "listino_codici_duplicati": len(set(duplicate_codes)),
        "listino_variazioni": changes,
    }


def build_fit_preview(
    rows: List[Dict[str, Any]],
    products: List[Dict[str, Any]],
    existing_listino: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare a normalized tariff snapshot with the local catalogue."""
    row_by_code = {item["adm_codice"]: item for item in rows}
    products_by_code: Dict[str, List[Dict[str, Any]]] = {}
    for product in products:
        code = _catalogue_adm_code(product)
        if code:
            products_by_code.setdefault(code, []).append(product)

    linked = changed = unchanged = ambiguous = 0
    changes = []
    ambiguous_products = []
    for code, matches in products_by_code.items():
        tariff = row_by_code.get(code)
        if not tariff:
            continue
        if len(matches) != 1:
            ambiguous += len(matches)
            ambiguous_products.append({
                "adm_codice": code,
                "prodotti": [str(item.get("descrizione") or item.get("codice") or "") for item in matches],
            })
            continue
        product = matches[0]
        linked += 1
        old_price = _money_value(product.get("prezzo"))
        new_price = _money_value(tariff.get("prezzo"))
        if old_price == new_price:
            unchanged += 1
            continue
        changed += 1
        changes.append({
            "product_id": product.get("id"),
            "codice": product.get("codice"),
            "adm_codice": code,
            "descrizione": product.get("descrizione") or tariff.get("descrizione"),
            "categoria_adm": tariff.get("categoria_adm"),
            "prezzo_attuale": old_price,
            "prezzo_nuovo": new_price,
            "differenza": round(new_price - old_price, 2),
        })

    missing_products = []
    for code, matches in products_by_code.items():
        if code in row_by_code:
            continue
        for product in matches:
            missing_products.append({
                "product_id": product.get("id"),
                "codice": product.get("codice"),
                "adm_codice": code,
                "descrizione": product.get("descrizione"),
            })

    existing_codes = {
        adm_numeric_code(item.get("adm_codice") or item.get("codice"))
        for item in existing_listino
        if item.get("adm_codice") or item.get("codice")
    }
    new_official = [
        {
            "adm_codice": item["adm_codice"],
            "codice": item["codice"],
            "descrizione": item["descrizione"],
            "categoria_adm": item["categoria_adm"],
            "prezzo": item["prezzo"],
        }
        for item in rows
        if item["adm_codice"] not in existing_codes
    ]
    changes.sort(key=lambda item: (item["descrizione"] or "", item["adm_codice"]))
    missing_products.sort(key=lambda item: (item.get("descrizione") or "", item["adm_codice"]))
    return {
        "righe": len(rows),
        "prodotti_catalogo_adm": sum(len(items) for items in products_by_code.values()),
        "prodotti_collegati": linked,
        "prezzi_modificati": changed,
        "prezzi_invariati": unchanged,
        "collegamenti_ambigui": ambiguous,
        "prodotti_non_trovati": len(missing_products),
        "righe_fit_non_collegate": max(0, len(rows) - linked),
        "nuovi_articoli_listino": len(new_official),
        "variazioni": changes,
        "prodotti_mancanti": missing_products[:100],
        "ambigui": ambiguous_products[:100],
        "nuovi_articoli": new_official[:100],
        **_listino_preview(rows, existing_listino),
    }


def _fit_listino_update(
    item: Dict[str, Any], snapshot_hash: str, synced_at: str,
) -> Dict[str, Any]:
    """Build a conflict-free Mongo update for both matches and upserts."""
    insert_defaults = ListinoItem(
        codice=item["codice"],
        descrizione=item["descrizione"],
        prezzo=item["prezzo"],
        confezione=item.get("confezione", ""),
    ).model_dump()
    set_fields = {
        **item,
        "tariffa_snapshot_hash": snapshot_hash,
        "updated_at": synced_at,
    }
    insert_defaults = {
        key: value for key, value in insert_defaults.items() if key not in set_fields
    }
    update = {"$set": set_fields}
    if insert_defaults:
        update["$setOnInsert"] = insert_defaults
    return update


def _fit_product_update_operations(
    rows: List[Dict[str, Any]], products: List[Dict[str, Any]], synced_at: str,
    import_id: Optional[str] = None,
) -> List[UpdateOne]:
    row_by_code = {item["adm_codice"]: item for item in rows}
    products_by_code: Dict[str, List[Dict[str, Any]]] = {}
    for product in products:
        code = _catalogue_adm_code(product)
        if code:
            products_by_code.setdefault(code, []).append(product)

    operations = []
    for code, matches in products_by_code.items():
        item = row_by_code.get(code)
        if not item or len(matches) != 1:
            continue
        product = matches[0]
        product_filter = (
            {"id": product["id"]}
            if product.get("id")
            else {"codice": product.get("codice")}
        )
        update_fields = {
            "categoria": item["categoria_adm"],
            "categoria_adm": item["categoria_adm"],
            "adm_codice": item["adm_codice"],
            "adm_descrizione": item["descrizione"],
            "prezzo": item["prezzo"],
            "adm_prezzo": item["prezzo"],
            "tariffa_fonte": item.get("fonte", "FIT"),
            "tariffa_aggiornata_il": item.get("fit_effective_date") or item.get("adm_aggiornato_il"),
            "tariffa_sincronizzata_il": synced_at,
            "tariffa_import_id": import_id,
        }
        if item.get("fonte") == "FIT":
            update_fields.update({
                "fit_codice": item["adm_codice"],
                "fit_descrizione": item["descrizione"],
                "fit_prezzo": item["prezzo"],
                "fit_aggiornato_il": item.get("fit_effective_date"),
                "fit_source_url": item.get("fit_xls_url"),
            })
        operations.append(UpdateOne(product_filter, {"$set": update_fields}))
    return operations


def _tariff_snapshot_hash(rows: List[Dict[str, Any]]) -> str:
    normalized = [
        {
            "adm_codice": item.get("adm_codice"),
            "descrizione": item.get("descrizione"),
            "prezzo": _money_value(item.get("prezzo")),
            "categoria_adm": item.get("categoria_adm"),
            "fonte": item.get("fonte"),
        }
        for item in sorted(rows, key=lambda value: value.get("adm_codice", ""))
    ]
    return hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _exact_duplicate_listino_ids(
    existing_by_code: Dict[str, List[Dict[str, Any]]],
) -> tuple[List[Any], List[str]]:
    """Return safely removable duplicate row ids and conflicting ADM codes.

    Older ADM imports can have created the same visible tariff row twice.  An
    exact duplicate is safe to collapse once the caller has taken a backup;
    rows with different commercial values must still require manual review.
    """
    removable_ids: List[Any] = []
    conflicting_codes: List[str] = []
    comparison_fields = (
        "codice", "descrizione", "prezzo", "confezione", "categoria_adm", "fonte",
    )
    for code, matches in existing_by_code.items():
        if len(matches) < 2:
            continue
        signatures = {
            tuple(
                _money_value(item.get(field)) if field == "prezzo"
                else str(item.get(field) or "").strip()
                for field in comparison_fields
            )
            for item in matches
        }
        if len(signatures) != 1:
            conflicting_codes.append(code)
            continue
        removable_ids.extend(item["_id"] for item in matches[1:] if item.get("_id") is not None)
    return removable_ids, sorted(conflicting_codes)


def build_hybrid_tariff_snapshot_sync(
    fetch_fit_tariff, fetch_categories, parse_pdf, include_adm_overlays=True,
):
    """Use FIT XLS, replacing only categories for which ADM is demonstrably newer."""
    fit_document = fetch_fit_tariff()
    fit_date = fit_document.get("effective_date")
    rows = list(fit_document["rows"])
    adm_categories = fetch_categories()
    overlays = []
    warnings = []
    if not include_adm_overlays:
        newer = [
            category["categoria"]
            for category in adm_categories
            if iso_date_from_adm(category.get("aggiornato_il"))
            and fit_date
            and iso_date_from_adm(category.get("aggiornato_il")) > fit_date
        ]
        if newer:
            warnings.append(
                "PDF ADM piu recenti disponibili per: " + ", ".join(newer)
            )
        return {
            "rows": rows,
            "snapshot_hash": _tariff_snapshot_hash(rows),
            "fit": {key: value for key, value in fit_document.items() if key not in {"rows", "content"}},
            "adm_overlays": overlays,
            "warnings": warnings,
        }
    for category in adm_categories:
        adm_date = iso_date_from_adm(category.get("aggiornato_il"))
        if not adm_date or not fit_date or adm_date < fit_date:
            continue
        category_name = category["categoria"]
        fit_count = sum(1 for item in rows if item["categoria_adm"] == category_name)
        adm_rows = parse_pdf(category)
        minimum_expected = max(1, int(fit_count * 0.5))
        if len(adm_rows) < minimum_expected:
            warnings.append(
                f"{category_name}: ADM piu recente ma ha restituito soltanto {len(adm_rows)} righe"
            )
            continue
        rows = [item for item in rows if item["categoria_adm"] != category_name]
        rows.extend(adm_rows)
        overlays.append({
            "categoria": category_name,
            "aggiornato_il": category.get("aggiornato_il"),
            "righe_fit": fit_count,
            "righe_adm": len(adm_rows),
            "url": category.get("url"),
        })
    return {
        "rows": rows,
        "snapshot_hash": _tariff_snapshot_hash(rows),
        "fit": {key: value for key, value in fit_document.items() if key not in {"rows", "content"}},
        "adm_overlays": overlays,
        "warnings": warnings,
    }


def create_adm_router(
    *,
    get_database,
    cap_limit,
    fetch_categories,
    parse_pdf,
    fetch_fit_tariff,
    bulk_upsert,
    bulk_update,
    apply_electronic_inhalation_categories,
    create_backup_snapshot,
    record_audit_event,
):
    router = APIRouter()

    @router.get("/listino")
    async def list_listino(
        q: Optional[str] = None,
        categoria: Optional[str] = None,
        fonte: Optional[str] = None,
        limit: int = 100,
        skip: int = 0,
        sort: str = "descrizione",
        direction: str = "asc",
    ):
        filt: Dict[str, Any] = {}
        if q:
            filt["$or"] = [
                {"codice": {"$regex": re.escape(q[:200]), "$options": "i"}},
                {"adm_codice": {"$regex": re.escape(q[:200]), "$options": "i"}},
                {"descrizione": {"$regex": re.escape(q[:200]), "$options": "i"}},
            ]
        if categoria:
            filt["categoria_adm"] = categoria[:200]
        if fonte:
            filt["fonte"] = fonte[:30]
        capped_limit = cap_limit(limit)
        capped_skip = max(0, min(int(skip or 0), 1_000_000))
        sort_fields = {
            "codice", "adm_codice", "descrizione", "categoria_adm",
            "confezione", "prezzo", "fonte",
        }
        sort_field = sort if sort in sort_fields else "descrizione"
        sort_direction = -1 if direction.lower() == "desc" else 1
        database = get_database()
        docs = await database.listino_adm.find(filt, {"_id": 0}).sort([
            (sort_field, sort_direction),
            ("_id", 1),
        ]).skip(capped_skip).limit(capped_limit).to_list(capped_limit)
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

    @router.get("/fit/status")
    async def fit_status():
        try:
            fit_document = await asyncio.to_thread(fetch_fit_tariff)
        except Exception as exc:
            raise HTTPException(502, f"Tariffario FIT non disponibile: {exc}") from exc
        categories = await asyncio.to_thread(fetch_categories)
        fit_date = fit_document.get("effective_date")
        newer_categories = [
            {
                "categoria": category["categoria"],
                "aggiornato_il": category.get("aggiornato_il"),
                "url": category.get("url"),
            }
            for category in categories
            if iso_date_from_adm(category.get("aggiornato_il"))
            and fit_date
            and iso_date_from_adm(category.get("aggiornato_il")) > fit_date
        ]
        last_sync = await get_database().fit_sync.find_one(
            {}, {"_id": 0}, sort=[("created_at", -1)]
        )
        return {
            "source": "FIT XLS con verifica ADM",
            "fit": {key: value for key, value in fit_document.items() if key not in {"rows", "content"}},
            "adm_newer_categories": newer_categories,
            "last_sync": last_sync,
        }

    @router.post("/fit/preview")
    async def fit_preview():
        try:
            snapshot = await asyncio.to_thread(
                build_hybrid_tariff_snapshot_sync,
                fetch_fit_tariff,
                fetch_categories,
                parse_pdf,
                False,
            )
        except Exception as exc:
            raise HTTPException(502, f"Impossibile preparare il tariffario: {exc}") from exc

        database = get_database()
        products = [
            product async for product in database.prodotti.find({}, {
                "_id": 0, "id": 1, "codice": 1, "adm_codice": 1,
                "descrizione": 1, "prezzo": 1, "categoria_adm": 1,
            })
        ]
        existing_listino = [
            item async for item in database.listino_adm.find(
                {}, {
                    "_id": 0,
                    "codice": 1,
                    "adm_codice": 1,
                    "descrizione": 1,
                    "prezzo": 1,
                    "confezione": 1,
                    "categoria_adm": 1,
                    "fonte": 1,
                }
            )
        ]
        summary = build_fit_preview(snapshot["rows"], products, existing_listino)
        now = datetime.now(timezone.utc)
        preview_id = str(uuid.uuid4())
        expires_at = now + timedelta(minutes=30)
        preview_document = {
            "id": preview_id,
            "created_at": now,
            "expires_at": expires_at,
            "status": "READY",
            "snapshot_hash": snapshot["snapshot_hash"],
            "catalogue_hash": _catalogue_snapshot_hash(products),
            "fit": snapshot["fit"],
            "adm_overlays": snapshot["adm_overlays"],
            "warnings": snapshot["warnings"],
            # The full row diff is returned to the browser, but need not be
            # duplicated inside the persisted preview (the normalized rows are
            # already stored here for the apply step).
            "summary": {
                key: value for key, value in summary.items()
                if key != "listino_variazioni"
            },
            "rows": snapshot["rows"],
        }
        previews = database.fit_tariff_previews
        await previews.delete_many({"expires_at": {"$lt": now}})
        await previews.insert_one(preview_document)
        return {
            "preview_id": preview_id,
            "expires_at": expires_at.isoformat(),
            "snapshot_hash": snapshot["snapshot_hash"],
            "catalogue_hash": preview_document["catalogue_hash"],
            "fit": snapshot["fit"],
            "adm_overlays": snapshot["adm_overlays"],
            "warnings": snapshot["warnings"],
            **summary,
        }

    @router.post("/fit/apply")
    async def fit_apply(body: FitApplyRequest):
        database = get_database()
        preview = await database.fit_tariff_previews.find_one(
            {"id": body.preview_id}, {"_id": 0}
        )
        if not preview:
            raise HTTPException(404, "Anteprima FIT non trovata: generarne una nuova")
        if preview.get("status") != "READY":
            raise HTTPException(409, "Questa anteprima FIT e gia stata applicata")
        expires_at = preview.get("expires_at")
        now = datetime.now(timezone.utc)
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < now:
                raise HTTPException(409, "Anteprima FIT scaduta: generarne una nuova")

        rows = preview.get("rows") or []
        if len(rows) < 100 or _tariff_snapshot_hash(rows) != preview.get("snapshot_hash"):
            raise HTTPException(409, "Anteprima FIT non integra: generarne una nuova")

        products = [
            product async for product in database.prodotti.find({}, {
                "_id": 0, "id": 1, "codice": 1, "adm_codice": 1,
                "descrizione": 1, "prezzo": 1, "categoria_adm": 1,
            })
        ]
        if _catalogue_snapshot_hash(products) != preview.get("catalogue_hash"):
            raise HTTPException(
                409,
                "Il catalogo prodotti e cambiato dopo l'anteprima: generarne una nuova",
            )

        existing_by_code: Dict[str, List[Dict[str, Any]]] = {}
        async for item in database.listino_adm.find(
            {}, {
                "_id": 1,
                "id": 1,
                "codice": 1,
                "adm_codice": 1,
                "descrizione": 1,
                "prezzo": 1,
                "confezione": 1,
                "categoria_adm": 1,
                "fonte": 1,
            }
        ):
            code = _catalogue_adm_code(item)
            if code:
                existing_by_code.setdefault(code, []).append(item)
        duplicate_ids, conflicting_duplicate_codes = _exact_duplicate_listino_ids(existing_by_code)
        if conflicting_duplicate_codes:
            sample = ", ".join(conflicting_duplicate_codes[:5])
            raise HTTPException(
                409,
                f"Listino locale ambiguo per i codici ADM {sample}: correggere le righe diverse",
            )

        claim = await database.fit_tariff_previews.update_one(
            {"id": body.preview_id, "status": "READY"},
            {"$set": {"status": "APPLYING", "apply_started_at": now.isoformat()}},
        )
        if not getattr(claim, "modified_count", 0):
            raise HTTPException(409, "Questa anteprima FIT e gia in applicazione")
        backup = None
        try:
            backup = await create_backup_snapshot(
                "Prima sincronizzazione tariffario FIT",
                "fit-tariff-sync",
                collection_names=["prodotti", "listino_adm"],
                metadata={
                    "preview_id": body.preview_id,
                    "snapshot_hash": preview["snapshot_hash"],
                },
            )
            if duplicate_ids:
                await database.listino_adm.delete_many({"_id": {"$in": duplicate_ids}})
            synced_at = now.isoformat()
            listino_operations = []
            for item in rows:
                matches = existing_by_code.get(item["adm_codice"], [])
                exact_filter = (
                    {"_id": matches[0]["_id"]}
                    if matches
                    else {"adm_codice": item["adm_codice"], "codice": item["codice"]}
                )
                listino_operations.append(UpdateOne(
                    exact_filter,
                    _fit_listino_update(item, preview["snapshot_hash"], synced_at),
                    upsert=True,
                ))
            listino_result = await bulk_upsert(database.listino_adm, listino_operations)
            if listino_result.get("errori"):
                raise RuntimeError(
                    f"{listino_result['errori']} righe del listino non sono state salvate"
                )
            expected_writes = len(rows)
            actual_writes = (
                listino_result.get("inseriti", 0) + listino_result.get("aggiornati", 0)
            )
            if actual_writes != expected_writes:
                raise RuntimeError(
                    f"Listino incompleto: salvate {actual_writes} righe su {expected_writes}"
                )
            product_operations = _fit_product_update_operations(
                rows, products, synced_at, import_id=body.preview_id,
            )
            product_result = await bulk_update(database.prodotti, product_operations)
            await apply_electronic_inhalation_categories()

            sync_document = {
                "id": str(uuid.uuid4()),
                "created_at": synced_at,
                "source": "FIT XLS con verifica ADM",
                "preview_id": body.preview_id,
                "snapshot_hash": preview["snapshot_hash"],
                "fit": preview.get("fit"),
                "adm_overlays": preview.get("adm_overlays", []),
                "warnings": preview.get("warnings", []),
                "righe": len(rows),
                "inseriti": listino_result.get("inseriti", 0),
                "aggiornati": listino_result.get("aggiornati", 0),
                "prodotti_trovati": product_result.get("trovati", 0),
                "prodotti_aggiornati": product_result.get("modificati", 0),
                "duplicati_listino_rimossi": len(duplicate_ids),
                "backup_id": backup.get("id"),
            }
            await database.fit_sync.insert_one(sync_document.copy())
            await record_audit_event(
                "SINCRONIZZATO",
                "TARIFFARIO_FIT",
                sync_document["id"],
                dopo={
                    "righe": sync_document["righe"],
                    "prodotti_aggiornati": sync_document["prodotti_aggiornati"],
                    "backup_id": sync_document["backup_id"],
                    "variazioni": (preview.get("summary") or {}).get("variazioni", []),
                },
                batch_id=body.preview_id,
            )
            await database.fit_tariff_previews.update_one(
                {"id": body.preview_id, "status": "APPLYING"},
                {"$set": {"status": "APPLIED", "applied_at": synced_at}, "$unset": {"rows": ""}},
            )
            return sync_document
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"[:500]
            await database.fit_tariff_previews.update_one(
                {"id": body.preview_id, "status": "APPLYING"},
                {"$set": {
                    "status": "FAILED",
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "failure": failure,
                    "backup_id": (backup or {}).get("id"),
                }},
            )
            try:
                await record_audit_event(
                    "FALLITO",
                    "TARIFFARIO_FIT",
                    body.preview_id,
                    dopo={"errore": failure, "backup_id": (backup or {}).get("id")},
                    batch_id=body.preview_id,
                )
            except Exception:
                pass
            raise HTTPException(
                500,
                "Sincronizzazione FIT non completata; il backup resta disponibile",
            ) from exc

    return router, list_listino, adm_categories, adm_sync, fit_status, fit_preview, fit_apply

"""Download and parse the public FIT tobacco tariff workbook."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import math
import re
import unicodedata
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse


FIT_TARIFFE_URL = "https://www.tabaccai.it/index.php/servizi/tariffe"
FIT_BASE_URL = "https://www.tabaccai.it"
FIT_MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
FIT_ALLOWED_HOSTS = {"tabaccai.it", "www.tabaccai.it"}

FIT_CATEGORY_MAP = {
    "ALTRI TABACCHI DA FUMO": "ALTRI TABACCHI DA FUMO",
    "SIGARETTE": "SIGARETTE",
    "SIGARETTI": "SIGARETTI",
    "SIGARI": "SIGARI",
    "TABACCO DA FIUTO O DA MASTICO": "FIUTO E MASTICO",
    "TABACCHI DA FIUTO": "FIUTO E MASTICO",
    "TRINCIATI PER SIGARETTE": "TRINCIATI PER SIGARETTA",
    "TRINCIATI PER SIGARETTA": "TRINCIATI PER SIGARETTA",
    "TRINCIATI PER PIPA": "ALTRI TABACCHI DA FUMO",
    "TABACCO DA INALAZIONE SENZA COMBUSTIONE": "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE",
    "INALAZIONE SENZA COMBUSTIONE": "PRODOTTI DA INALAZIONE SENZA COMBUSTIONE",
}

ITALIAN_MONTHS = {
    "GENNAIO": 1,
    "FEBBRAIO": 2,
    "MARZO": 3,
    "APRILE": 4,
    "MAGGIO": 5,
    "GIUGNO": 6,
    "LUGLIO": 7,
    "AGOSTO": 8,
    "SETTEMBRE": 9,
    "OTTOBRE": 10,
    "NOVEMBRE": 11,
    "DICEMBRE": 12,
}


def _plain_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(text.encode("ascii", "ignore").decode().upper().split())


def _date_from_filename(value: str) -> Optional[str]:
    match = re.search(r"(?:Completo_|Modificato_)(\d{4})(\d{2})(\d{2})", value, re.I)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def _italian_date_from_text(value: str) -> Optional[str]:
    plain = _plain_text(value)
    numeric = re.search(r"(?:AGGIORNAT[OA]|IN VIGORE)\s+(?:AL|DAL)\s+(\d{1,2})/(\d{1,2})/(\d{4})", plain)
    if numeric:
        try:
            return date(int(numeric.group(3)), int(numeric.group(2)), int(numeric.group(1))).isoformat()
        except ValueError:
            return None
    words = re.search(
        r"(?:AGGIORNAT[OA]|IN VIGORE)\s+(?:AL|DAL)\s+(\d{1,2})\s+([A-Z]+)\s+(\d{4})",
        plain,
    )
    if not words:
        return None
    month = ITALIAN_MONTHS.get(words.group(2))
    if not month:
        return None
    try:
        return date(int(words.group(3)), month, int(words.group(1))).isoformat()
    except ValueError:
        return None


def _direct_download_url(href: str) -> str:
    absolute = urljoin(FIT_TARIFFE_URL, href)
    parsed = urlparse(absolute)
    target = parse_qs(parsed.query).get("url", [""])[0]
    return urljoin(FIT_BASE_URL, target) if target else absolute


def _validated_fit_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in FIT_ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("URL tariffario FIT non autorizzato")
    return value


def extract_fit_tariff_info(html: str) -> Dict[str, Any]:
    """Find the newest complete XLS tariff in a FIT tariff-page response."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for anchor in soup.find_all("a"):
        href = anchor.get("href") or ""
        direct_url = _direct_download_url(href)
        if not re.search(r"/Completo_\d{8}\.xls(?:$|\?)", direct_url, re.I):
            continue
        effective_date = _date_from_filename(direct_url)
        if effective_date:
            _validated_fit_url(direct_url)
            candidates.append({
                "page_url": FIT_TARIFFE_URL,
                "download_url": direct_url,
                "effective_date": effective_date,
                "filename": direct_url.rsplit("/", 1)[-1].split("?", 1)[0],
            })
    if not candidates:
        raise ValueError("Nessun tariffario completo XLS trovato sul sito FIT")
    return max(candidates, key=lambda item: item["effective_date"])


def fetch_fit_tariff_info_sync() -> Dict[str, Any]:
    import requests

    response = requests.get(
        _validated_fit_url(FIT_TARIFFE_URL),
        headers={"User-Agent": "GestionaleTabacchi/1.0"},
        timeout=45,
        allow_redirects=False,
    )
    if response.is_redirect:
        raise ValueError("Redirect inatteso dalla pagina tariffari FIT")
    response.raise_for_status()
    return extract_fit_tariff_info(response.text)


def download_fit_tariff_sync(info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    import requests

    resolved = dict(info or fetch_fit_tariff_info_sync())
    payload = bytearray()
    with requests.get(
        _validated_fit_url(resolved["download_url"]),
        headers={"User-Agent": "GestionaleTabacchi/1.0"},
        timeout=90,
        stream=True,
        allow_redirects=False,
    ) as response:
        if response.is_redirect:
            raise ValueError("Redirect inatteso durante il download FIT")
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=128 * 1024):
            if not chunk:
                continue
            payload.extend(chunk)
            if len(payload) > FIT_MAX_DOWNLOAD_BYTES:
                raise ValueError("Il tariffario FIT supera il limite di 10 MB")
    content = bytes(payload)
    if not content.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValueError("Il download FIT non e un file XLS valido")
    resolved["content"] = content
    resolved["sha256"] = hashlib.sha256(content).hexdigest()
    resolved["size_bytes"] = len(content)
    return resolved


def normalize_fit_code(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return ""
        return str(int(value))
    text = str(value or "").strip().upper()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2].lstrip("0") or "0"
    match = re.search(r"\d+", text)
    return (match.group(0).lstrip("0") or "0") if match else ""


def parse_fit_money(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError("prezzo assente")
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError("prezzo non numerico")
        decimal_value = Decimal(str(value))
    else:
        text = str(value).strip().replace("€", "").replace(" ", "")
        if not text:
            raise ValueError("prezzo assente")
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        try:
            decimal_value = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("prezzo non numerico") from exc
    if decimal_value <= 0:
        raise ValueError("prezzo non positivo")
    return float(decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_fit_xls_bytes(content: bytes, info: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a legacy XLS workbook without relying on fixed row positions."""
    import xlrd

    try:
        workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
    except Exception as exc:
        raise ValueError(f"Tariffario FIT illeggibile: {exc}") from exc
    try:
        if not workbook.sheet_names():
            raise ValueError("Il tariffario FIT non contiene fogli")
        sheet = workbook.sheet_by_index(0)
        header_row = None
        header_map: Dict[str, int] = {}
        for row_index in range(min(sheet.nrows, 30)):
            values = [_plain_text(value) for value in sheet.row_values(row_index)]
            if "CATEGORIA" in values and "CODICE" in values and "DENOMINAZIONE COMMERCIALE" in values:
                header_row = row_index
                header_map = {value: index for index, value in enumerate(values)}
                break
        if header_row is None:
            raise ValueError("Intestazioni del tariffario FIT non riconosciute")

        required = ["CATEGORIA", "CODICE", "DENOMINAZIONE COMMERCIALE", "PREZZO PER CONFEZIONE (EUR)"]
        # The euro glyph is stripped by ASCII normalization, leaving either EUR or an empty pair of brackets.
        price_index = next(
            (index for label, index in header_map.items() if label.startswith("PREZZO PER CONFEZIONE")),
            None,
        )
        if price_index is None or any(label not in header_map for label in required[:3]):
            raise ValueError("Colonne obbligatorie del tariffario FIT mancanti")
        package_index = next(
            (index for label, index in header_map.items() if label.startswith("TIPO DI CONFEZIONE")),
            None,
        )
        kg_index = next(
            (index for label, index in header_map.items() if label.startswith("PREZZO PER KG")),
            None,
        )

        preamble = " ".join(
            str(value or "")
            for row_index in range(header_row)
            for value in sheet.row_values(row_index)
        )
        updated_date = None
        updated_match = re.search(r"aggiornat[oa]\s+al\s+(\d{1,2}/\d{1,2}/\d{4})", preamble, re.I)
        if updated_match:
            updated_date = _italian_date_from_text(f"aggiornato al {updated_match.group(1)}")
        # The filename identifies the tariff's effective date; the preamble also
        # contains an earlier "elenco aggiornato al" date which must not replace it.
        effective_date = info.get("effective_date") or _italian_date_from_text(preamble)

        rows: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        seen_codes = set()
        for row_index in range(header_row + 1, sheet.nrows):
            values = sheet.row_values(row_index)
            if not any(str(value or "").strip() for value in values):
                continue
            try:
                raw_category = str(values[header_map["CATEGORIA"]] or "").strip()
                category = FIT_CATEGORY_MAP.get(_plain_text(raw_category))
                code = normalize_fit_code(values[header_map["CODICE"]])
                description = " ".join(str(values[header_map["DENOMINAZIONE COMMERCIALE"]] or "").split())
                if not category:
                    raise ValueError(f"categoria non riconosciuta: {raw_category}")
                if not code or not description:
                    raise ValueError("codice o descrizione assente")
                if code in seen_codes:
                    raise ValueError(f"codice duplicato: {code}")
                price = parse_fit_money(values[price_index])
                price_kg = None
                if kg_index is not None and str(values[kg_index] or "").strip():
                    price_kg = parse_fit_money(values[kg_index])
                packaging = (
                    " ".join(str(values[package_index] or "").split())
                    if package_index is not None
                    else ""
                )
                seen_codes.add(code)
                rows.append({
                    "adm_codice": code,
                    "codice": f"AMMS{code}",
                    "descrizione": description,
                    "confezione": packaging,
                    "prezzo": price,
                    "prezzo_kg": price_kg,
                    "categoria_adm": category,
                    "fonte": "FIT",
                    "fit_xls_url": info.get("download_url"),
                    "fit_effective_date": effective_date,
                    "fit_updated_date": updated_date,
                })
            except (IndexError, ValueError) as exc:
                errors.append({"riga": row_index + 1, "errore": str(exc)})
        if errors:
            sample = "; ".join(f"riga {item['riga']}: {item['errore']}" for item in errors[:5])
            raise ValueError(f"Tariffario FIT non valido ({len(errors)} errori): {sample}")
        if len(rows) < 100:
            raise ValueError(f"Tariffario FIT incompleto: soltanto {len(rows)} prodotti")
        categories = {}
        for item in rows:
            categories[item["categoria_adm"]] = categories.get(item["categoria_adm"], 0) + 1
        return {
            **{key: value for key, value in info.items() if key != "content"},
            "effective_date": effective_date,
            "updated_date": updated_date,
            "categories": categories,
            "rows": rows,
            "row_count": len(rows),
        }
    finally:
        workbook.release_resources()


def fetch_and_parse_fit_tariff_sync() -> Dict[str, Any]:
    downloaded = download_fit_tariff_sync()
    return parse_fit_xls_bytes(downloaded["content"], downloaded)


def iso_date_from_adm(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None

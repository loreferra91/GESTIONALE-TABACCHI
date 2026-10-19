import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo


def csv_vending_datetime(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    for date_format in (
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d/%m/%y %H:%M:%S",
        "%d/%m/%y %H:%M",
        "%d-%m-%y %H:%M:%S",
        "%d-%m-%y %H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d/%m/%y",
        "%d-%m-%y",
    ):
        try:
            return datetime.strptime(text, date_format).isoformat()
        except ValueError:
            continue
    raise ValueError(f"data non riconosciuta: {text}")


def normalize_vending_column(value: Any) -> str:
    column = re.sub(r"\s+", "", str(value or "").strip().upper())
    match = re.fullmatch(r"\d+[-/]([A-Z]+\d+)", column)
    return match.group(1) if match else column


def parse_sale_datetime(value: Any):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed:
        return parsed.replace(tzinfo=None)
    for date_format in (
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d/%m/%y %H:%M:%S",
        "%d/%m/%y %H:%M",
        "%d-%m-%y %H:%M:%S",
        "%d-%m-%y %H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d/%m/%y",
        "%d-%m-%y",
    ):
        try:
            return datetime.strptime(text, date_format)
        except ValueError:
            continue
    return None


def latest_historical_vending_datetime(documents: List[Dict[str, Any]]):
    parsed_datetimes = []
    for document in documents:
        raw = document.get("raw") or []
        value = raw[1] if len(raw) > 1 and raw[1] else (raw[2] if len(raw) > 2 else None)
        parsed = parse_sale_datetime(value)
        if parsed:
            parsed_datetimes.append(parsed)
    return max(parsed_datetimes, default=None)


def filter_new_csv_vending_rows(
    rows: List[Dict[str, Any]], historical_documents: List[Dict[str, Any]]
) -> tuple[List[Dict[str, Any]], Any]:
    cutoff = latest_historical_vending_datetime(historical_documents)
    if not cutoff:
        return rows, None
    return [
        row
        for row in rows
        if (parse_sale_datetime(row.get("data")) or datetime.min) > cutoff
    ], cutoff


def local_sale_datetime(value: Any) -> Optional[datetime]:
    parsed = parse_sale_datetime(value)
    if not parsed:
        return None
    text = str(value or "")
    if parsed.tzinfo is None and not re.search(r"(?:Z|[+-]\d\d:\d\d)$", text):
        return parsed
    aware = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return aware.astimezone(ZoneInfo("Europe/Rome")).replace(tzinfo=None)


def create_csv_vending_parser(
    *,
    normalize_nonnegative_money: Callable[[Any], float],
    sum_money: Callable[[Any], float],
    payment_bucket: Callable[[Any], str],
    product_code_text: Callable[[Any], str],
    adm_numeric_code: Callable[[Any], str],
) -> Dict[str, Callable[..., Any]]:
    def parse_csv_vending(raw: str, pagamento: str = "CONTANTI") -> Dict[str, Any]:
        sample = raw[:2000]
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except Exception:
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)

        def normalize_header(value):
            return re.sub(r"\s+", " ", (value or "").strip().lower())

        field_map = {normalize_header(key): key for key in (reader.fieldnames or [])}

        def pick(row, *keys):
            for key in keys:
                actual = field_map.get(normalize_header(key))
                if actual and row.get(actual) not in (None, ""):
                    return row.get(actual)
            return None

        skipped = 0
        errors = []
        rows = []
        for index, row in enumerate(reader):
            try:
                codice = str(
                    pick(row, "codice", "codice aams", "cod aams", "cod", "aams") or ""
                ).strip()
                nome = str(
                    pick(row, "nome prodotto", "prodotto", "descrizione", "articolo") or ""
                ).strip()
                prezzo = pick(row, "prezzo", "importo")
                data = pick(row, "data", "date")
                colonna = normalize_vending_column(pick(row, "colonna", "column"))
                categoria = str(pick(row, "categoria", "tipo") or "").strip()
                payment = str(
                    pick(row, "pagamento", "payment") or pagamento
                ).strip().upper() or pagamento.upper()
                if not codice and not nome:
                    skipped += 1
                    continue
                price = normalize_nonnegative_money(prezzo) if prezzo not in (None, "") else 0.0
                rows.append(
                    {
                        "riga": index + 2,
                        "data": csv_vending_datetime(data),
                        "codice": codice,
                        "nome": nome,
                        "prezzo": price,
                        "_identity_price": f"{float(str(prezzo or 0).replace(',', '.')):.2f}",
                        "colonna": colonna,
                        "categoria": categoria,
                        "pagamento": payment,
                    }
                )
            except Exception as exc:
                errors.append({"riga": index + 2, "errore": str(exc)})
        return {
            "righe": rows,
            "saltati": skipped,
            "errori": errors,
            "delimitatore": delimiter,
        }

    def summarize_vending_csv(parsed: Dict[str, Any]) -> Dict[str, Any]:
        payment_counts: Dict[str, int] = {}
        payment_amounts: Dict[str, float] = {}
        dates = []
        for row in parsed.get("righe", []):
            bucket = payment_bucket(row.get("pagamento"))
            payment_counts[bucket] = payment_counts.get(bucket, 0) + 1
            payment_amounts[bucket] = sum_money(
                [payment_amounts.get(bucket, 0), row.get("prezzo") or 0]
            )
            parsed_date = parse_sale_datetime(row.get("data"))
            if parsed_date:
                dates.append(parsed_date)
        electronic = sum_money(
            payment_amounts.get(bucket, 0)
            for bucket in ("CARTE", "PAGOBANCOMAT", "ALTRO_ELETTRONICO")
        )
        return {
            "righe": len(parsed.get("righe", [])),
            "saltati": int(parsed.get("saltati", 0)),
            "errori": parsed.get("errori", []),
            "pagamenti": payment_counts,
            "importi_pagamenti": payment_amounts,
            "totale_elettronici": electronic,
            "totale": sum_money(payment_amounts.values()),
            "data_da": min(dates).isoformat() if dates else None,
            "data_a": max(dates).isoformat() if dates else None,
        }

    def csv_vending_event_base(row: Dict[str, Any]) -> str:
        payload = {
            "data": str(row.get("data") or ""),
            "colonna": normalize_vending_column(row.get("colonna")),
            "codice": product_code_text(row.get("codice")),
            "prezzo": row.get("_identity_price", f"{float(row.get('prezzo') or 0):.2f}"),
            "pagamento": str(row.get("pagamento") or "").strip().upper(),
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def csv_vending_compatibility_key(row: Dict[str, Any]) -> str:
        parsed = parse_sale_datetime(row.get("data"))
        minute = parsed.strftime("%Y-%m-%dT%H:%M") if parsed else str(row.get("data") or "")[:16]
        raw_code = product_code_text(row.get("codice"))
        numeric_code = (
            adm_numeric_code(raw_code)
            if raw_code.isdigit() or raw_code.startswith("AMMS")
            else ""
        )
        identity = numeric_code or raw_code
        if not identity:
            identity = re.sub(
                r"\s+",
                " ",
                str(row.get("nome") or row.get("descrizione") or "").strip().upper(),
            )
        payload = {
            "minuto": minute,
            "colonna": normalize_vending_column(row.get("colonna")),
            "prodotto": identity,
            "prezzo": row.get(
                "_identity_price",
                f"{float(row.get('prezzo', row.get('importo', 0)) or 0):.2f}",
            ),
            "pagamento": payment_bucket(row.get("pagamento")),
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def assign_csv_vending_accounting_keys(rows: List[Dict[str, Any]]) -> None:
        occurrences: Dict[str, int] = {}
        for row in rows:
            accounting_base = csv_vending_compatibility_key(row)
            occurrence = occurrences.get(accounting_base, 0) + 1
            occurrences[accounting_base] = occurrence
            row["csv_event_compatibility_key"] = accounting_base
            row["csv_accounting_occurrence"] = occurrence
            row["csv_accounting_key"] = f"{accounting_base}:{occurrence}"

    def assign_csv_vending_event_keys(rows: List[Dict[str, Any]]) -> None:
        exact_occurrences: Dict[str, int] = {}
        for row in rows:
            base = csv_vending_event_base(row)
            occurrence = exact_occurrences.get(base, 0) + 1
            exact_occurrences[base] = occurrence
            row["csv_event_occurrence"] = occurrence
            row["csv_event_key"] = f"{base}:{occurrence}"
        assign_csv_vending_accounting_keys(rows)

    return {
        "parse_csv_vending": parse_csv_vending,
        "summarize_vending_csv": summarize_vending_csv,
        "csv_vending_event_base": csv_vending_event_base,
        "assign_csv_vending_event_keys": assign_csv_vending_event_keys,
        "csv_vending_compatibility_key": csv_vending_compatibility_key,
        "assign_csv_vending_accounting_keys": assign_csv_vending_accounting_keys,
    }

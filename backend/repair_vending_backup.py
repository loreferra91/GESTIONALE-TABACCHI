"""Ricostruisce il solo registro contabile vending in un backup gestionale.

Il file sorgente non viene mai sovrascritto. Le vendite, i prodotti, le colonne
vending e la cassa tecnica restano byte-per-byte equivalenti nel payload JSON.
Gli import CSV storici vengono finalizzati perché, dopo la ricostruzione, il
loro annullamento non avrebbe più un legame affidabile con gli eventi rebuilt.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROTECTED_COLLECTIONS = (
    "prodotti",
    "vending",
    "vendite",
    "cassa_vending_stato",
)
ACCOUNTING_STATE_ID = "ledger"
REPAIR_NAMESPACE = uuid.UUID("fd46648a-203e-45ab-a503-b505c5d79776")


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("data vuota")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in (
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
        "%d/%m/%y %H:%M:%S", "%d/%m/%y %H:%M",
        "%d-%m-%y %H:%M:%S", "%d-%m-%y %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"data non riconosciuta: {text}")


def _normalize_column(value: Any) -> str:
    column = re.sub(r"\s+", "", str(value or "").strip().upper())
    match = re.fullmatch(r"\d+[-/]([A-Z]+\d+)", column)
    return match.group(1) if match else column


def _numeric_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("AMMS"):
        text = text[4:]
    digits = re.sub(r"\D", "", text)
    return digits.lstrip("0") or digits


def _payment_bucket(value: Any) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
    if not normalized or "CONTANT" in normalized:
        return "CONTANTI"
    if "PAGOBANCOMAT" in normalized or normalized in {"BANCOMAT", "PAGOBANCO"}:
        return "PAGOBANCOMAT"
    if "CART" in normalized:
        return "CARTE"
    return "ALTRO_ELETTRONICO"


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _exact_base(row: dict[str, Any]) -> str:
    return _digest({
        "data": row["data"],
        "colonna": row["colonna"],
        "codice": row["codice"],
        "prezzo": f"{row['prezzo']:.2f}",
        "pagamento": row["pagamento_raw"],
    })


def _accounting_base(row: dict[str, Any]) -> str:
    identity = _numeric_code(row["codice"]) or row["codice"] or row["nome"].upper()
    return _digest({
        "minuto": row["parsed_at"].strftime("%Y-%m-%dT%H:%M"),
        "colonna": row["colonna"],
        "prodotto": identity,
        "prezzo": f"{row['prezzo']:.2f}",
        "pagamento": row["pagamento"],
    })


def _read_csv(path: Path) -> tuple[list[dict[str, Any]], str]:
    content = path.read_bytes()
    raw = content.decode("utf-8-sig", errors="replace")
    try:
        delimiter = csv.Sniffer().sniff(raw[:2000], delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ";"
    reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
    fields = {re.sub(r"\s+", " ", (name or "").strip().lower()): name for name in reader.fieldnames or []}

    def pick(source: dict[str, Any], *names: str) -> Any:
        for name in names:
            actual = fields.get(name)
            if actual and source.get(actual) not in (None, ""):
                return source[actual]
        return None

    rows = []
    for line_number, source in enumerate(reader, 2):
        parsed_at = _parse_datetime(pick(source, "data", "date"))
        price_raw = pick(source, "prezzo", "importo")
        price = float(str(price_raw or 0).replace(",", "."))
        code = str(pick(source, "codice", "codice aams", "cod aams", "cod", "aams") or "").strip().upper()
        name = str(pick(source, "nome prodotto", "prodotto", "descrizione", "articolo") or "").strip()
        if not code and not name:
            continue
        payment_raw = str(pick(source, "pagamento", "payment") or "CONTANTI").strip().upper()
        rows.append({
            "line_number": line_number,
            "parsed_at": parsed_at,
            "data": parsed_at.isoformat(),
            "codice": code,
            "nome": name,
            "prezzo": price,
            "colonna": _normalize_column(pick(source, "colonna", "column")),
            "pagamento_raw": payment_raw,
            "pagamento": _payment_bucket(payment_raw),
        })

    rows.sort(
        key=lambda row: (
            row["parsed_at"], row["colonna"], row["codice"], row["prezzo"], row["pagamento"]
        ),
        reverse=True,
    )
    exact_occurrences: Counter[str] = Counter()
    accounting_occurrences: Counter[str] = Counter()
    file_sha256 = hashlib.sha256(content).hexdigest()
    for row in rows:
        exact_base = _exact_base(row)
        exact_occurrences[exact_base] += 1
        accounting_base = _accounting_base(row)
        accounting_occurrences[accounting_base] += 1
        row["csv_event_key"] = f"{exact_base}:{exact_occurrences[exact_base]}"
        row["csv_event_occurrence"] = exact_occurrences[exact_base]
        row["csv_event_compatibility_key"] = accounting_base
        row["csv_accounting_occurrence"] = accounting_occurrences[accounting_base]
        row["csv_accounting_key"] = f"{accounting_base}:{accounting_occurrences[accounting_base]}"
        row["file_sha256"] = file_sha256
        row["file_name"] = path.name
    return rows, file_sha256


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def rebuild_backup(backup: dict[str, Any], csv_paths: list[Path]) -> tuple[dict[str, Any], dict[str, Any]]:
    collections = backup.get("collections")
    if not isinstance(collections, dict):
        raise ValueError("backup privo di collections")
    for name in (*PROTECTED_COLLECTIONS, "vending_accounting_snapshots", "vending_accounting_state", "vending_accounting_events"):
        if name not in collections:
            raise ValueError(f"collection mancante: {name}")

    protected_before = {name: _fingerprint(collections[name]) for name in PROTECTED_COLLECTIONS}
    repaired_at = datetime.now(timezone.utc).isoformat()
    events_by_key: dict[str, dict[str, Any]] = {}
    file_summaries = []

    for csv_path in csv_paths:
        rows, file_sha256 = _read_csv(csv_path)
        if not rows:
            raise ValueError(f"CSV senza righe valide: {csv_path}")
        for row in rows:
            events_by_key[row["csv_accounting_key"]] = row
        file_summaries.append({
            "file_name": csv_path.name,
            "sha256": file_sha256,
            "rows": len(rows),
            "data_da": min(row["data"] for row in rows),
            "data_a": max(row["data"] for row in rows),
        })

    selected_rows = sorted(events_by_key.values(), key=lambda row: row["parsed_at"])
    payment_counts: Counter[str] = Counter()
    payment_amounts: defaultdict[str, float] = defaultdict(float)
    events = []
    for row in selected_rows:
        payment_counts[row["pagamento"]] += 1
        payment_amounts[row["pagamento"]] += row["prezzo"]
        events.append({
            "id": str(uuid.uuid5(REPAIR_NAMESPACE, row["csv_accounting_key"])),
            "csv_event_key": row["csv_event_key"],
            "csv_event_occurrence": row["csv_event_occurrence"],
            "csv_event_compatibility_key": row["csv_event_compatibility_key"],
            "csv_accounting_occurrence": row["csv_accounting_occurrence"],
            "csv_accounting_key": row["csv_accounting_key"],
            "data": row["data"],
            "importo": round(row["prezzo"], 2),
            "pagamento": row["pagamento"],
            "source_batch_id": None,
            "first_seen_at": repaired_at,
            "file_sha256": row["file_sha256"],
            "file_name": row["file_name"],
            "rebuilt_from_complete_csv": True,
        })

    amounts = {key: round(value, 2) for key, value in sorted(payment_amounts.items())}
    total = round(sum(amounts.values()), 2)
    data_da = selected_rows[0]["data"]
    data_a = selected_rows[-1]["data"]
    latest_file = max(file_summaries, key=lambda item: item["data_a"])
    combined_sha256 = hashlib.sha256(
        "|".join(item["sha256"] for item in file_summaries).encode("ascii")
    ).hexdigest()

    old_state = (collections.get("vending_accounting_state") or [{}])[0]
    old_active_snapshot = None
    for snapshot in collections["vending_accounting_snapshots"]:
        if snapshot.get("status") == "active":
            old_active_snapshot = snapshot.get("id")
            snapshot["status"] = "superseded"
            snapshot["superseded_at"] = repaired_at
            snapshot["superseded_by_repair"] = True

    snapshot_id = str(uuid.uuid5(REPAIR_NAMESPACE, combined_sha256))
    collections["vending_accounting_snapshots"].append({
        "id": snapshot_id,
        "status": "active",
        "created_at": repaired_at,
        "file_name": " + ".join(item["file_name"] for item in file_summaries),
        "sha256": combined_sha256,
        "previous_snapshot_id": old_active_snapshot,
        "righe": len(events),
        "saltati": 0,
        "pagamenti": dict(payment_counts),
        "importi_pagamenti": amounts,
        "totale_elettronici": round(total - amounts.get("CONTANTI", 0), 2),
        "totale": total,
        "data_da": data_da,
        "data_a": data_a,
        "csv_event_keys": [event["csv_event_key"] for event in events],
        "csv_accounting_keys": [event["csv_accounting_key"] for event in events],
        "accounting_identity_version": 2,
        "rebuilt_from_complete_csvs": [item["file_name"] for item in file_summaries],
    })

    collections["vending_accounting_events"] = events
    collections["vending_accounting_state"] = [{
        "id": ACCOUNTING_STATE_ID,
        "created_at": old_state.get("created_at") or repaired_at,
        "ledger_started_at": data_da,
        "updated_at": repaired_at,
        "last_aligned_at": repaired_at,
        "baseline_importi_pagamenti": {},
        "baseline_righe": 0,
        "baseline_data_da": None,
        "baseline_data_a": None,
        "baseline_event_keys": [],
        "baseline_accounting_keys": [],
        "accounting_identity_version": 2,
        "migrated_from_snapshot_id": snapshot_id,
        "last_file_name": latest_file["file_name"],
        "last_file_sha256": latest_file["sha256"],
        "last_file_rows": latest_file["rows"],
        "last_data_da": latest_file["data_da"],
        "last_data_a": latest_file["data_a"],
        "legacy_sales_seeded_at": repaired_at,
        "legacy_sales_seeded": 0,
        "repaired_from_complete_csvs": [item["file_name"] for item in file_summaries],
        "repair_snapshot_id": snapshot_id,
    }]

    finalized_batches = 0
    for batch in collections.get("vendite_bulk_imports", []):
        if batch.get("sorgente") == "CSV_VENDING" and batch.get("status") == "active":
            batch["status"] = "finalized"
            batch["accounting_repair_finalized_at"] = repaired_at
            finalized_batches += 1

    counts = backup.setdefault("counts", {})
    counts["vending_accounting_events"] = len(events)
    counts["vending_accounting_state"] = 1
    counts["vending_accounting_snapshots"] = len(collections["vending_accounting_snapshots"])
    counts["vendite_bulk_imports"] = len(collections.get("vendite_bulk_imports", []))
    backup["repair_metadata"] = {
        "kind": "vending-accounting-v2",
        "repaired_at": repaired_at,
        "source_csvs": file_summaries,
        "events": len(events),
        "payment_counts": dict(payment_counts),
        "payment_amounts": amounts,
        "total": total,
        "finalized_csv_batches": finalized_batches,
        "protected_collections": list(PROTECTED_COLLECTIONS),
    }

    protected_after = {name: _fingerprint(collections[name]) for name in PROTECTED_COLLECTIONS}
    if protected_before != protected_after:
        raise RuntimeError("una collection protetta è stata modificata")
    if len(events) != len({event["csv_accounting_key"] for event in events}):
        raise RuntimeError("chiavi contabili duplicate dopo la ricostruzione")

    return backup, backup["repair_metadata"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("csv", nargs="+", type=Path)
    parser.add_argument("--force", action="store_true", help="sovrascrive solo il file output")
    args = parser.parse_args()

    source = args.backup.resolve()
    output = args.output.resolve()
    if source == output:
        raise SystemExit("il file output deve essere diverso dal backup originale")
    if output.exists() and not args.force:
        raise SystemExit(f"output già esistente: {output}")
    if not source.is_file():
        raise SystemExit(f"backup non trovato: {source}")
    for csv_path in args.csv:
        if not csv_path.is_file():
            raise SystemExit(f"CSV non trovato: {csv_path}")

    backup = json.loads(source.read_text(encoding="utf-8-sig"))
    repaired, summary = rebuild_backup(backup, [path.resolve() for path in args.csv])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(repaired, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    verification = json.loads(output.read_text(encoding="utf-8"))
    if verification.get("repair_metadata") != summary:
        raise RuntimeError("verifica del file salvato non riuscita")
    print(json.dumps({"output": str(output), **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

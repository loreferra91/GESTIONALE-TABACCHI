"""Read-only reconciliation of old CSV imports against the rebuilt CSV ledger.

Matching timestamps alone never proves a duplicate. A match requires the
original compatibility hash (product, minute, column, price and payment),
complete occurrence numbers, original event keys and complete-CSV provenance.
Only the forecast input is normalized; operational sales/stock/cash stay intact.
"""
from collections import Counter, defaultdict
import hashlib
import json
import re

try:
    from ..csv_vending import normalize_vending_column, parse_sale_datetime
    from ..auto_order_planning import commercial_day
except ImportError:  # direct ASGI import
    from csv_vending import normalize_vending_column, parse_sale_datetime
    from auto_order_planning import commercial_day


class LegacyVendingReconciler:
    def __init__(self, collector, compatibility_key, legacy_rows=()):
        self.collector = collector
        self.compatibility_key = compatibility_key
        self.legacy = []
        self.ids = Counter()
        self.claimed_keys = set()
        self.claimed_signatures = set()
        self.legacy_signatures = set()
        for row in legacy_rows:
            try:
                self.legacy_signatures.add(self.signature(row))
            except (TypeError, ValueError):
                pass

    def signature(self, row):
        # The producer's normalization also handles AMMS/leading zeros/payments.
        return self.compatibility_key({**row, "colonna": ""})

    def add(self, row):
        day = commercial_day(row.get("data"))
        if not day or not self.collector.start <= day <= self.collector.end:
            self.collector.add(row)
            return
        self.ids[str(row.get("id") or "")] += 1
        if str(row.get("sorgente") or "").upper() == "CSV_VENDING":
            if not row.get("csv_event_key"):
                self.legacy.append(row)
                return
            try:
                if self.signature(row) in self.legacy_signatures:
                    self.legacy.append(row)
                    return
            except (TypeError, ValueError):
                pass
            self.claimed_keys.add(str(row["csv_event_key"]))
            try:
                self.claimed_signatures.add(self.signature(row))
            except (TypeError, ValueError):
                pass
        self.collector.add(row)

    def finish(self, events, columns):
        ledger = defaultdict(list)
        key_counts = Counter(str(event.get("csv_event_key") or "") for event in events)
        for event in events:
            ledger[event.get("csv_event_compatibility_key")].append(event)
        groups = defaultdict(list)
        unresolved = []
        for row in self.legacy:
            try:
                groups[self.signature(row)].append(row)
            except (TypeError, ValueError):
                unresolved.append(row)
        column_names = sorted({normalize_vending_column(c.get("colonna")) for c in columns} - {""})
        evidence = []
        raw_count = canonical_count = 0
        for signature, rows in sorted(groups.items()):
            codes = {str(r.get("codice") or "").strip() for r in rows}
            matches = []
            if (len(codes) == 1 and "" not in codes
                    and signature not in self.claimed_signatures
                    and all(r.get("quantita") == 1 and r.get("canale") == "VENDING"
                            and r.get("id") and self.ids[str(r["id"])] == 1 for r in rows)):
                for column in column_names:
                    digest = self.compatibility_key({**rows[0], "colonna": column})
                    if ledger.get(digest):
                        matches.append((digest, ledger[digest], column))
            if len(matches) != 1:
                unresolved.extend(rows)
                continue
            digest, candidates, column = matches[0]
            keyed_rows = [r for r in rows if r.get("csv_event_key")]
            occurrences = [e.get("csv_accounting_occurrence") for e in candidates]
            valid = (
                len(candidates) <= len(rows)
                and (not keyed_rows or (
                    len(keyed_rows) == len(candidates)
                    and len({r["csv_event_key"] for r in keyed_rows}) == len(keyed_rows)
                    and all(normalize_vending_column(r.get("colonna")) == column
                            and re.fullmatch(r"[0-9a-f]{64}:[1-9][0-9]*", str(r["csv_event_key"]))
                            for r in keyed_rows)
                ))
                and all(type(n) is int for n in occurrences)
                and set(occurrences) == set(range(1, len(candidates) + 1))
            )
            for event in candidates:
                key = str(event.get("csv_event_key") or "")
                try:
                    # Recompute using the ledger's timestamp, amount and payment;
                    # a corrupt stored hash must not be accepted on trust alone.
                    verified = self.compatibility_key({
                        **rows[0], "colonna": column, "data": event.get("data"),
                        "importo": event.get("importo"), "pagamento": event.get("pagamento"),
                    }) == digest
                except (TypeError, ValueError):
                    verified = False
                valid = valid and (
                    verified and event.get("rebuilt_from_complete_csv") is True
                    and bool(re.fullmatch(r"[0-9a-f]{64}", str(event.get("file_sha256") or "")))
                    and bool(event.get("file_name"))
                    and bool(re.fullmatch(r"[0-9a-f]{64}:[1-9][0-9]*", key))
                    and key_counts[key] == 1 and key not in self.claimed_keys
                    and event.get("csv_accounting_key") == f"{digest}:{event.get('csv_accounting_occurrence')}"
                    and parse_sale_datetime(event.get("data")) is not None
                )
            if not valid:
                unresolved.extend(rows)
                continue
            code = next(iter(codes))
            for event in sorted(candidates, key=lambda e: e["csv_accounting_occurrence"]):
                self.collector.add({
                    "codice": code, "data": event["data"], "quantita": 1,
                    "canale": "VENDING", "sorgente": "CSV_VENDING",
                    "csv_event_key": event["csv_event_key"],
                })
                self.claimed_keys.add(event["csv_event_key"])
            raw_count += len(rows)
            canonical_count += len(candidates)
            evidence.append({"codice": code, "compatibility_key": digest,
                             "raw_rows": len(rows),
                             "events": sorted((e["csv_event_key"], e["file_sha256"]) for e in candidates)})
        for row in unresolved:
            self.collector.add(row)
        return {
            "legacy_rows": sum(not r.get("csv_event_key") for r in self.legacy),
            "matched_rows": raw_count, "canonical_events": canonical_count,
            "duplicate_copies": raw_count - canonical_count,
            "unresolved_rows": len(unresolved),
            "products": sorted({e["codice"] for e in evidence}),
            "evidence_digest": hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
        }

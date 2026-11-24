"""Validate a downloaded Gestionale JSON backup OFFLINE, without database access.

This verifies the format, per-collection SHA-256, all declared counts and
overall file digest. It does NOT assert independent storage or a verified
MongoDB restore. Never prints documents, passwords or personal data.
"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sys

# These are the source-of-truth v1 backup collection names in backup_router.py.
BACKUP_COLLECTIONS = (
    "prodotti", "listino_adm", "vending", "storico_ordini", "parametri",
    "db_storico_vend", "db_storico_vending_ext", "vendite",
    "vendite_bulk_imports", "cassa_rapida_batches", "versamenti", "prelievi_vending",
    "scontrini_vending", "cassa_vending_stato", "vending_accounting_snapshots",
    "vending_accounting_state", "vending_accounting_events", "vending_operations",
    "accounting_baselines", "cassa", "ordini_fornitore", "ordini_fornitore_righe",
    "auto_order_import_state", "import_history", "adm_sync", "anomalie_ignorate",
    "smart_venue", "smart_venue_hidden", "smart_venue_values",
    "smart_venue_movimenti", "ricezioni_merce", "rettifiche_inventario",
    "rettifiche_venduti", "reconciliation_cases", "audit_events",
    "gratta_vinci_prodotti", "gratta_vinci_movimenti",
)
OPTIONAL_COLLECTIONS = frozenset((
    "vendite_bulk_imports", "cassa_rapida_batches", "vending_operations",
    "scontrini_vending", "cassa_vending_stato", "vending_accounting_snapshots",
    "vending_accounting_state", "vending_accounting_events",
    "accounting_baselines", "anomalie_ignorate", "smart_venue",
    "smart_venue_hidden", "smart_venue_values", "smart_venue_movimenti",
    "ricezioni_merce", "rettifiche_inventario", "rettifiche_venduti",
    "reconciliation_cases", "audit_events", "auto_order_import_state",
    "gratta_vinci_prodotti", "gratta_vinci_movimenti",
))
MAX_BACKUP_BYTES = 20 * 1024 * 1024


class BackupValidationError(ValueError):
    pass


def inspect_export(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".json":
        raise BackupValidationError("Expected a regular downloaded .json backup")
    if not 0 < path.stat().st_size <= MAX_BACKUP_BYTES:
        raise BackupValidationError("Backup size outside supported 20 MiB limit")
    file_bytes = path.read_bytes()
    try:
        payload = json.loads(file_bytes)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BackupValidationError("Backup is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("format") != "gestionale-tabacchi-backup" or payload.get("version") != 1:
        raise BackupValidationError("Unsupported backup format/version")
    collections = payload.get("collections")
    counts = payload.get("counts")
    checksums = payload.get("checksums")
    if not isinstance(collections, dict) or not isinstance(counts, dict) or not isinstance(checksums, dict):
        raise BackupValidationError("Collection counts and integrity hashes are required")
    names = set(collections)
    if unknown := names - set(BACKUP_COLLECTIONS):
        raise BackupValidationError("Unknown backup collection types")
    if missing := set(BACKUP_COLLECTIONS) - OPTIONAL_COLLECTIONS - names:
        raise BackupValidationError("Required operational collections missing")
    if set(counts) != names or set(checksums) != names:
        raise BackupValidationError("Every collection must have a declared count and SHA-256 hash")

    document_count = 0
    for name, docs in collections.items():
        if not isinstance(docs, list):
            raise BackupValidationError(f"Invalid document list in {name}")
        if any(not isinstance(doc, dict) or "_id" in doc for doc in docs):
            raise BackupValidationError(f"Invalid document structure in {name}")
        if type(counts[name]) is not int or counts[name] != len(docs):
            raise BackupValidationError(f"Count mismatch in {name}")
        digest = hashlib.sha256()
        for document in docs:
            digest.update(json.dumps(
                document, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), default=str,
            ).encode("utf-8"))
            digest.update(b"\n")
        expected = checksums[name]
        if not isinstance(expected, str) or len(expected) != 64 or not secrets.compare_digest(
            expected.lower(), digest.hexdigest()
        ):
            raise BackupValidationError(f"SHA-256 mismatch in {name}")
        document_count += len(docs)
    return {
        "result": "pass",
        "format": "gestionale-tabacchi-backup",
        "version": 1,
        "created_at": str(payload.get("created_at") or "")[:40],
        "collections_verified": len(names),
        "total_documents_verified": document_count,
        "file_bytes": len(file_bytes),
        "file_sha256": hashlib.sha256(file_bytes).hexdigest(),
        "document_hashes_verified": True,
        "independent_external_copy_verified": False,
        "restore_verified": False,
        "note": "Format and content hashes verified locally; recovery evidence remains separate.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup_json", type=Path, help="Locally downloaded full JSON backup")
    args = parser.parse_args()
    try:
        print(json.dumps(inspect_export(args.backup_json), sort_keys=True))
        return 0
    except (BackupValidationError, OSError):
        # Avoid printing file contents, paths, credentials or data values.
        print(json.dumps({"result": "failed", "reason": "Backup file validation failed"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())

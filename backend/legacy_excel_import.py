"""Explicit opt-in for legacy workbook writes to operational collections."""
import os

from fastapi import HTTPException


def require_legacy_excel_import(confirmation: str | None) -> None:
    enabled = os.environ.get("ENABLE_LEGACY_EXCEL_IMPORT", "").strip().lower() == "true"
    if not enabled:
        raise HTTPException(409, "Import Excel legacy disabilitato: abilitarlo solo per una migrazione controllata")
    if confirmation != "confermo-import-legacy":
        raise HTTPException(409, "Conferma esplicita dell'import Excel legacy mancante")

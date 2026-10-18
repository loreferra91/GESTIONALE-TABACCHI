"""Import-history persistence, lookup helpers, and read endpoint."""
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter


def import_includes_accounting(report):
    """Return whether an import contains the RIEP_VENDITA accounting snapshot."""
    return "RIEP_VENDITA" in (report.get("fogli_trovati") or [])


def create_import_history_router(*, get_database):
    router = APIRouter()

    async def record_import_history(file_name, report, backup_id=None):
        totals = report.get("totali", {})
        includes_accounting = import_includes_accounting(report)
        document = {
            "id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "file": file_name,
            "backup_id": backup_id,
            "tipo_import": report.get("tipo_import") or (
                "completo" if includes_accounting else "giacenze"
            ),
            "contabilita_inclusa": includes_accounting,
            "fogli_trovati": report.get("fogli_trovati", []),
            "fogli_mancanti": report.get("fogli_mancanti", []),
            "totali": totals,
            "errori": sum(
                (value or {}).get("errori", 0)
                for value in (report.get("dettaglio") or {}).values()
                if isinstance(value, dict)
            ),
        }
        await get_database().import_history.insert_one(document)
        return document

    async def latest_accounting_import():
        """Return the latest full import, including pre-flag legacy entries."""
        return await get_database().import_history.find_one(
            {
                "$or": [
                    {"contabilita_inclusa": True},
                    {"fogli_trovati": "RIEP_VENDITA"},
                ]
            },
            {"_id": 0},
            sort=[("created_at", -1)],
        )

    @router.get("/import/history")
    async def import_history(limit: int = 20):
        capped_limit = max(1, min(int(limit or 20), 100))
        return await get_database().import_history.find(
            {}, {"_id": 0}
        ).sort("created_at", -1).limit(capped_limit).to_list(capped_limit)

    return router, record_import_history, latest_accounting_import, import_history

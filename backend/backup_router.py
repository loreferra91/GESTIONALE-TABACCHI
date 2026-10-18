"""Backup snapshots, retention, export validation, and restore routes."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
import logging
import secrets
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pymongo.errors import DuplicateKeyError


BACKUP_COLLECTIONS = [
    "prodotti", "listino_adm", "vending", "storico_ordini", "parametri",
    "db_storico_vend", "db_storico_vending_ext", "vendite",
    "vendite_bulk_imports", "versamenti", "prelievi_vending",
    "scontrini_vending", "cassa_vending_stato", "vending_accounting_snapshots",
    "vending_accounting_state", "vending_accounting_events", "cassa",
    "ordini_fornitore", "ordini_fornitore_righe", "import_history", "adm_sync",
    "anomalie_ignorate", "smart_venue", "smart_venue_hidden",
    "smart_venue_values", "ricezioni_merce", "rettifiche_inventario",
    "rettifiche_venduti", "reconciliation_cases", "audit_events",
]
BACKUP_FILE_FORMAT = "gestionale-tabacchi-backup"
BACKUP_FILE_VERSION = 1
OPTIONAL_BACKUP_COLLECTIONS = {
    "vendite_bulk_imports", "scontrini_vending", "cassa_vending_stato",
    "vending_accounting_snapshots", "vending_accounting_state",
    "vending_accounting_events", "anomalie_ignorate", "smart_venue",
    "smart_venue_hidden", "smart_venue_values", "ricezioni_merce",
    "rettifiche_inventario", "rettifiche_venduti", "reconciliation_cases",
    "audit_events",
}
BACKUP_RETENTION_LIMIT = 3
BACKUP_RETENTION_POLICY_ID = "backup-retention-v1"
EXCEL_IMPORT_BACKUP_COLLECTIONS = [
    "prodotti", "listino_adm", "vending", "storico_ordini", "parametri",
    "db_storico_vend", "db_storico_vending_ext", "smart_venue",
    "smart_venue_hidden", "smart_venue_values",
]


def create_backup_router(
    *, get_database, session_kwargs, run_transactionally, read_capped,
):
    router = APIRouter()

    async def create_backup_snapshot(
        label: str,
        reason: str = "manuale",
        collection_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        database = get_database()
        snapshot_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        counts: Dict[str, int] = {}
        names = BACKUP_COLLECTIONS if collection_names is None else [
            name for name in collection_names if name in BACKUP_COLLECTIONS
        ]
        checksums: Dict[str, str] = {}
        await database.backup_snapshot_items.delete_many({"snapshot_id": snapshot_id})
        for collection_name in names:
            cursor = database[collection_name].find({}, {"_id": 0})
            digest = hashlib.sha256()
            count = 0
            pending = []

            async def store_document(document):
                nonlocal count, pending
                digest.update(json.dumps(
                    document, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), default=str,
                ).encode("utf-8"))
                digest.update(b"\n")
                pending.append({
                    "snapshot_id": snapshot_id,
                    "collection": collection_name,
                    "position": count,
                    "doc": document,
                })
                count += 1
                if len(pending) >= 1000:
                    await database.backup_snapshot_items.insert_many(pending)
                    pending = []

            try:
                async for document in cursor:
                    await store_document(document)
            except TypeError:
                for document in await cursor.to_list(None):
                    await store_document(document)
            if pending:
                await database.backup_snapshot_items.insert_many(pending)
            counts[collection_name] = count
            checksums[collection_name] = digest.hexdigest()

        meta = {
            "id": snapshot_id,
            "label": label[:180],
            "reason": reason[:80],
            "created_at": created_at,
            "counts": counts,
            "checksums": checksums,
            "total_docs": sum(counts.values()),
            "partial": collection_names is not None,
            "status": "READY",
        }
        await database.backup_snapshots.insert_one(meta.copy())
        migration_collection = getattr(database, "app_migrations", None)
        retention_enabled = await migration_collection.find_one(
            {"id": BACKUP_RETENTION_POLICY_ID}, {"_id": 0, "id": 1}
        ) if migration_collection is not None else None
        if retention_enabled:
            try:
                await prune_backup_snapshots(BACKUP_RETENTION_LIMIT)
            except Exception:
                logging.exception("Impossibile applicare la retention dei backup MongoDB")
        return meta

    async def backup_retention_preview(
        keep: int = BACKUP_RETENTION_LIMIT,
    ) -> Dict[str, Any]:
        keep = max(1, min(int(keep or BACKUP_RETENTION_LIMIT), 20))
        snapshots = await get_database().backup_snapshots.find(
            {"status": {"$ne": "PRUNED"}}, {"_id": 0}
        ).sort("created_at", -1).to_list(None)
        candidates = snapshots[keep:]
        return {
            "keep": keep,
            "snapshot_totali": len(snapshots),
            "snapshot_conservati": len(snapshots[:keep]),
            "snapshot_da_svuotare": len(candidates),
            "documenti_da_eliminare_stimati": sum(
                int(row.get("total_docs") or 0) for row in candidates
            ),
            "candidati": [{
                "id": row.get("id"), "label": row.get("label"),
                "reason": row.get("reason"), "created_at": row.get("created_at"),
                "total_docs": int(row.get("total_docs") or 0),
            } for row in candidates],
        }

    async def prune_backup_snapshots(
        keep: int = BACKUP_RETENTION_LIMIT,
    ) -> Dict[str, Any]:
        preview = await backup_retention_preview(keep)
        pruned_at = datetime.now(timezone.utc).isoformat()
        deleted_items = 0
        database = get_database()
        for candidate in preview["candidati"]:
            snapshot_id = candidate.get("id")
            if not snapshot_id:
                continue
            deletion = await database.backup_snapshot_items.delete_many(
                {"snapshot_id": snapshot_id}
            )
            deleted_items += int(getattr(deletion, "deleted_count", 0) or 0)
            await database.backup_snapshots.update_one(
                {"id": snapshot_id, "status": {"$ne": "PRUNED"}},
                {"$set": {
                    "status": "PRUNED", "pruned_at": pruned_at,
                    "pruned_docs": int(candidate.get("total_docs") or 0),
                }},
            )
        return {
            **preview,
            "snapshot_svuotati": len(preview["candidati"]),
            "documenti_eliminati": deleted_items,
            "pruned_at": pruned_at,
        }

    async def backup_documents(
        snapshot_id: str,
        collection_names: Optional[List[str]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        collections = {}
        names = BACKUP_COLLECTIONS if collection_names is None else collection_names
        database = get_database()
        for collection_name in names:
            items = await database.backup_snapshot_items.find(
                {"snapshot_id": snapshot_id, "collection": collection_name},
                {"_id": 0, "doc": 1},
            ).sort("position", 1).to_list(None)
            collections[collection_name] = [item["doc"] for item in items]
        return collections

    def validate_backup_file(payload: Any) -> Dict[str, List[Dict[str, Any]]]:
        if not isinstance(payload, dict):
            raise HTTPException(422, "Il file di backup non contiene un oggetto JSON valido")
        if payload.get("format") != BACKUP_FILE_FORMAT or payload.get("version") != BACKUP_FILE_VERSION:
            raise HTTPException(422, "Formato o versione del backup non riconosciuti")
        collections = payload.get("collections")
        if not isinstance(collections, dict):
            raise HTTPException(422, "Il backup non contiene le collezioni dati")
        missing = [
            name for name in BACKUP_COLLECTIONS
            if name not in collections and name not in OPTIONAL_BACKUP_COLLECTIONS
        ]
        unknown = [name for name in collections if name not in BACKUP_COLLECTIONS]
        if missing:
            raise HTTPException(422, f"Backup incompleto: mancano {', '.join(missing)}")
        if unknown:
            raise HTTPException(422, f"Backup non valido: collezioni sconosciute {', '.join(unknown)}")
        declared_counts = payload.get("counts") or {}
        declared_checksums = payload.get("checksums") or {}
        for collection_name, documents in collections.items():
            if not isinstance(documents, list):
                raise HTTPException(422, f"Collezione {collection_name} non valida")
            if any(not isinstance(doc, dict) or "_id" in doc for doc in documents):
                raise HTTPException(422, f"Documento non valido nella collezione {collection_name}")
            if collection_name in declared_counts and int(declared_counts[collection_name]) != len(documents):
                raise HTTPException(422, f"Conteggio non valido per la collezione {collection_name}")
            if collection_name in declared_checksums:
                digest = hashlib.sha256()
                for document in documents:
                    digest.update(json.dumps(
                        document, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), default=str,
                    ).encode("utf-8"))
                    digest.update(b"\n")
                if not secrets.compare_digest(
                    str(declared_checksums[collection_name]), digest.hexdigest()
                ):
                    raise HTTPException(422, f"Checksum non valido per la collezione {collection_name}")
        return collections

    async def restore_collections(collections, session=None):
        database = get_database()
        kwargs = session_kwargs(session)
        for collection_name in BACKUP_COLLECTIONS:
            if collection_name not in collections:
                continue
            await database[collection_name].delete_many({}, **kwargs)
            documents = collections[collection_name]
            for start in range(0, len(documents), 1000):
                await database[collection_name].insert_many(
                    documents[start:start + 1000], **kwargs
                )

    @asynccontextmanager
    async def restore_lock():
        database = get_database()
        token = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        await database.operation_locks.delete_many({
            "_id": "backup-restore", "expires_at": {"$lte": now},
        })
        try:
            await database.operation_locks.insert_one({
                "_id": "backup-restore", "token": token, "created_at": now,
                "expires_at": now + timedelta(hours=6),
            })
        except DuplicateKeyError as exc:
            raise HTTPException(409, "Un altro ripristino è già in corso") from exc
        try:
            yield
        finally:
            await database.operation_locks.delete_one(
                {"_id": "backup-restore", "token": token}
            )

    @router.post("/backup/create")
    async def backup_create(body: Optional[Dict[str, Any]] = None):
        label = str((body or {}).get("label") or f"Backup {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}")
        reason = str((body or {}).get("reason") or "manuale")
        return await create_backup_snapshot(label, reason)

    @router.get("/backup/retention-preview")
    async def backup_cleanup_preview(keep: int = BACKUP_RETENTION_LIMIT):
        return await backup_retention_preview(keep)

    @router.post("/backup/prune")
    async def backup_cleanup(keep: int = BACKUP_RETENTION_LIMIT):
        result = await prune_backup_snapshots(keep)
        await get_database().app_migrations.update_one(
            {"id": BACKUP_RETENTION_POLICY_ID},
            {"$set": {
                "id": BACKUP_RETENTION_POLICY_ID,
                "enabled_at": datetime.now(timezone.utc).isoformat(),
                "keep": result["keep"],
            }},
            upsert=True,
        )
        return {**result, "retention_enabled": True}

    @router.get("/backup")
    async def backup_list(limit: int = 20):
        capped = max(1, min(int(limit or 20), 100))
        return await get_database().backup_snapshots.find(
            {"status": {"$ne": "PRUNED"}}, {"_id": 0}
        ).sort("created_at", -1).limit(capped).to_list(capped)

    @router.get("/backup/{snapshot_id}/download")
    async def backup_download(snapshot_id: str):
        database = get_database()
        meta = await database.backup_snapshots.find_one(
            {"id": snapshot_id, "status": {"$ne": "PRUNED"}}, {"_id": 0}
        )
        if not meta:
            raise HTTPException(404, "backup non trovato")
        backed_up_names = list((meta.get("counts") or {}).keys())
        missing = [name for name in BACKUP_COLLECTIONS if name not in backed_up_names and name not in OPTIONAL_BACKUP_COLLECTIONS]
        if missing:
            if meta.get("partial"):
                raise HTTPException(409, "Questo è un backup automatico parziale: può essere ripristinato dall'app, ma non scaricato come backup completo.")
            raise HTTPException(409, "Questo backup è precedente al formato completo. Creane uno nuovo per scaricare anche vendite, cassa e ordini.")
        collections = await backup_documents(snapshot_id)
        payload = {
            "format": BACKUP_FILE_FORMAT, "version": BACKUP_FILE_VERSION,
            "created_at": meta.get("created_at"), "label": meta.get("label"),
            "counts": {name: len(docs) for name, docs in collections.items()},
            "checksums": meta.get("checksums") or {}, "collections": collections,
        }
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        timestamp = str(meta.get("created_at") or datetime.now(timezone.utc).isoformat())[:19].replace(":", "-")
        return StreamingResponse(
            io.BytesIO(content), media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="gestionale-backup-{timestamp}.json"'},
        )

    @router.post("/backup/{snapshot_id}/restore")
    async def backup_restore(snapshot_id: str):
        database = get_database()
        async with restore_lock():
            meta = await database.backup_snapshots.find_one(
                {"id": snapshot_id, "status": {"$ne": "PRUNED"}}, {"_id": 0}
            )
            if not meta:
                raise HTTPException(404, "backup non trovato")
            pre_restore = await create_backup_snapshot(
                f"Prima del ripristino {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}",
                "pre-restore",
            )
            names = [name for name in (meta.get("counts") or {}) if name in BACKUP_COLLECTIONS]
            collections = await backup_documents(snapshot_id, names)
            await run_transactionally(lambda session: restore_collections(collections, session))
            if "vending" in names:
                await database.vending.update_many({}, {"$set": {
                    "giacenza_aggiornata_il": meta.get("created_at"),
                    "giacenza_sorgente": "BACKUP_RIPRISTINATO",
                }})
            await database.backup_snapshots.update_one(
                {"id": snapshot_id},
                {"$set": {"last_restored_at": datetime.now(timezone.utc).isoformat()}},
            )
            return {"ok": True, "restored": snapshot_id, "pre_restore_backup": pre_restore["id"]}

    @router.post("/backup/restore-file")
    async def backup_restore_file(file: UploadFile = File(...)):
        raw = await read_capped(file)
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(422, f"File di backup non leggibile: {exc}")
        collections = validate_backup_file(payload)
        database = get_database()
        async with restore_lock():
            pre_restore = await create_backup_snapshot(
                f"Prima del ripristino file {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}",
                "pre-restore-file",
            )
            await run_transactionally(lambda session: restore_collections(collections, session))
            if "vending" in collections:
                await database.vending.update_many({}, {"$set": {
                    "giacenza_aggiornata_il": payload.get("created_at") or datetime.now(timezone.utc).isoformat(),
                    "giacenza_sorgente": "BACKUP_FILE_RIPRISTINATO",
                }})
            return {
                "ok": True, "file": file.filename,
                "restored_docs": sum(len(docs) for docs in collections.values()),
                "pre_restore_backup": pre_restore["id"],
            }

    return {
        "router": router,
        "create_backup_snapshot": create_backup_snapshot,
        "backup_retention_preview": backup_retention_preview,
        "prune_backup_snapshots": prune_backup_snapshots,
        "backup_documents": backup_documents,
        "validate_backup_file": validate_backup_file,
        "restore_collections": restore_collections,
        "restore_lock": restore_lock,
        "backup_create": backup_create,
        "backup_cleanup_preview": backup_cleanup_preview,
        "backup_cleanup": backup_cleanup,
        "backup_list": backup_list,
        "backup_download": backup_download,
        "backup_restore": backup_restore,
        "backup_restore_file": backup_restore_file,
    }

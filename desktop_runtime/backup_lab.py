"""Gate 4A: bounded encrypted logical backup/restore *laboratory only*.

NOT a desktop production backup or a substitute for mongodump/oplog backups.
Only isolated, marked rsDesktopCert databases are accepted. The source is
captured using one snapshot-read transaction. Restoration is into an empty
randomly named disposable namespace, never in-place.

The format is AES-256-GCM with scrypt KDF and authenticated BSON records.
The entire backup is bounded in memory to prevent untrusted allocation.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
import struct
from typing import Any

from bson import BSON
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from pymongo.read_concern import ReadConcern

MAGIC = b"GTBK-LAB\x01"
MAX_PLAINTEXT = 32 * 1024 * 1024
MAX_ARCHIVE = MAX_PLAINTEXT + 1024
MAX_RECORD = 16 * 1024 * 1024
VALID_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,62}$")


class BackupLaboratoryError(RuntimeError):
    pass

# Fail closed on collection types/options that this bounded logical lab cannot
# faithfully round-trip. Full production backups still require MongoDB tools.
_COLLECTION_OPTIONS = frozenset({
    "validator", "validationLevel", "validationAction", "collation",
    "capped", "size", "max",
})
_INDEX_OPTIONS = frozenset({
    "name", "unique", "sparse", "expireAfterSeconds",
    "partialFilterExpression", "collation", "hidden",
})


def _capture_metadata(db: Any, names: list[str]) -> dict:
    metadata: dict[str, dict] = {}
    info_by_name = {item["name"]: item for item in db.list_collections()}
    for name in names:
        info = info_by_name[name]
        if info.get("type") != "collection":
            raise BackupLaboratoryError("Views and non-standard collections unsupported in lab")
        options = dict(info.get("options", {}))
        if any(key not in _COLLECTION_OPTIONS for key in options):
            raise BackupLaboratoryError("Collection has unsupported options; refusing incomplete backup")
        indexes = []
        for index in db[name].list_indexes():
            if index["name"] == "_id_":
                continue
            details = dict(index)
            details.pop("v", None)
            keys = list(details.pop("key").items())
            if any(key not in _INDEX_OPTIONS for key in details):
                raise BackupLaboratoryError("Index has unsupported options; refusing incomplete backup")
            if not keys or "name" not in details:
                raise BackupLaboratoryError("Index definition is incomplete")
            indexes.append({"keys": [[k, v] for k, v in keys], "options": details})
        metadata[name] = {"options": options, "indexes": indexes}
    return metadata


def _check_metadata(metadata: Any, names: dict) -> None:
    if not isinstance(metadata, dict) or set(metadata) != set(names):
        raise BackupLaboratoryError("Missing or extra collection metadata")
    for collection, specs in metadata.items():
        if not isinstance(specs, dict) or set(specs) != {"options", "indexes"}:
            raise BackupLaboratoryError("Invalid collection metadata structure")
        if not isinstance(specs["options"], dict) or any(
            k not in _COLLECTION_OPTIONS for k in specs["options"]
        ):
            raise BackupLaboratoryError("Unsupported authenticated collection options")
        indexes = specs["indexes"]
        if not isinstance(indexes, list):
            raise BackupLaboratoryError("Invalid authenticated index list")
        index_names = set()
        for entry in indexes:
            if not isinstance(entry, dict) or set(entry) != {"keys", "options"}:
                raise BackupLaboratoryError("Invalid index metadata")
            keys, opts = entry["keys"], entry["options"]
            if (not isinstance(keys, list) or not keys or not isinstance(opts, dict)
                or not isinstance(opts.get("name"), str)
                or opts["name"] in index_names
                or any(k not in _INDEX_OPTIONS for k in opts)):
                raise BackupLaboratoryError("Invalid authenticated index options")
            index_names.add(opts["name"])
            if any(not isinstance(pair, list) or len(pair) != 2
                   or not isinstance(pair[0], str) for pair in keys):
                raise BackupLaboratoryError("Invalid authenticated index keys")


def _restore_indexes(db: Any, metadata: dict) -> None:
    for name, spec in metadata.items():
        for index in spec["indexes"]:
            db[name].create_index(
                [(key, order) for key, order in index["keys"]],
                **index["options"],
            )
    # Compare normalized indexes after the recreation, excluding MongoDB's
    # server-managed default _id index.
    if _capture_metadata(db, sorted(metadata)) != metadata:
        raise BackupLaboratoryError("Restored indexes/options differ from authenticated archive")


def _validate_name(name: str, prefix: str) -> None:
    if (not isinstance(name, str) or not name.startswith(prefix)
        or not VALID_NAME.fullmatch(name) or len(name) > 63):
        raise BackupLaboratoryError("Only an isolated disposable laboratory database is allowed")


def _check_topology(client: Any) -> None:
    hello = client.admin.command("hello")
    if (hello.get("setName") != "rsDesktopCert"
        or not hello.get("isWritablePrimary")
        or hello.get("logicalSessionTimeoutMinutes") is None):
        raise BackupLaboratoryError("Backup lab requires isolated rsDesktopCert primary with transactions")


def _check_source(db: Any) -> None:
    _validate_name(db.name, "desktop_backup_cert_")
    _check_topology(db.client)
    marker = db.sync_metadata.find_one({"_id": "backup-laboratory"})
    if marker is None or marker.get("value") != "disposable-only":
        raise BackupLaboratoryError("Required explicit backup-laboratory marker is missing")


def _key(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or len(passphrase) < 16:
        raise BackupLaboratoryError("A backup passphrase of at least 16 characters is mandatory")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _frame(record: dict) -> bytes:
    raw = BSON.encode(record)
    if len(raw) > MAX_RECORD:
        raise BackupLaboratoryError("Record exceeds maximum supported size")
    return struct.pack(">I", len(raw)) + raw


def _encode_snapshot(db: Any) -> bytes:
    names = sorted(db.list_collection_names())
    if not names or any(not VALID_NAME.fullmatch(name) or name.startswith("system.") for name in names):
        raise BackupLaboratoryError("Unsupported or empty collection set")
    output = BytesIO()
    counts: dict[str, int] = {}
    # Snapshot reads share a single consistent view of all existing collections.
    with db.client.start_session() as session:
        with session.start_transaction(read_concern=ReadConcern("snapshot")):
            for name in names:
                counts[name] = 0
                for document in db[name].find({}, session=session):
                    output.write(_frame({"type": "document", "collection": name, "data": document}))
                    counts[name] += 1
                    if output.tell() > MAX_PLAINTEXT - 8192:
                        raise BackupLaboratoryError("Snapshot too large for bounded laboratory archive")
    payload = output.getvalue()
    metadata = _capture_metadata(db, names)
    manifest = {"type": "manifest", "version": 2, "source": db.name,
                "replica_set": "rsDesktopCert", "counts": counts,
                "metadata": metadata,
                "documents_sha256": sha256(payload).hexdigest(),
                "created_at": datetime.now(timezone.utc)}
    combined = _frame(manifest) + payload
    if len(combined) > MAX_PLAINTEXT:
        raise BackupLaboratoryError("Snapshot exceeds laboratory archive limit")
    return combined


def _parse_snapshot(raw: bytes) -> tuple[dict, dict[str, list[dict]]]:
    if len(raw) > MAX_PLAINTEXT or not raw:
        raise BackupLaboratoryError("Invalid or oversized snapshot")
    items: list[tuple[dict, bytes]] = []
    cursor = 0
    while cursor < len(raw):
        if len(raw) - cursor < 4:
            raise BackupLaboratoryError("Truncated BSON frame")
        count = struct.unpack(">I", raw[cursor:cursor + 4])[0]
        cursor += 4
        if not 5 <= count <= MAX_RECORD or count > len(raw) - cursor:
            raise BackupLaboratoryError("Invalid BSON frame length")
        blob = raw[cursor:cursor + count]
        cursor += count
        try:
            doc = BSON(blob).decode()
        except Exception as exc:
            raise BackupLaboratoryError("Invalid BSON record") from exc
        items.append((doc, struct.pack(">I", count) + blob))
    manifest = items[0][0]
    if (manifest.get("type") != "manifest" or manifest.get("version") != 2
        or manifest.get("replica_set") != "rsDesktopCert"
        or not isinstance(manifest.get("counts"), dict)
        or not isinstance(manifest.get("documents_sha256"), str)):
        raise BackupLaboratoryError("Invalid backup manifest")
    _validate_name(manifest.get("source"), "desktop_backup_cert_")
    names = manifest["counts"]
    if not names or any(not VALID_NAME.fullmatch(n) or type(c) is not int or c < 0
                        for n, c in names.items()):
        raise BackupLaboratoryError("Invalid collection counts")
    _check_metadata(manifest.get("metadata"), names)
    rows: dict[str, list[dict]] = {name: [] for name in names}
    digest = sha256()
    for obj, frame in items[1:]:
        collection = obj.get("collection")
        if obj.get("type") != "document" or collection not in rows or not isinstance(obj.get("data"), dict):
            raise BackupLaboratoryError("Invalid document frame")
        rows[collection].append(obj["data"])
        digest.update(frame)
    if digest.hexdigest() != manifest["documents_sha256"]:
        raise BackupLaboratoryError("Backup content checksum mismatch")
    if {name: len(docs) for name, docs in rows.items()} != names:
        raise BackupLaboratoryError("Archive collection counts mismatch")
    marker = next((x for x in rows.get("sync_metadata", [])
                   if x.get("_id") == "backup-laboratory"), None)
    if not marker or marker.get("value") != "disposable-only":
        raise BackupLaboratoryError("Source laboratory marker missing from archive")
    return manifest, rows


def read_encrypted_backup(path: Path, passphrase: str) -> tuple[dict, dict[str, list[dict]]]:
    path = Path(path)
    size = path.stat().st_size
    minimum = len(MAGIC) + 16 + 12 + 16
    if not minimum <= size <= MAX_ARCHIVE:
        raise BackupLaboratoryError("Invalid or oversized encrypted archive")
    content = path.read_bytes()
    if not content.startswith(MAGIC):
        raise BackupLaboratoryError("Invalid archive magic")
    offset = len(MAGIC)
    salt, nonce = content[offset:offset + 16], content[offset + 16:offset + 28]
    aad = content[:offset + 28]
    try:
        plaintext = AESGCM(_key(passphrase, salt)).decrypt(nonce, content[offset + 28:], aad)
    except InvalidTag as exc:
        raise BackupLaboratoryError("Wrong password or modified archive") from exc
    return _parse_snapshot(plaintext)


def encrypted_backup(db: Any, destination: Path, passphrase: str) -> dict:
    """Refuse to overwrite an existing path. No secrets or raw DB files are logged."""
    _check_source(db)
    plaintext = _encode_snapshot(db)
    salt, nonce = os.urandom(16), os.urandom(12)
    aad = MAGIC + salt + nonce
    encrypted = aad + AESGCM(_key(passphrase, salt)).encrypt(nonce, plaintext, aad)
    destination = Path(destination)
    if not destination.parent.is_dir() or destination.is_symlink():
        raise BackupLaboratoryError("Backup destination parent must exist and be a regular directory")
    fd = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(encrypted)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    manifest, _ = read_encrypted_backup(destination, passphrase)
    return {"path": str(destination), "bytes": len(encrypted),
            "collections": manifest["counts"], "verified": True}


def restore_into_empty_lab(db: Any, source: Path, passphrase: str) -> dict:
    """Decrypt/authenticate fully before touching the target. No drops or overwrites."""
    _validate_name(db.name, "desktop_restore_cert_")
    _check_topology(db.client)
    manifest, rows = read_encrypted_backup(source, passphrase)
    if db.list_collection_names():
        raise BackupLaboratoryError("Target is not empty; restore refuses overwrite")
    metadata = manifest["metadata"]
    for name in rows:
        db.create_collection(name, **metadata[name]["options"])
    # A failure in any collection aborts all inserted documents, leaving only
    # empty collections in the disposable restore namespace for inspection.
    with db.client.start_session() as session:
        with session.start_transaction():
            for name, documents in rows.items():
                if documents:
                    db[name].insert_many(documents, ordered=True, session=session)
    _restore_indexes(db, metadata)
    observed = {name: db[name].count_documents({}) for name in rows}
    if observed != manifest["counts"]:
        raise BackupLaboratoryError("Restored collection counts differ from authenticated archive")
    # Count verification is a minimum. A full logical hash comparison follows.
    readback = BytesIO()
    for name in sorted(rows):
        for document in db[name].find({}):
            readback.write(_frame({"type": "document", "collection": name, "data": document}))
    if sha256(readback.getvalue()).hexdigest() != manifest["documents_sha256"]:
        raise BackupLaboratoryError("Restored content digest mismatch")
    return {"restored": True, "destination": db.name, "counts": observed,
            "source": manifest["source"], "verified": True}

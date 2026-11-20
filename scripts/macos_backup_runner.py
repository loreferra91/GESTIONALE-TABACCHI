"""macOS production MongoDB backup runner; no plaintext archive or CLI secrets.

Run by launchd as the logged-in user. Requires a public GPG encryption key,
MongoDB Database Tools, and a user-created macOS Keychain password item.
Never modifies Atlas and never deletes historical backup archives.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import shutil
import subprocess
import sys
import tempfile
import uuid

try:
    from .encrypted_backup import backup
    from .backup_health import check_backup
except ImportError:  # Direct invocation from the repository's scripts directory
    from encrypted_backup import backup
    from backup_health import check_backup

DATABASE = "gestionale"
ACCOUNT = "gestionale_backup_reader_prod"
SERVICE = "it.gestionale.tabacchi.mongodb.backup"
FINGERPRINT = "5270DA0DC9C353FC55DAA45EC32735EE65573B62"
URI = f"mongodb+srv://{ACCOUNT}@cluster0.wwhyhan.mongodb.net/?authSource=admin"


def _require_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError("Private directory must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("Invalid private directory")
    if path.stat().st_uid != os.getuid():
        raise RuntimeError("Private directory is not owned by current user")
    if path.stat().st_mode & 0o077:
        raise RuntimeError("Private directory permissions must be 0700")


def read_keychain_password() -> str:
    # Password is captured via stdout pipe, never passed on a command line or printed.
    result = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-a", ACCOUNT,
         "-s", SERVICE, "-w"],
        capture_output=True, text=True, timeout=40, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Keychain item unavailable; verify service, account and access")
    password = result.stdout.removesuffix("\n")
    if not password or "\n" in password or "\r" in password:
        raise RuntimeError("Keychain password missing or unsupported")
    return password


@contextmanager
def private_mongodump_config(secret: str, temp_root: Path):
    _require_private_directory(temp_root)
    with tempfile.TemporaryDirectory(prefix="mongo-config-", dir=temp_root) as dirname:
        config = Path(dirname) / "mongodump.yaml"
        # JSON strings are valid YAML scalars; this safely escapes arbitrary passwords.
        # The URI contains the public username and host, never the password.
        with config.open("x", encoding="utf-8") as handle:
            os.chmod(config, 0o600)
            handle.write(f"uri: {json.dumps(URI)}\n")
            handle.write(f"password: {json.dumps(secret)}\n")
            handle.flush()
            os.fsync(handle.fileno())
        yield config
    # TemporaryDirectory removes the sensitive config after use, including errors.


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def mirror_to_removable_volume(archive: Path, record: dict, volume: Path) -> Path:
    """Copy only encrypted bytes to an actually mounted volume; never overwrite."""
    if volume.is_symlink() or not volume.is_mount():
        raise RuntimeError("Configured removable backup volume is not mounted")
    directory = volume / "Gestionale-Backups"
    if directory.is_symlink():
        raise RuntimeError("Remote backup directory must not be a symlink")
    directory.mkdir(exist_ok=True)
    target = directory / archive.name
    if target.is_symlink():
        raise RuntimeError("Remote target must not be a symlink")
    if target.exists():
        if target.stat().st_size != record["bytes"] or sha256(target) != record["sha256"]:
            raise RuntimeError("Remote file exists with different contents; not overwriting")
        return target
    partial = directory / f".{archive.name}.{uuid.uuid4().hex}.partial"
    try:
        with archive.open("rb") as source, partial.open("xb") as destination:
            shutil.copyfileobj(source, destination, 1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        if partial.stat().st_size != record["bytes"] or sha256(partial) != record["sha256"]:
            raise RuntimeError("Removable backup verification failed")
        if target.exists() or target.is_symlink():
            raise RuntimeError("Remote target appeared concurrently; refusing overwrite")
        os.rename(partial, target)
        if sha256(target) != record["sha256"]:
            raise RuntimeError("Removable backup read-back verification failed")
        return target
    finally:
        partial.unlink(missing_ok=True)


def should_skip_recent_backup(output_dir: Path, threshold_hours: float = 12) -> bool:
    return check_backup(str(output_dir), max_age_hours=threshold_hours, verify_hash=True)["backup_fresh"]


def run_backup(*, output_dir: Path, temp_root: Path, lock_dir: Path,
               usb_volume: Path | None, force: bool = False, on_stage=None) -> dict:
    stage = on_stage or (lambda name: None)
    stage("local_directories")
    _require_private_directory(output_dir)
    _require_private_directory(temp_root)
    _require_private_directory(lock_dir)
    stage("acquire_lock")
    lock_file = lock_dir / "production-backup.lock"
    with lock_file.open("a+b") as lock:
        os.chmod(lock_file, 0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Backup already running on this Mac") from exc
        stage("freshness_check")
        if not force and should_skip_recent_backup(output_dir):
            result = {"status": "recent_verified_backup_present", "backup_created": False}
            if usb_volume is not None:
                record = json.loads((output_dir / "last-success.json").read_text(encoding="utf-8"))
                # check_backup verified the name, length, and hash above.
                archive = output_dir / Path(record["filename"]).name
                stage("usb_copy")
                result["usb_path"] = str(mirror_to_removable_volume(archive, record, usb_volume))
                result["usb_verified"] = True
            return result
        stage("toolchain")
        for binary in ("mongodump", "gpg"):
            if not shutil.which(binary):
                raise RuntimeError(f"Required tool unavailable: {binary}")
        stage("keychain_access")
        password = read_keychain_password()
        stage("temporary_credentials")
        with private_mongodump_config(password, temp_root) as config:
            password = ""  # Do not retain an additional reference longer than needed.
            stage("mongodump_gpg")
            record = backup(db_name=DATABASE, config_file=str(config),
                            output_dir=str(output_dir), recipient=FINGERPRINT)
        archive = output_dir / record["filename"]
        stage("local_integrity")
        health = check_backup(str(output_dir), max_age_hours=1, verify_hash=True)
        if not health["backup_fresh"] or sha256(archive) != record["sha256"]:
            raise RuntimeError("Local backup post-write verification failed")
        result = {"status": "local_verified", "backup_created": True,
                  "file": record["filename"], "sha256": record["sha256"],
                  "bytes": record["bytes"], "restore_certified": False,
                  "consistency": "not_transactionally_certified"}
        if usb_volume is not None:
            stage("usb_copy")
            target = mirror_to_removable_volume(archive, record, usb_volume)
            result["usb_verified"] = True
            result["usb_path"] = str(target)
        else:
            result["usb_verified"] = False
        return result


def local_diagnostics(output_dir: Path, temp_root: Path, lock_dir: Path) -> dict:
    """Read-only environment inventory. Never prints or retrieves a secret."""
    def private_dir_state(path: Path) -> str:
        if path.is_symlink():
            return "unsafe_symlink"
        if not path.exists():
            return "not_created_yet"
        if not path.is_dir() or path.stat().st_uid != os.getuid():
            return "wrong_type_or_owner"
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            return "permissions_not_private"
        return "ok"

    tools_ok = {name: shutil.which(name) is not None for name in ("mongodump", "gpg")}
    gpg_key = False
    if tools_ok["gpg"]:
        try:
            keys = subprocess.run(["gpg", "--batch", "--with-colons", "--list-keys", FINGERPRINT],
                                  capture_output=True, text=True, timeout=20, check=False)
            gpg_key = (keys.returncode == 0 and any(
                row.startswith("fpr:") and row.split(":")[9].upper() == FINGERPRINT
                for row in keys.stdout.splitlines()))
        except (OSError, subprocess.TimeoutExpired):
            pass
    keychain_found = False
    try:
        item = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", ACCOUNT,
                               "-s", SERVICE], capture_output=True, text=True,
                              timeout=20, check=False)
        keychain_found = item.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {
        "status": "local_diagnostics_only",
        "tools_available": tools_ok,
        "gpg_public_key_found": gpg_key,
        "keychain_item_found": keychain_found,
        "directories": {
            "backup": private_dir_state(output_dir),
            "temporary": private_dir_state(temp_root),
            "locks": private_dir_state(lock_dir),
        },
        "atlas_credentials_verified": False,
        "mongodb_connection_tested": False,
    }


def notify_failure():
    # Best effort only; no filenames, secrets or exception details in notifications.
    try:
        subprocess.run(["/usr/bin/osascript", "-e",
                        'display notification "Controlla il registro dei backup" with title "Backup Gestionale: ERRORE"'],
                       check=False, timeout=6, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-production", action="store_true")
    parser.add_argument("--force", action="store_true", help="Ignore freshness gate")
    parser.add_argument("--usb-volume", type=Path,
                        help="Optional mounted USB volume; failure if missing")
    parser.add_argument("--preflight", action="store_true", help="No backup and no Keychain access")
    parser.add_argument("--diagnose", action="store_true",
                        help="Read-only local checks without displaying or retrieving passwords")
    args = parser.parse_args(argv)
    if not args.allow_production:
        parser.error("Production backup requires explicit --allow-production")
    home = Path.home()
    output_dir = home / "Gestionale-Backups"
    state_dir = home / "Library/Application Support/GestionaleTabacchiBackup"
    temp_root = state_dir / "tmp"
    lock_dir = state_dir / "locks"
    try:
        if args.preflight:
            print(json.dumps({"status": "preflight_only", "database": DATABASE,
                              "keychain_service": SERVICE,
                              "tools_available": {b: bool(shutil.which(b)) for b in ("mongodump", "gpg")}},
                             sort_keys=True))
            return 0
        if args.diagnose:
            print(json.dumps(local_diagnostics(output_dir, temp_root, lock_dir), sort_keys=True))
            return 0
        last_stage = ["startup"]
        result = run_backup(output_dir=output_dir, temp_root=temp_root,
                            lock_dir=lock_dir, usb_volume=args.usb_volume,
                            force=args.force, on_stage=lambda stage: last_stage.__setitem__(0, stage))
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        # Never print underlying exception messages: tools could embed credentials.
        stage = last_stage[0] if "last_stage" in locals() else "startup"
        print(f"ERROR [{stage}]: production backup was not fully verified", file=sys.stderr)
        notify_failure()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

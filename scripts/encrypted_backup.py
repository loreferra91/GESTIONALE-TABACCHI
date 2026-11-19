"""Create encrypted *independent* MongoDB archives; never restores production.

Requires mongodump, GnuPG, a pre-provisioned 0600 mongodump YAML config,
and a verified recipient public key. No plaintext dump touches local disk.
The caller must supply a persistent OFF-CLUSTER destination.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid


def _validate_config(config_file: Path):
    if not config_file.is_file():
        raise RuntimeError("mongodump configuration file missing")
    if stat.S_IMODE(config_file.stat().st_mode) & 0o077:
        raise RuntimeError("mongodump configuration permissions must be 0600")


def backup(*, db_name: str, config_file: str, output_dir: str, recipient: str,
           mongodump_cmd: str = "mongodump", gpg_cmd: str = "gpg",
           gpg_home: str | None = None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", db_name):
        raise ValueError("Invalid database name")
    if not re.fullmatch(r"[A-Fa-f0-9]{40,64}", recipient):
        raise ValueError("Recipient must be a pinned full GPG fingerprint")
    config = Path(config_file)
    _validate_config(config)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(destination.stat().st_mode) & 0o077:
        raise RuntimeError("Backup directory permissions must be 0700")

    created_at = datetime.now(timezone.utc).isoformat()
    stem = f"{db_name}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:12]}"
    filename = f"{stem}.archive.gz.gpg"
    partial = destination / f".{filename}.partial"
    final = destination / filename
    manifest_partial = destination / ".last-success.json.partial"
    dump_args = [mongodump_cmd, "--config", str(config), "--db", db_name,
                 "--archive", "--gzip"]
    gpg_args = [gpg_cmd, "--batch", "--no-tty", "--yes", "--trust-model", "always",
                "--encrypt", "--recipient", recipient]
    if gpg_home:
        gpg_args[1:1] = ["--homedir", str(gpg_home)]
    try:
        with partial.open("xb") as destination_file:
            os.chmod(partial, 0o600)
            # Never place a MongoDB connection URI in command-line arguments.
            with subprocess.Popen(dump_args, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL) as dump:
                with subprocess.Popen(gpg_args, stdin=dump.stdout, stdout=destination_file,
                                      stderr=subprocess.DEVNULL) as encrypt:
                    dump.stdout.close()
                    encrypted_code = encrypt.wait()
                    dump_code = dump.wait()
            destination_file.flush()
            os.fsync(destination_file.fileno())
        if dump_code != 0 or encrypted_code != 0 or partial.stat().st_size < 32:
            raise RuntimeError("Backup or encryption failed; no success record written")
        with partial.open("rb") as encrypted_file:
            checksum = hashlib.file_digest(encrypted_file, "sha256").hexdigest()
        record = {
            "created_at": created_at,
            "database": db_name,
            "status": "encrypted",
            "filename": filename,
            "sha256": checksum,
            "bytes": partial.stat().st_size,
            "restore_certified": False,
            "consistency": "not_transactionally_certified",
        }
        os.replace(partial, final)
        try:
            with manifest_partial.open("x", encoding="utf-8") as stream:
                os.chmod(manifest_partial, 0o600)
                json.dump(record, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(manifest_partial, destination / "last-success.json")
        except Exception:
            # Do not claim a new successful backup if its manifest was not
            # atomically published. The encrypted file remains inspectable.
            raise
        return record
    finally:
        partial.unlink(missing_ok=True)
        manifest_partial.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-name", required=True)
    parser.add_argument("--mongodump-config", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--gpg-home")
    parser.add_argument("--allow-production", action="store_true",
                        help="Explicit approval gate for the 'gestionale' database")
    args = parser.parse_args()
    if args.db_name == "gestionale" and not args.allow_production:
        parser.error("Production backups require explicit --allow-production")
    record = backup(db_name=args.db_name, config_file=args.mongodump_config,
                    output_dir=args.output_dir, recipient=args.recipient,
                    gpg_home=args.gpg_home)
    # No sensitive URI or key material is emitted.
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()

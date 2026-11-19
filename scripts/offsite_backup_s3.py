"""Off-cluster, encrypted S3 backup with independent read-back verification.

The remote manifest is published only after an encrypted archive has been
downloaded from off-cluster storage and its size and SHA-256 verified.
Never uploads plaintext or secrets. This is NOT a transactional snapshot
or an automatic restore certification.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

try:
    from .encrypted_backup import backup
except ImportError:
    from encrypted_backup import backup


BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
PREFIX_RE = re.compile(r"^[a-zA-Z0-9/_-]+$")


def s3_location(bucket, prefix):
    """Permit only canonical bucket and prefix; reject URLs and shell strings."""
    if not isinstance(bucket, str) or not BUCKET_RE.fullmatch(bucket):
        raise ValueError("Invalid S3 bucket")
    if not isinstance(prefix, str) or not PREFIX_RE.fullmatch(prefix):
        raise ValueError("Invalid S3 prefix")
    normalized = prefix.strip("/")
    if not normalized or any(component in {"", ".", ".."} for component in normalized.split("/")):
        raise ValueError("Invalid S3 prefix path")
    return f"s3://{bucket}/{normalized}"


def _run_aws(*args):
    subprocess.run(["aws", *args], check=True, capture_output=True, text=True)


def _sha256_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def upload_encrypted_backup(*, db_name, config_file, recipient, bucket, prefix):
    if db_name not in {"gestionale_staging", "gestionale"}:
        raise ValueError("Only explicit staging or production database names are supported")
    destination = s3_location(bucket, prefix)
    with tempfile.TemporaryDirectory(prefix="gestionale-offsite-") as tmp:
        temporary_dir = Path(tmp)
        record = backup(
            db_name=db_name, config_file=config_file,
            output_dir=str(temporary_dir), recipient=recipient,
        )
        encrypted = temporary_dir / record["filename"]
        if not encrypted.is_file() or encrypted.stat().st_size != record["bytes"]:
            raise RuntimeError("Encrypted archive changed before external upload")
        if _sha256_file(encrypted) != record["sha256"]:
            raise RuntimeError("Encrypted archive checksum changed before upload")

        archive_key = f"{destination}/{record['filename']}"
        manifest_key = f"{destination}/last-success.json"
        # No bearer tokens, database URI or plaintext passed as command args.
        _run_aws("s3", "cp", str(encrypted), archive_key, "--only-show-errors",
                 "--sse", "AES256")
        downloaded = temporary_dir / "verified-remote.archive.gpg"
        _run_aws("s3", "cp", archive_key, str(downloaded), "--only-show-errors")
        if downloaded.stat().st_size != record["bytes"] or _sha256_file(downloaded) != record["sha256"]:
            raise RuntimeError("Off-cluster archive read-back verification failed")

        # Publish last-success only AFTER remote readback. The previous
        # success manifest remains untouched if upload/readback fails.
        verified_record = {
            **record,
            "offcluster_verified": True,
            "offcluster_destination": archive_key,
            "offcluster_verified_at": datetime.now(timezone.utc).isoformat(),
            "restore_certified": False,
        }
        manifest = temporary_dir / "verified-last-success.json"
        manifest.write_text(json.dumps(verified_record, sort_keys=True), encoding="utf-8")
        manifest.chmod(0o600)
        _run_aws("s3", "cp", str(manifest), manifest_key, "--only-show-errors",
                 "--sse", "AES256")
        # Confirm exact remote manifest bytes as well.
        returned_manifest = temporary_dir / "verified-last-success-remote.json"
        _run_aws("s3", "cp", manifest_key, str(returned_manifest), "--only-show-errors")
        if _sha256_file(manifest) != _sha256_file(returned_manifest):
            raise RuntimeError("Remote backup success manifest verification failed")
        return {
            "database": db_name, "offcluster_verified": True,
            "archive_sha256": record["sha256"], "archive_bytes": record["bytes"],
            "archive_key": archive_key, "manifest_key": manifest_key,
            "restore_certified": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-name", choices=("gestionale_staging", "gestionale"), required=True)
    parser.add_argument("--mongodump-config", required=True)
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--s3-bucket", required=True)
    parser.add_argument("--s3-prefix", required=True)
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args()
    if args.db_name == "gestionale" and not args.allow_production:
        parser.error("Production off-cluster backups require explicit --allow-production")
    summary = upload_encrypted_backup(
        db_name=args.db_name, config_file=args.mongodump_config,
        recipient=args.recipient, bucket=args.s3_bucket, prefix=args.s3_prefix,
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()

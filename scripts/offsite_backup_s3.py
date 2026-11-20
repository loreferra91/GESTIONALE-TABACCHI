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
from urllib.parse import urlsplit

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


BACKBLAZE_ENDPOINT_RE = re.compile(r"^s3\\.[a-z0-9]+(?:-[a-z0-9]+)*\\.backblazeb2\\.com$")
BACKBLAZE_FREE_GUARD_BYTES = 8 * 1024**3  # reserve headroom below provider's 10 GB free allowance


def validate_endpoint(endpoint):
    """Only accept HTTPS regional Backblaze S3 endpoints or AWS default."""
    if endpoint is None:
        return None
    try:
        parsed = urlsplit(endpoint)
        hostname = (parsed.hostname or "").lower()
        allowed = (
            parsed.scheme == "https"
            and BACKBLAZE_ENDPOINT_RE.fullmatch(hostname)
            and not parsed.username and not parsed.password
            and parsed.port is None and not parsed.path
            and not parsed.query and not parsed.fragment
        )
    except (TypeError, ValueError):
        allowed = False
    if not allowed:
        raise ValueError("Only the official regional Backblaze HTTPS S3 endpoint is accepted")
    return endpoint


def _run_aws(*args, endpoint=None):
    cmd = ["aws"]
    if endpoint:
        cmd.extend(["--endpoint-url", endpoint])
    cmd.extend(args)
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _remote_version_bytes(bucket, *, endpoint):
    """Conservative S3 version inventory including historical object versions.

    A dedicated private bucket is required. This does not account for other
    buckets in the account; operator B2 caps and alerts remain mandatory.
    """
    result = _run_aws(
        "s3api", "list-object-versions", "--bucket", bucket,
        "--output", "json", endpoint=endpoint,
    )
    try:
        data = json.loads(result.stdout)
        versions = data.get("Versions", [])
        if not isinstance(versions, list):
            raise ValueError("Bad versions payload")
        sizes = [x["Size"] for x in versions]
        if any(type(size) is not int or size < 0 for size in sizes):
            raise ValueError("Bad object size")
        return sum(sizes)
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Unable to verify full remote B2 storage inventory") from None


def _sha256_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def upload_encrypted_backup(*, db_name, config_file, recipient, bucket, prefix, endpoint=None):
    if db_name not in {"gestionale_staging", "gestionale"}:
        raise ValueError("Only explicit staging or production database names are supported")
    destination = s3_location(bucket, prefix)
    endpoint = validate_endpoint(endpoint)

    def aws_call(*args):
        if endpoint:
            return _run_aws(*args, endpoint=endpoint)
        return _run_aws(*args)
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
        if endpoint:
            # Backblaze has a perpetual 10 GB free storage allowance.
            # Account-level usage may include other buckets and multipart uploads;
            # this 8 GiB *bucket* cap is an additional guard, not a billing guarantee.
            stored = _remote_version_bytes(bucket, endpoint=endpoint)
            if stored + record["bytes"] + 16_384 > BACKBLAZE_FREE_GUARD_BYTES:
                raise RuntimeError("B2 free storage safety budget exceeded; no archive uploaded")
        # No bearer tokens, database URI or plaintext passed as command args.
        aws_call("s3", "cp", str(encrypted), archive_key, "--only-show-errors",
                 "--sse", "AES256")
        downloaded = temporary_dir / "verified-remote.archive.gpg"
        aws_call("s3", "cp", archive_key, str(downloaded), "--only-show-errors")
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
        aws_call("s3", "cp", str(manifest), manifest_key, "--only-show-errors",
                 "--sse", "AES256")
        # Confirm exact remote manifest bytes as well.
        returned_manifest = temporary_dir / "verified-last-success-remote.json"
        aws_call("s3", "cp", manifest_key, str(returned_manifest), "--only-show-errors")
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
    parser.add_argument(
        "--s3-endpoint",
        help="Official https://s3.REGION.backblazeb2.com endpoint; omit for AWS S3",
    )
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args()
    if args.db_name == "gestionale" and not args.allow_production:
        parser.error("Production off-cluster backups require explicit --allow-production")
    summary = upload_encrypted_backup(
        db_name=args.db_name, config_file=args.mongodump_config,
        recipient=args.recipient, bucket=args.s3_bucket, prefix=args.s3_prefix,
        endpoint=args.s3_endpoint,
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()

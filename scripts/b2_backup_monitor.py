"""Read-only Backblaze B2 backup freshness and remote-object existence check.

The check never downloads or decrypts the archive, mutates storage, or contacts
MongoDB. A HEAD response is NOT a replacement for an isolated restore drill.
Credentials are supplied to the installed AWS CLI via its private local profile.
"""
import argparse
from datetime import datetime, timezone
import json
import re
import subprocess

from scripts.offsite_backup_s3 import s3_location, validate_endpoint


ARCHIVE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,160}\.archive\.gz\.gpg$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
VALID_DBS = frozenset({"gestionale_staging", "gestionale"})


def _aws(arguments, *, endpoint):
    try:
        response = subprocess.run(
            ["aws", "--endpoint-url", endpoint, *arguments],
            capture_output=True, text=True, timeout=45, check=True,
        )
    except (OSError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as exc:
        # Never emit CLI stderr; it can contain sensitive configuration.
        raise RuntimeError("Remote B2 verification unavailable") from None
    return response.stdout


def _parse_time(value):
    if not isinstance(value, str):
        raise ValueError("Missing timestamp")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("Timestamp has no timezone")
    return instant.astimezone(timezone.utc)


def check_remote_backup(*, db_name, bucket, prefix, endpoint,
                        max_age_hours=36.0, now=None, aws_call=None):
    """Return only non-sensitive status; fail closed on incomplete evidence."""
    result = {
        "backup_fresh": False, "offcluster_verified": False,
        "archive_present": False, "archive_size_verified": False,
        "restore_certified": False, "reason": "verification_failed",
    }
    try:
        if db_name not in VALID_DBS or not 0 < max_age_hours <= 168:
            raise ValueError("Invalid monitoring arguments")
        location = s3_location(bucket, prefix)
        if not endpoint:
            raise ValueError("Regional B2 endpoint required")
        validate_endpoint(endpoint)
        clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        execute = aws_call or _aws
        raw = execute(["s3", "cp", location + "/last-success.json",
                       "-", "--only-show-errors"], endpoint=endpoint)
        if not isinstance(raw, str) or len(raw) > 16_384:
            raise ValueError("Invalid manifest size")
        manifest = json.loads(raw)
        filename = manifest["filename"]
        archive_key = location + "/" + filename
        size = manifest["bytes"]
        verified_at = _parse_time(manifest["offcluster_verified_at"])
        created_at = _parse_time(manifest["created_at"])
        if (
            not isinstance(filename, str)
            or not ARCHIVE_RE.fullmatch(filename)
            or type(size) is not int or size <= 0
            or manifest["database"] != db_name
            or manifest["status"] != "encrypted"
            or manifest["offcluster_verified"] is not True
            or manifest["offcluster_destination"] != archive_key
            or not SHA256_RE.fullmatch(manifest["sha256"])
            or created_at > verified_at
        ):
            raise ValueError("Manifest integrity fields inconsistent")
        result["offcluster_verified"] = True
        result["restore_certified"] = manifest.get("restore_certified") is True
        age_hours = (clock - verified_at).total_seconds() / 3600
        if not -5 / 60 <= age_hours <= max_age_hours:
            result["reason"] = "stale_or_future_manifest"
            return result
        result["backup_fresh"] = True
        key = archive_key.removeprefix("s3://" + bucket + "/")
        raw_head = execute(
            ["s3api", "head-object", "--bucket", bucket,
             "--key", key, "--output", "json"],
            endpoint=endpoint,
        )
        head = json.loads(raw_head)
        result["archive_present"] = True
        result["archive_size_verified"] = (
            type(head.get("ContentLength")) is int
            and head["ContentLength"] == size
        )
        if not result["archive_size_verified"]:
            result["backup_fresh"] = False
            result["reason"] = "remote_archive_size_mismatch"
        else:
            result["reason"] = "ok"
    except (ValueError, KeyError, TypeError, RuntimeError,
            json.JSONDecodeError, OverflowError):
        result["backup_fresh"] = False
        result["reason"] = "verification_failed"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-name", choices=sorted(VALID_DBS), required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--max-age-hours", type=float, default=36)
    args = parser.parse_args()
    result = check_remote_backup(
        db_name=args.db_name, bucket=args.bucket, prefix=args.prefix,
        endpoint=args.endpoint, max_age_hours=args.max_age_hours,
    )
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["backup_fresh"] else 2)


if __name__ == "__main__":
    main()

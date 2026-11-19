"""Read-only freshness and integrity check for independently encrypted backups."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def check_backup(directory: str, max_age_hours: float, verify_hash: bool = False) -> dict:
    if max_age_hours <= 0:
        raise ValueError("max_age_hours must be positive")
    base = Path(directory)
    try:
        record = json.loads((base / "last-success.json").read_text(encoding="utf-8"))
        created = datetime.fromisoformat(record["created_at"])
        age = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        filename = Path(record["filename"]).name
        encrypted = base / filename
        size_valid = encrypted.is_file() and encrypted.stat().st_size == record["bytes"]
        hash_valid = None
        if size_valid and verify_hash:
            with encrypted.open("rb") as stream:
                hash_valid = hashlib.file_digest(stream, "sha256").hexdigest() == record["sha256"]
        fresh = (
            0 <= age <= max_age_hours
            and record["status"] == "encrypted"
            and size_valid
            and hash_valid is not False
        )
        return {
            "backup_fresh": fresh, "archive_present": size_valid,
            "hash_valid": hash_valid, "restore_certified": False,
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {"backup_fresh": False, "archive_present": False,
                "hash_valid": None, "restore_certified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--max-age-hours", type=float, required=True)
    parser.add_argument("--verify-hash", action="store_true")
    args = parser.parse_args()
    result = check_backup(args.directory, args.max_age_hours, args.verify_hash)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["backup_fresh"] else 2)


if __name__ == "__main__":
    main()

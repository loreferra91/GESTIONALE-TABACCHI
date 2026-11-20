"""Independently upload and restore-test an existing macOS GPG MongoDB archive.

Never connects to Atlas. Reads a locally verified encrypted archive, uploads
encrypted bytes to a private Backblaze B2 bucket, downloads and checks SHA-256,
then restores into a disposable, network-isolated local MongoDB container.
No remote deletion and no plaintext archive saved on the Mac.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

DEFAULT_BUCKET = "gestionale-tabacchi-recovery-2026"
DEFAULT_ENDPOINT = "https://s3.eu-central-003.backblazeb2.com"
DEFAULT_PROFILE = "gestionale-b2"
RESTORE_DB = "gestionale_b2_restore_cert"
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_local_archive(directory: Path) -> tuple[Path, dict]:
    """Use the writer's verified manifest, never a guessed latest file."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Backup directory is absent or is a symlink")
    manifest = directory / "last-success.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Verified backup manifest missing")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("Invalid backup manifest")
    name = record.get("filename")
    expected_hash = record.get("sha256")
    expected_size = record.get("bytes")
    if (not isinstance(name, str) or Path(name).name != name
            or name.startswith(".") or not name.endswith(".archive.gz.gpg")):
        raise ValueError("Invalid encrypted archive filename")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_hash):
        raise ValueError("Invalid backup SHA-256")
    if type(expected_size) is not int or expected_size <= 0:
        raise ValueError("Invalid backup byte count")
    archive = directory / name
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("Encrypted archive missing or symlinked")
    if archive.stat().st_size != expected_size or digest(archive) != expected_hash.lower():
        raise ValueError("Encrypted archive differs from verified manifest")
    return archive, record


def validate_target(bucket: str, endpoint: str, profile: str) -> None:
    if not BUCKET_RE.fullmatch(bucket):
        raise ValueError("Invalid bucket name")
    if not re.fullmatch(r"https://s3\.[a-z0-9-]+\.backblazeb2\.com", endpoint):
        raise ValueError("Only an official Backblaze regional HTTPS endpoint is allowed")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", profile):
        raise ValueError("Invalid AWS CLI profile")


def aws_call(profile: str, endpoint: str, *args: str, timeout: int = 240) -> str:
    command = ["aws", "--profile", profile, "--endpoint-url", endpoint, *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        # Never print underlying AWS errors: they may contain sensitive data.
        raise RuntimeError("B2 operation failed at " + args[0])
    return result.stdout


def remote_roundtrip(archive: Path, record: dict, *, bucket: str,
                     endpoint: str, profile: str, destination: Path) -> tuple[Path, str]:
    aws_call(profile, endpoint, "s3api", "head-bucket", "--bucket", bucket)
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    key = f"gestionale/production/recovery-drills/{date}/{uuid.uuid4().hex}-{archive.name}"
    target = f"s3://{bucket}/{key}"
    # Random new key ensures normal retries never overwrite a retained backup.
    aws_call(profile, endpoint, "s3", "cp", str(archive), target,
             "--only-show-errors", "--sse", "AES256",
             "--metadata", f"sha256={record['sha256']}", timeout=300)
    stat_raw = aws_call(profile, endpoint, "s3api", "head-object",
                        "--bucket", bucket, "--key", key)
    metadata = json.loads(stat_raw)
    if metadata.get("ContentLength") != record["bytes"]:
        raise RuntimeError("Remote size check failed")
    if metadata.get("Metadata", {}).get("sha256", "").lower() != record["sha256"].lower():
        raise RuntimeError("Remote metadata hash mismatch")
    downloaded = destination / "downloaded-from-b2.archive.gz.gpg"
    aws_call(profile, endpoint, "s3", "cp", target, str(downloaded),
             "--only-show-errors", timeout=300)
    if downloaded.stat().st_size != record["bytes"] or digest(downloaded) != record["sha256"].lower():
        raise RuntimeError("B2 downloaded archive differs from local verified archive")
    return downloaded, key


def docker_call(*args: str, timeout: int = 30) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True,
                            timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError("Isolated Docker recovery step failed")
    return result.stdout


def restore_isolated(downloaded: Path) -> dict:
    if not shutil.which("gpg") or not shutil.which("docker"):
        raise RuntimeError("Docker and GPG are required for independent restore")
    container = "gestionale-b2-restore-" + uuid.uuid4().hex[:12]
    started = False
    try:
        docker_call("run", "--detach", "--rm", "--name", container,
                    "--network", "none", "mongo:8.0", "--bind_ip", "127.0.0.1", timeout=90)
        started = True
        for _ in range(40):
            ready = subprocess.run(["docker", "exec", container, "mongosh", "--quiet",
                                    "--eval", "db.adminCommand({ping:1}).ok"],
                                   capture_output=True, text=True, timeout=10, check=False)
            if ready.returncode == 0 and ready.stdout.strip() == "1":
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable MongoDB failed to start")
        decrypt = subprocess.Popen(["gpg", "--decrypt", str(downloaded)], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
        try:
            restore = subprocess.run(
                ["docker", "exec", "-i", container, "mongorestore", "--archive", "--gzip",
                 "--nsFrom=gestionale.*", f"--nsTo={RESTORE_DB}.*", "--stopOnError"],
                stdin=decrypt.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=540, check=False,
            )
        finally:
            if decrypt.stdout:
                decrypt.stdout.close()
        decrypt_code = decrypt.wait(timeout=60)
        if restore.returncode or decrypt_code:
            raise RuntimeError("Decryption or isolated mongorestore failed")
        verify_js = """
            const d=db.getSiblingDB('gestionale_b2_restore_cert');
            const names=d.getCollectionNames().filter(n => !n.startsWith('system.'));
            let docs=0, indexes=0;
            names.forEach(n => { const c=d.getCollection(n); docs += c.countDocuments({}); indexes += c.getIndexes().length; });
            const result={collections:names.length, documents:docs, indexes:indexes,
                prodotti:d.prodotti.countDocuments({}), vendite:d.vendite.countDocuments({}),
                audit_events:d.audit_events.countDocuments({})};
            print(JSON.stringify(result));
        """
        lines = docker_call("exec", container, "mongosh", "--quiet", "--eval", verify_js,
                            timeout=120).splitlines()
        summary = json.loads(lines[-1])
        # Based on 20 Nov 2026 operational baseline: 38 collections, ~139,800 docs.
        # Safety floors detect unexpectedly incomplete restores, not transactional consistency.
        if (summary["collections"] < 35 or summary["documents"] < 100_000
                or summary["prodotti"] < 200 or summary["vendite"] < 4500
                or summary["audit_events"] < 500
                or summary["indexes"] <= summary["collections"]):
            raise RuntimeError("Recovered document/index counts below safety floors")
        return summary
    finally:
        if started:
            subprocess.run(["docker", "rm", "-f", container],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30, check=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path,
                        default=Path.home() / "Backup-Gestionale-Tabacchi")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--upload-and-restore", action="store_true",
                        help="Explicitly upload encrypted bytes to B2 and restore from download")
    args = parser.parse_args(argv)
    try:
        validate_target(args.bucket, args.endpoint, args.profile)
        archive, record = checked_local_archive(args.directory.expanduser())
        print(json.dumps({"step": "local_verified", "archive": archive.name,
                          "bytes": record["bytes"], "sha256": record["sha256"]}, sort_keys=True))
        if not args.upload_and_restore:
            print("PREFLIGHT OK: nothing uploaded or restored; use --upload-and-restore")
            return 0
        for binary in ("aws", "gpg", "docker"):
            if not shutil.which(binary):
                raise RuntimeError(f"Required tool unavailable: {binary}")
        with tempfile.TemporaryDirectory(prefix="gestionale-b2-verify-") as temp:
            downloaded, key = remote_roundtrip(archive, record, bucket=args.bucket,
                                                endpoint=args.endpoint,
                                                profile=args.profile,
                                                destination=Path(temp))
            print(json.dumps({"step": "b2_readback_verified", "bucket": args.bucket,
                              "object_key": key, "sha256": digest(downloaded)}, sort_keys=True))
            summary = restore_isolated(downloaded)
        print(json.dumps({"status": "B2_REMOTE_RESTORE_VERIFIED", "restore": summary,
                          "note": "Logical isolated restore; live dump is not a transactional snapshot"},
                         sort_keys=True))
        return 0
    except Exception as error:
        # Never show AWS CLI stderr, credentials or database documents.
        print(f"ERROR [{type(error).__name__}]: B2 proof NOT completed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

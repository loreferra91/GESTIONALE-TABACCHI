"""Safely relocate verified encrypted backups and reschedule an existing macOS LaunchAgent.

Never deletes source archives, never touches MongoDB, and never overwrites a
conflicting destination. Requires an existing LaunchAgent and installed runner
supporting --output-dir. Run once on the operator-owned Mac.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import uuid

LABEL = "it.gestionale.tabacchi.backup"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def private_dir(path: Path, create: bool = False) -> None:
    if path.is_symlink():
        raise RuntimeError("Backup directory cannot be a symlink")
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir() or path.stat().st_uid != os.getuid():
        raise RuntimeError("Backup directory is missing or not owned by this user")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError("Backup directory permissions must be private (0700)")


def source_files(source: Path) -> list[Path]:
    private_dir(source)
    archives = sorted(source.glob("*.archive.gz.gpg"))
    if not archives:
        raise RuntimeError("No encrypted source archives found")
    for file in archives:
        if file.is_symlink() or not file.is_file() or file.stat().st_size == 0:
            raise RuntimeError("Invalid source archive")
    manifest = source / "last-success.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise RuntimeError("Missing or unsafe backup success manifest")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    name = record.get("filename")
    if not isinstance(name, str) or Path(name).name != name:
        raise RuntimeError("Unsafe manifest archive name")
    recent = source / name
    if recent not in archives or record.get("bytes") != recent.stat().st_size:
        raise RuntimeError("Manifest points to a missing or incompatible archive")
    if record.get("sha256") != digest(recent):
        raise RuntimeError("Latest backup checksum mismatch")
    return archives + [manifest]


def copy_verified(source: Path, destination: Path) -> int:
    files = source_files(source)
    private_dir(destination, create=True)
    copied = 0
    for original in files:
        target = destination / original.name
        if target.is_symlink():
            raise RuntimeError("Unsafe destination file")
        source_hash = digest(original)
        if target.exists():
            if not target.is_file() or target.stat().st_size != original.stat().st_size or digest(target) != source_hash:
                raise RuntimeError("Conflicting destination: refusing overwrite")
            continue
        temporary = destination / f".{original.name}.{uuid.uuid4().hex}.partial"
        try:
            with original.open("rb") as inp, temporary.open("xb") as out:
                os.chmod(temporary, 0o600)
                shutil.copyfileobj(inp, out, 1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
            if temporary.stat().st_size != original.stat().st_size or digest(temporary) != source_hash:
                raise RuntimeError("Copied data failed verification")
            os.link(temporary, target)
            copied += 1
            if digest(target) != source_hash:
                raise RuntimeError("Destination failed read-back verification")
        finally:
            temporary.unlink(missing_ok=True)
    return copied


def prepared_plist(plist: Path, destination: Path) -> bytes:
    if plist.is_symlink() or not plist.is_file():
        raise RuntimeError("Existing LaunchAgent plist missing or symlinked")
    config = plistlib.loads(plist.read_bytes())
    if config.get("Label") != LABEL or not isinstance(config.get("ProgramArguments"), list):
        raise RuntimeError("LaunchAgent identity does not match")
    argv = config["ProgramArguments"]
    if (len(argv) < 3 or "macos_backup_runner.py" not in argv[1]
            or "--allow-production" not in argv):
        raise RuntimeError("Unexpected LaunchAgent command; refusing changes")
    if "--output-dir" in argv:
        i = argv.index("--output-dir")
        if i + 1 >= len(argv):
            raise RuntimeError("Malformed output directory argument")
        argv[i + 1] = str(destination)
    else:
        argv.extend(["--output-dir", str(destination)])
    config["StartCalendarInterval"] = {"Hour": 11, "Minute": 0}
    return plistlib.dumps(config)


def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/launchctl", *args], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, check=check)


def apply(*, home: Path, destination: Path) -> None:
    source = home / "Gestionale-Backups"
    plist = home / "Library/LaunchAgents" / f"{LABEL}.plist"
    if destination == source or destination.is_symlink() or destination.parent != home:
        raise RuntimeError("Destination must be a new, direct, private folder in your home directory")
    new_plist = prepared_plist(plist, destination)
    copies = copy_verified(source, destination)
    source_files(destination)
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    current = launchctl("print", service, check=False)
    was_loaded = current.returncode == 0
    if was_loaded and re.search(r"^\s*state\s*=\s*running\s*$", current.stdout, re.M):
        raise RuntimeError("LaunchAgent is currently running; retry after it has finished")
    backup = plist.with_name(plist.name + ".before-1100-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".bak")
    with backup.open("xb") as out:
        os.chmod(backup, 0o600)
        out.write(plist.read_bytes())
        out.flush()
        os.fsync(out.fileno())
    temp = plist.with_name(f".{plist.name}.{uuid.uuid4().hex}.partial")
    try:
        with temp.open("xb") as out:
            os.chmod(temp, 0o600)
            out.write(new_plist)
            out.flush()
            os.fsync(out.fileno())
        if was_loaded:
            launchctl("bootout", domain, str(plist))
        os.replace(temp, plist)
        try:
            launchctl("bootstrap", domain, str(plist))
        except subprocess.CalledProcessError:
            os.replace(backup, plist)
            if was_loaded:
                launchctl("bootstrap", domain, str(plist), check=False)
            raise RuntimeError("New LaunchAgent failed to load; restored previous configuration")
    finally:
        temp.unlink(missing_ok=True)
    print(f"NEW_FOLDER: {destination}")
    print("SCHEDULE: 11:00 local time")
    print(f"ARCHIVES_AND_MANIFEST_VERIFIED: {len(source_files(destination))}")
    print(f"COPIED_NEW_FILES: {copies}")
    print(f"PREVIOUS_CONFIGURATION: {backup}")
    print("LAUNCHAGENT: loaded")
    print("OLD_BACKUPS: preserved")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Execute verified copy and launchd configuration update")
    args = parser.parse_args()
    home = Path.home()
    destination = home / "Backup-Gestionale-Tabacchi"
    try:
        if not args.apply:
            files = source_files(home / "Gestionale-Backups")
            prepared_plist(home / "Library/LaunchAgents" / f"{LABEL}.plist", destination)
            print(f"PREFLIGHT OK: {len(files) - 1} encrypted archives and valid manifest")
            print(f"TARGET: {destination}")
            print("SCHEDULE: 11:00 local time; use --apply to change anything")
            return 0
        apply(home=home, destination=destination)
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

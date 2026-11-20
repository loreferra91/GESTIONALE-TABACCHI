"""Opt-in LaunchAgent installer: daily 03:00 Europe/Rome system local time.

Installer writes only a plist and log directory, never starts a backup.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import os
import plistlib
import sys

LABEL = "it.gestionale.tabacchi.backup"


def launch_agent(*, python: Path, runner: Path, logs: Path,
                 hour: int = 3, minute: int = 0, usb_volume: Path | None = None) -> dict:
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError("Invalid schedule")
    if not python.is_file() or not runner.is_file() or runner.is_symlink():
        raise ValueError("Python executable or runner missing")
    args = [str(python.resolve()), str(runner.resolve()), "--allow-production"]
    if usb_volume is not None:
        args.extend(["--usb-volume", str(usb_volume)])
    return {"Label": LABEL, "ProgramArguments": args,
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},
            "RunAtLoad": True, "KeepAlive": False,
            "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
            "StandardOutPath": str(logs / "backup.stdout.log"),
            "StandardErrorPath": str(logs / "backup.stderr.log")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Write plist, but do not load it")
    parser.add_argument("--hour", type=int, default=3)
    parser.add_argument("--minute", type=int, default=0)
    parser.add_argument("--usb-volume", type=Path, help="Optional mounted USB destination")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    home = Path.home()
    logs = home / "Library/Logs/GestionaleTabacchiBackup"
    output = home / "Library/LaunchAgents" / f"{LABEL}.plist"
    agent = launch_agent(python=Path(sys.executable), runner=root / "macos_backup_runner.py",
                         logs=logs, hour=args.hour, minute=args.minute, usb_volume=args.usb_volume)
    if not args.install:
        print("PREFLIGHT OK: LaunchAgent prepared but not installed or loaded")
        print(f"Schedule: {args.hour:02}:{args.minute:02} local time; RunAtLoad enabled")
        return 0
    if output.is_symlink() or output.exists():
        raise RuntimeError("LaunchAgent already exists; will not overwrite")
    if logs.is_symlink():
        raise RuntimeError("Log path must not be a symlink")
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    if logs.stat().st_mode & 0o077:
        raise RuntimeError("Log directory permissions must be 0700")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        os.chmod(output, 0o600)
        plistlib.dump(agent, stream)
    print(f"INSTALLED: {output}")
    print(f"Activate manually: launchctl bootstrap gui/$(id -u) '{output}'")
    print("No backup started; production and staging unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

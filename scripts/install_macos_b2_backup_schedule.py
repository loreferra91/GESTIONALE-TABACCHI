"""Opt-in macOS LaunchAgent installer for a free locally-run backup schedule.

This does not run any backup and has no Atlas/Backblaze API access.
It deliberately refuses missing or world-readable configuration files.
"""
import argparse
import os
from pathlib import Path
import plistlib
import stat


def make_launch_agent(*, shell_runner: Path, config: Path, log_dir: Path,
                      hour: int = 2, minute: int = 30) -> dict:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Invalid local schedule")
    if shell_runner.is_symlink() or not shell_runner.is_file():
        raise ValueError("Shell backup runner not available")
    if config.is_symlink() or not config.is_file():
        raise ValueError("Private backup configuration not found")
    if stat.S_IMODE(config.stat().st_mode) != 0o600:
        raise ValueError("Private backup configuration must have 0600 permissions")
    if config.stat().st_uid != os.getuid():
        raise ValueError("Backup configuration belongs to another account")
    return {
        "Label": "it.gestionale.tabacchi.backup",
        "ProgramArguments": ["/bin/bash", str(shell_runner.resolve())],
        "EnvironmentVariables": {"GESTIONALE_BACKUP_ENV": str(config.resolve())},
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(log_dir / "backup.out.log"),
        "StandardErrorPath": str(log_dir / "backup.err.log"),
        "RunAtLoad": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=Path.home() / ".config/gestionale-tabacchi/b2-backup.env")
    parser.add_argument("--hour", type=int, default=2)
    parser.add_argument("--minute", type=int, default=30)
    parser.add_argument("--install", action="store_true",
                        help="Write a LaunchAgent plist; does not start it")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    runner = repo / "scripts/run_local_b2_backup.sh"
    log_dir = Path.home() / ".config/gestionale-tabacchi/logs"
    agent = make_launch_agent(
        shell_runner=runner, config=args.config, log_dir=log_dir,
        hour=args.hour, minute=args.minute,
    )
    if not args.install:
        print("Configuration verified. Pass --install to create the local LaunchAgent.")
        return
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    dst = Path.home() / "Library/LaunchAgents/it.gestionale.tabacchi.backup.plist"
    if dst.exists() or dst.is_symlink():
        raise RuntimeError("LaunchAgent already exists; refusing to overwrite it")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("xb") as out:
        os.chmod(dst, 0o600)
        plistlib.dump(agent, out)
    print("Local LaunchAgent configured. It will only run while this Mac can execute scheduled jobs.")
    print("Load it yourself with: launchctl bootstrap gui/$(id -u) "
          + str(dst))
    print("No credentials were printed and no backup was started.")


if __name__ == "__main__":
    main()

"""Produce a native PyInstaller sidecar with the Tauri-required target suffix.

Runs on the target OS/CPU only. PyInstaller does not cross-compile between
Apple Silicon and Intel; the GitHub Actions matrix supplies native runners.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "desktop-app" / "src-tauri" / "binaries"
VALID_TARGETS = {
    "x86_64-pc-windows-msvc": "win32",
    "aarch64-apple-darwin": "darwin",
    "x86_64-apple-darwin": "darwin",
}


def installable_target(target: str, platform: str) -> None:
    if VALID_TARGETS.get(target) != platform:
        raise RuntimeError("Native PyInstaller build requires matching target OS")
    if target == "aarch64-apple-darwin" and sys.platform == "darwin":
        import platform as machine
        if machine.machine().lower() not in {"arm64", "aarch64"}:
            raise RuntimeError("ARM64 PyInstaller must run on Apple Silicon")
    if target == "x86_64-apple-darwin" and sys.platform == "darwin":
        import platform as machine
        if machine.machine().lower() not in {"x86_64", "amd64"}:
            raise RuntimeError("Intel PyInstaller must run on Intel macOS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=sorted(VALID_TARGETS))
    args = parser.parse_args()
    installable_target(args.target, sys.platform)
    if not (ROOT / "frontend" / "build" / "index.html").is_file():
        raise RuntimeError("Frontend build must exist before PyInstaller")
    command = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--noconfirm", "--clean",
        "--name", "gestionale-runtime",
        "--paths", str(ROOT),
        "--hidden-import", "backend.server",
        "--collect-submodules", "keyring.backends",
        "--collect-submodules", "uvicorn",
        "--collect-data", "reportlab",
        "--collect-data", "pdfplumber",
        "--add-data", f"{ROOT / 'frontend' / 'build'}:frontend/build",
        "--distpath", str(ROOT / "desktop-app" / ".pyinstaller-dist"),
        "--workpath", str(ROOT / "desktop-app" / ".pyinstaller-build"),
        "--specpath", str(ROOT / "desktop-app" / ".pyinstaller-spec"),
        str(ROOT / "desktop_runtime" / "packaged_entry.py"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    executable = ROOT / "desktop-app" / ".pyinstaller-dist" / (
        "gestionale-runtime.exe" if sys.platform == "win32" else "gestionale-runtime"
    )
    if not executable.is_file():
        raise RuntimeError("PyInstaller reported success but sidecar binary is missing")
    BIN.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    output = BIN / f"gestionale-runtime-{args.target}{suffix}"
    shutil.copyfile(executable, output)
    output.chmod(output.stat().st_mode | 0o111)
    subprocess.run([str(output), "--self-test"], cwd=ROOT, check=True, timeout=60)
    print("Validated frozen sidecar:", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

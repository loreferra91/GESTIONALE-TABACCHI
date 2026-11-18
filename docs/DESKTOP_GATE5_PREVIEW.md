# Gate 5A — Tauri 2 native diagnostic preview installers

**NOT the finished Gestionale Tabacchi application. NOT usable for business operations.** No safe automatic Python/MongoDB sidecar is bundled. The GUI only probes the existing separately launched, isolated FastAPI prototype at `127.0.0.1:18085` and, after a response matching the expected app name and status, opens it in a separate native webview.

## Source paths

- `desktop-app/src-tauri`: Rust / Tauri 2 shell, two narrowly scoped commands and a loopback-only probe; commands do not accept URLs or shell strings.
- `desktop-app/ui`: standalone static diagnostic page, not the business React UI.
- `desktop-app/scripts/generate_icons.py`: deterministic icons generated at build time.
- `.github/workflows/desktop-preview.yml`: branch-only native builds and short-lived artifacts; no public release, no deploy or production secrets.

## Installer outputs (when the build succeeds)

- Windows x64: NSIS `*-setup.exe`.
- macOS Apple Silicon ARM64: `.dmg`.
- macOS Intel x64: `.dmg`.

Each GitHub Actions job uploads a separate downloadable **workflow artifact**. A successful workflow is required before stating any installer exists. The preview may be unsigned on Windows and ad-hoc signed on macOS; OS security prompts may appear. Only install these packages for diagnosis and testing, not daily tobacco shop use.

## How to open the local backend

1. Launch the Tauri preview. It shows a warning and **does not** start the backend or connect to the cloud.
2. If you separately installed Python, MongoDB and frontend dependencies and explicitly created a test-only local environment, start `python -m desktop_runtime.run_local` from the repository checkout. **Do not import real store data.**
3. Use 'Verifica servizio' and 'Apri il gestionale locale'. The Rust layer checks `GET http://127.0.0.1:18085/api/` for HTTP 200 and the app identity before opening a second window.
4. The loopback health check is **not authentication**: another local process could imitate this response or replace the port between check and connection. Never run on an untrusted/shared OS user. The second webview is not granted shell or administrative permissions.

## Not yet implemented

- Bundled, signed Python sidecar + deterministic startup/shutdown.
- Automatic authenticated MongoDB initialization on Windows and macOS, keychain-tested enrollment, embedded database binary/licensing and secure IPC.
- Full inventory authority enforced by all offline writers; robust bidirectional cloud synchronization, conflict resolution UI, external backup and recovery.
- Signed/notarized installers and native QA including power-loss, upgrades, permissions and installer rollback.
- Actual offline use without prerequisites; current installer is a **diagnostic launcher only**.

## Release decision

The PR remains **DRAFT**. The workflow has `contents: read`, uploads artifacts with 7-day retention and cannot publish a release or touch Render/Atlas. No merge to `main` is permitted.

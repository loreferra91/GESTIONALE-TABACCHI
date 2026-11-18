# Gate 5B native Python sidecar installer — laboratory

**This is experimental. It does NOT contain MongoDB or a working cloud-sync
engine. Never use real tobacco store records.** The original web app on Render,
the production MongoDB Atlas database and `main` are untouched.

Two parallel installer workflows are intentional:

- `desktop-preview.yml` builds the old standalone **diagnostic-only** GUI.
- `desktop-sidecar-lab.yml` builds a separate **runtime lab** installer.
  On each native target runner, PyInstaller freezes `desktop_runtime/packaged_entry.py`,
  bundles the compiled React frontend and Python backend, validates
  `--self-test` **without networking**, and produces a target-suffixed
  `src-tauri/binaries/gestionale-runtime-<target>` for the Tauri
  `bundle.externalBin` override.

Use the official GitHub Actions artifact link only after the relevant job
is green and an artifact is listed. No release publication or signing keys
are provided.

## Runtime behavior

The diagnostic screen offers an explicit **Avvia backend integrato (lab)**
button. Rust uses the Tauri shell plugin internally, with a fixed sidecar
name and fixed `--serve` argument; arbitrary process invocation is forbidden.
Closing the main window terminates only the Python child process. No
MongoDB process is created or killed.

For `--serve` to work, you must independently set up the Gate 4B
authenticated MongoDB preview on the target PC, leave it running on loopback
port 27028, and have the native OS keychain credentials provisioned.
The backend binds only to `127.0.0.1:18085` and enables the strict
offline-write guard.

This step removes a manual Python command from the workflow **only if**
the authenticated database prerequisites are already satisfied. There
is no self-contained database or automatic secure provisioning yet.

## Build verification != runtime certification

The CI proves a frozen binary can locate the React build and backend module
without opening the database. It does not certify live authenticated MongoDB
startup inside the installer, account prompts, crash isolation on macOS/Windows,
data backups, webview ownership binding or full hybrid synchronization.

PyInstaller must run on the native CPU architecture for Apple Silicon and
Intel macOS. Do not copy an Intel frozen executable into an ARM bundle.
Until verified, retain these binaries only as 7-day GitHub Actions
artifacts. Never merge the draft PR.

# Desktop Gate 1 — persistent local replica set (prototype)

This is an **experimental local-only launcher**, not an installer, not synchronized with Atlas, not certified for real shop usage. Original Render, main and production settings remain untouched. It preserves data across restarts in an OS-specific user directory; it does NOT load fixture Excel files automatically.

## Prerequisites

- Python 3.12, production Python dependencies (`pip install -r backend/requirements-prod.txt`).
- Node.js 20 and Yarn 1.22 for an initial `cd frontend && yarn install --frozen-lockfile && yarn build`.
- A separately installed **MongoDB Community Server** `mongod` binary available on PATH. It is not bundled. Verify licensing and distribution conditions before shipping installers.
- The operating system must allow loopback binding to ports 27028 (MongoDB) and 18085 (FastAPI). Both ports must be free.

From repository root:

```bash
python -m desktop_runtime.run_local
```

Windows PowerShell uses the same Python module command after installing the prerequisites. The Python prototype is cross-platform but has **not** yet been validated on actual Windows/macOS devices.

## What it does

1. Creates a persistent directory: macOS `~/Library/Application Support/GestionaleTabacchiDesktop`, Windows `%LOCALAPPDATA%\\GestionaleTabacchiDesktop`, Linux `~/.local/share/gestionale-tabacchi-desktop`. For disposable test data only, override with `GESTIONALE_DESKTOP_DATA_DIR`.
2. Generates a persistent random Basic Auth secret in `auth.secret` with restrictive create permissions; prints its value for prototype login only (remove this behavior before release). Does not store Atlas credentials.
3. Launches an **owned** mongod bound to 127.0.0.1 with replica-set ID `rsGestionaleDesktop`; refuses to attach to an occupied MongoDB port.
4. Initializes replica set on first use, checks for writable PRIMARY, performs a real MongoDB transaction probe. Refuses startup if any gate fails.
5. Launches existing `backend.server:app` bound to loopback, pointing only at local `gestionale_desktop` and disabling agent integration, legacy Excel imports, and automatic Auto-Order 3.1 confirmations. Opens the UI after a successful API health check.
6. On Ctrl-C, terminates only the MongoDB and FastAPI subprocesses it launched, leaving the persistent database files untouched.

No cloud credentials, synchronization, deployment, write replay or data migration are introduced. Tests: `python -m pytest backend/tests/test_desktop_local_runtime.py backend/tests/test_desktop_sync_protocol.py -q`.

## Explicit security and recovery limitations

- MongoDB is **loopback-only but unauthenticated** in this prototype. Any local process under the same host may attempt to access it. Do not use with genuine business records. Harden database authorization and encrypt secrets before pilot.
- No verified encrypted automated external backup/restore yet. Do not run financially important workloads. Existing application-level MongoDB snapshots do not replace recoverable external backups.
- If port 27028 is already occupied, startup stops rather than connecting to an unknown instance. If an earlier crashed process owns the port, stop that process manually after checking ownership; never blindly kill processes by port.
- A stored replica-set configuration tied to the initial port may fail if you later change ports; use the same port unless you have a controlled replica-set reconfiguration and backup.
- A corrupt data directory, unclean shutdown, lack of disk space or unsupported mongod version should halt startup for operator intervention, not delete data or reset the replica set.
- Startup probing creates a collection `desktop_health_probes` in local data. No business collections in Atlas are accessed.
- Browser authentication currently uses shared Basic Auth; the final Tauri sidecar will require a per-launch token, a restricted local API, operating-system keychain storage, and a read-only desktop readiness API.
- Current business mutations still use the existing local-environment fallback logic. **This prototype is not yet eligible for transactional offline production writes**. Gate 2 must make the whole desktop write path fail closed on transaction errors and persist the outbox atomically before business testing.

## Gate 1 acceptance still pending

Real MongoDB startup/restart tests on both OSes, forced crash/restore tests, verified encrypted backups, secure packaging, process ownership checks, and fully integrated local security tests. The scripts and mocked tests alone cannot certify those requirements.

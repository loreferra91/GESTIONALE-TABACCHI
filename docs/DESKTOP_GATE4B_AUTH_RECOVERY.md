# Gate 4B — authenticated replica / crash recovery certificate (disposable only)

This adds a **keyfile/authentication provisioning contract and CI lab**. It does not migrate or turn on authentication for the existing desktop launcher.

- A MongoDB 7 second disposable container starts with `--replSet rsDesktopAuthCert --keyFile ... --auth`. Keyfile is generated with restrictive file permissions and retained in a temporary Docker volume.
- Through the MongoDB localhost bootstrap exception, the first administrator is created; a separate `desktop_api` user receives ONLY `readWrite` on the disposable test DB.
- The CI lab commits two outbox-like records with a real transaction, creates an index, SIGKILLs `mongod` (simulated abrupt power loss of MongoDB), starts it again and verifies authenticated transactions, two intact events, preserved index and refused unauthenticated reads.
- The keyfile helper explicitly refuses unsafe permissions, symlinks and implicit overwrite/rotation.
- No credentials are committed for real devices. The static CI-only credentials are valid solely for the disposable MongoDB lab.

## Why the current launcher is unchanged

The existing `desktop_runtime.run_local` still runs a *non-authenticated* MongoDB prototype. Enabling mandatory auth in place could make an existing installation inaccessible. Before a live desktop switch, the app must have secure OS keychain storage (Windows Credential Manager and macOS Keychain), a formally tested fresh-setup flow and migration/rollback plan for any existing local data. Never automatically upgrade the legacy MongoDB data directory.

## Limitations

- SIGKILL recovery tested in Linux Docker, not Windows/macOS power loss.
- The experiment does not certify filesystem durability across actual sudden power failure, journal corruption, storage exhaustion, or backup consistency.
- This does not add remote sync or permit use of real store sales. The existing backup lab is still limited; indexes and collection options require a *separate full-fidelity backup/restore* implementation before release.
- Never merge the branch or deploy to Render solely because this certificate passes.

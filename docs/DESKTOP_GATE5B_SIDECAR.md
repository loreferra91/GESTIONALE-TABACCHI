# Gate 5B — experimental bundled FastAPI sidecar (fail-closed)

**Not yet the finished self-contained desktop product.** This stage packages the existing FastAPI backend and compiled React interface as a Python executable for Tauri to launch on demand. MongoDB remains a separate, already authenticated service managed outside the app. Nothing connects to Atlas.

## Security gates

The `--serve` path:
1. Requires the React production build and importable backend packaged in the frozen binary.
2. Ignores any inherited desktop data-directory override, requires an existing OS-specific user data directory and prevents connecting to an already occupied API port.
3. Requires the **existing** `secure-mongo-data` profile, a replica set `rsGestionaleDesktopSecure` listening on loopback port 27028 and validated credentials from the native macOS Keychain or Windows Credential Manager. It **never** creates users, starts mongod or migrates an unauthenticated database.
4. Reads a separate FastAPI Basic Auth credential from the native keychain; does not print it or place it in a file. The user must authenticate separately in the webview.
5. Forces desktop strict mode and disables non-instrumented write routes, agent integration, Excel imports and Auto-Order 3.1. The cloud sender is not run.
6. Starts Uvicorn in the sidecar process, not through `sys.executable -m uvicorn`, which would fail inside PyInstaller.

The `--self-test` path validates packaged assets only: no MongoDB, no network and no runtime data mutation.

## Limitations and release blockers

- **Full offline one-click startup is NOT yet achieved.** Existing authenticated mongod must already be running. The current Gate 4B secure profile is still experimental; real OS QA/backup restore is pending.
- The Rust Tauri GUI must own the sidecar process and close it when quitting; avoid orphaned database processes by not spawning mongod in this phase.
- Basic Auth credential enrollment/provisioning UI is not implemented. There is no read-only shadow mode for complete domain operations. Real shop data remain forbidden.
- PyInstaller native builds must be validated on each target. PyInstaller binaries cannot simply be cross-compiled from Intel to ARM via the Rust target triple.
- No installer or code signing claim until GitHub Actions uploads a verified artifact. Do not merge or deploy to Render.

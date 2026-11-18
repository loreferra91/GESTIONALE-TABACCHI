# Gate 4B — optional authenticated desktop preview

Experimental only. **Do not run with real tobacco shop records.** The opt-in
launcher switch is `GESTIONALE_DESKTOP_SECURE_PREVIEW=1` and requires the
desktop dependencies `pip install -r desktop_runtime/requirements-desktop.txt`.

## Security properties

- Mac: require the native **macOS Keychain** backend; Windows: require native
  **Credential Manager** backend. No plaintext credential-file fallback.
- Keyfile is generated once with restrictive permissions, kept separate from
  the previous unauthenticated MongoDB directory.
- On first installation only, the MongoDB internal keyfile enables auth,
  the localhost exception creates an administrative recovery user and a
  dedicated `desktop_api` user with readWrite on `gestionale_desktop` only.
- The app connection uses that account and requires real replica-set
  transactions. Credentials are stored in the OS credential store, but are
  passed to the FastAPI subprocess through its environment; this remains a
  prototype limitation until an authenticated sidecar IPC design is in place.
- Any earlier `mongo-data` directory or partial secure setup blocks startup.
  This path must NEVER auto-migrate, reset, overwrite or silently import data.

## Known limitations

- Only the Linux **disposable** keyfile/RBAC/crash-restart certificate has run.
  Native Windows Credential Manager and macOS Keychain tests are NOT complete.
- The current FastAPI Basic Auth and API loopback transport are not considered
  a completed desktop security model. Authentication for the GUI sidecar must
  be implemented separately.
- The backup lab is still a bounded research tool; the authenticated preview
  does **not** schedule full external production backups.
- Existing non-secure prototype remains the default so the experiment cannot
  accidentally change past local data. The preview is explicitly opt-in.
- Windows/macOS installers, production credential lifecycle, audited recovery
  and stock authority are pending. Do not merge into main or deploy to Render.

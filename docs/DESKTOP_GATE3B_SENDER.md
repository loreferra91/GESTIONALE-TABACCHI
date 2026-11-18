# Desktop Gate 3B — explicit manual sender to staging (not cloud sync)

Development branch only. The sender is a standalone **opt-in diagnostic CLI** and does not run as a background agent or in the desktop launcher.

- Default execution is `--dry-run` and **does not send network traffic**, nor modify outbox records.
- `--send` requires `DESKTOP_SYNC_STAGING_ALLOW_SEND=1`, a loopback-only staging URL, an actual local replica set `rsGestionaleDesktop`, the dedicated `gestionale_desktop` database, device UUID and a staging-only secret.
- The receiver is the existing `backend.desktop_sync.staging_service` with an isolated database marked `desktop-sync-staging`. It only records receipts; it does **not** touch cloud inventory, accounting or real sales.
- The sender validates the complete immutable sale snapshot and its hash **before transmitting**. It sends up to 25 events per explicit invocation (default 10); failures remain local `PENDING` for safe retry.
- Only exact signed-in response content (`operation_id`, `status=STAGED`, `business_applied=false`) advances the local record through atomic compare-and-set to `STAGED`. It never uses `ACKNOWLEDGED` or removes an event.
- No automatic retry, reverse cloud reconciliation, real stock authority, secure provisioning, multi-device guarantees, signed installers or encrypted backups are implemented.

Environment (in a disposable lab only; use a new random token, never real shop credentials):

```sh
export DESKTOP_LOCAL_MONGO_URL='mongodb://127.0.0.1:27028/?directConnection=true'
export DESKTOP_LOCAL_DB_NAME='gestionale_desktop'
export DESKTOP_SYNC_STAGING_DEVICE_ID='<matching local device UUID>'
export DESKTOP_SYNC_STAGING_TOKEN='<same staging-only secret as receiver>'
export DESKTOP_SYNC_STAGING_BASE_URL='http://127.0.0.1:18086'
python -m backend.desktop_sync.staging_sender
# Only for isolated test events, never real business records:
export DESKTOP_SYNC_STAGING_ALLOW_SEND=1
python -m backend.desktop_sync.staging_sender --send
```

The sender leaves the local outbox in `STAGED`, a reversible receipt-state update, not definitive authoritative acceptance. In a future production sync protocol, `STAGED` must not be interpreted as cloud-side booking.

Unit tests: `python -m pytest backend/tests/test_desktop_staging_sender.py -q`.

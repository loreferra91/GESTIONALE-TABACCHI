# Gate 3A — staging receipt service (no business posting)

**Status:** isolated staging ingestion implementation. Not production sync, not a Render deployment, not a desktop installer.

## Why the distinction matters

A desktop sale can be recorded in the local store while offline. Cloud **receipt** does not prove authority to deduct shared inventory. This staging server stores the immutable event with status `STAGED` and `business_applied=false`. The client must **not** treat this as `ACKNOWLEDGED`, alter cloud stock/cash, or delete the local outbox. There is no server-side business posting, pull cursor, or offline stock escrow. No automatic conflict resolution.

## Scope

- Standalone FastAPI app: `backend.desktop_sync.staging_service:app`. It is **not** registered with `backend.server`, Render or the application's router.
- Auth: explicit one-device UUID allowlist and a staging-only secret (>=32 chars), using constant-time checks. This is a **pilot authentication model**, not production enrollment/revocation.
- Dedicated staging MongoDB database: name must start `desktop_sync_staging_` and must contain `sync_metadata: {_id: 'purpose', value: 'desktop-sync-staging'}` **before** startup. Existing production DB names are refused. Requires an actual MongoDB primary replica set with sessions.
- New events enter `sync_receipts`, with unique operation and sale identities per workspace. Same ID+digest yields replay; differing payload yields conflict; same sale ID with new operation yields conflict. Records are committed inside a MongoDB transaction.
- Wire shape matches the current `desktop_outbox` immutable fields, including the event snapshot. Amounts are integer cents. Only `sale.create` from `NEGOZIO` is allowed.
- `POST /staging/sync/v1/push`: 202 new `STAGED`, 200 replay, 409 ID conflict, 422 tamper/invalid payload. Never changes `vendite`, `prodotti`, `cassa`, ordini or movements.
- `GET /staging/sync/v1/receipts/{operation_id}` and `GET /staging/sync/v1/status` offer authenticated *receipt-only* metadata.

## Example isolated local setup

Never use Atlas production or a shared application DB. Initialize a dedicated replica-set database on a disposable/local test MongoDB. Create its purpose marker explicitly in mongosh:

```javascript
use desktop_sync_staging_lab
db.sync_metadata.insertOne({_id: "purpose", value: "desktop-sync-staging"})
```

Set environment variables for the staging *service* (the token and both UUIDs are examples only; generate fresh values):

```sh
export DESKTOP_SYNC_STAGING_ENABLED=1
export DESKTOP_SYNC_STAGING_MONGO_URL='mongodb://127.0.0.1:27019/?directConnection=true'
export DESKTOP_SYNC_STAGING_DB='desktop_sync_staging_lab'
export DESKTOP_SYNC_STAGING_WORKSPACE_ID='<new workspace UUID>'
export DESKTOP_SYNC_STAGING_DEVICE_ID='<registered desktop device UUID>'
export DESKTOP_SYNC_STAGING_TOKEN='<random secret of 32+ chars>'
python -m uvicorn backend.desktop_sync.staging_service:app --host 127.0.0.1 --port 18086
```

Remote standalone deployment is not performed. If later explicitly approved, a **dedicated** staging cluster and credentials are required; remote targets need `DESKTOP_SYNC_STAGING_ALLOW_REMOTE=1` and TLS, plus the staging marker. Never set production `MONGO_URL` as staging. This service is not yet hardened for Internet exposure: provision private-network access, TLS termination, rotation, rate limits and monitoring first.

## Tests

CI `Desktop isolated MongoDB certification` runs tests on disposable MongoDB 7 replica set `rsDesktopCert`. The tests ensure a staged receipt is durable, replay-safe, tamper-rejecting, device-authenticated, and does not touch business collections. This does not certify cross-device inventory, distributed conflict reconciliation, backup restore, or a production pilot.

## Next gates

- 3B: manual, explicitly gated outbox sender and local `STAGED` receipt state (never final ACK).
- 3C: authoritative, server-sequenced domain projections and offline stock authority/escrow; only then may events become `ACKNOWLEDGED`.
- 4: destructive/crash/conflict drills on dedicated staging and independently encrypted backup verification.

# Gate 2 — first transaction-bound offline outbox (manual sales only)

Branch `feature/desktop-hybrid-offline-sync`. Experimental; **no real cloud synchronization** and **not approved for shop use**. Main/Render remain unchanged.

## Implemented

- `GESTIONALE_DESKTOP_STRICT_MODE=1` is only set by the prototype desktop launcher and requires a persistent installation-specific UUID.
- FastAPI startup checks actual MongoDB transaction topology and creates unique desktop outbox indexes.
- All HTTP POST/PUT/PATCH/DELETE requests are denied with HTTP 423 **except POST /api/vendite**. This deliberately disables all uninstrumented operations, including stock imports, supplier receipts, vending restocks and manual product changes.
- A desktop manual sale requires a stable `X-Idempotency-Key` (10–160 characters). A deterministic per-device UUID is derived from this key. A canonical digest of key sale fields, with integer-cent monetary representation, binds the request to one immutable operation.
- Inside the **same real MongoDB transaction**, the existing sale and stock mutation execute and `desktop_outbox` receives a pending event with its operation identity and digest. Exact replay returns the existing sale. Reused keys with changed payload fail with HTTP 409. Unique outbox index protects concurrent commits; conflicts fail closed.
- Desktop transaction fallback to session-less compensations is explicitly disabled. Existing Render/non-desktop behavior is preserved.
- Desktop launcher persists `device.id` and uses the local Mongo replica set from Gate 1.

## Known limitations and non-goals

- **No push/pull API**, ACK handling, cloud connection, cursor, reconciliation UI, authentication suitable for production, encrypted backup or installers. Pending events are local only.
- No cloud server connection is attempted.
- Other write endpoints are intentionally blocked, so the application is not yet fully operational offline.
- The post-sale Vending cache reconciliation remains outside the sale transaction in the legacy flow. A dedicated durable projection path will be required before vending sales can be declared fully transactional; for now keep **VENDING manual sales disabled at the desktop boundary** until this is fixed.
- The existing UI may not yet send a desktop-compatible stable idempotency header for manual sales. Frontend integration is required before a pilot.
- No escrow/stock allocation model for multiple offline writers; no multi-device write authority. Zero support for offline double-spend prevention beyond local stock CAS.
- Only safe unit tests are supplied; real Mongo replica-set transaction/retry/crash acceptance and desktop OS tests remain pending.

## Test commands

```sh
python -m pytest backend/tests/test_desktop_outbox.py backend/tests/test_desktop_sync_protocol.py backend/tests/test_desktop_local_runtime.py -q
```

## Next

1. Require a stable desktop sale idempotency key in frontend, with retry preservation.
2. Add integration tests on a real isolated MongoDB replica set, including crash between transaction steps and duplicate submissions.
3. Attach outbox to all other domain-write paths in batches; keep default deny until audited.
4. Add per-device authority/escrow and a staging-only cloud inbox/outbox API before bidirectional sync.

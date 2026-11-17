# GESTIONALE-TABACCHI Desktop — hybrid offline-first architecture

Status: **design + isolated sync preflight prototype**, **NOT a usable desktop installer or deployed synchronization service**.
Baseline: main commit `b799b37869b1ae72e27e47f603c239fdd211d4f9` (2026-11-17). Development branch `feature/desktop-hybrid-offline-sync`. No modifications to main, Render, Atlas, existing runtime routes or accounting formulas.

## Non-negotiable constraints

1. Offline is a real persistent local MongoDB database, **never** `backend.local_test_app` or `mongomock_motor`. The existing .command launchers are ephemeral test tools, not desktop production.
2. Both local and cloud MongoDB must support real multi-document transactions via a replica set. Never silently fall back to simulated compensation for cash/stock writes.
3. Local database and Atlas production must initially remain **separate**. No automatic imports, migrations or synchronization against production before explicit staging acceptance.
4. No last-write-wins for cash, sales, stock, vending, supplier orders, ledger balances, quantities, corrections, prices affecting booked transactions, or inventory counts.
5. Every critical command carries immutable globally unique operation ID, device ID, authenticated actor, schema version, base entity revision, canonical payload digest and server-assigned commit sequence. Retain original posting and record compensating reversals rather than deleting history.
6. Every accepted command and its idempotency receipt are committed **atomically** alongside the business mutation and an append-only audit/outbox record. Unique `operation_id` plus payload hash prevent ambiguous retries.
7. One logical inventory/write authority per business workspace. Offline devices must either hold explicitly allocated stock/operation authority (bounded escrow) or queue high-risk commands pending server acceptance. Two independent offline devices cannot both promise to decrement the same unpartitioned inventory safely.
8. Offline backups must be encrypted and restorable; keeping snapshots only in the same database is insufficient. Server-side restore drills are separate from local restore drills.

## Suggested runtime

- UI: Tauri 2 window hosting current React build, preserving API paths, keyboard use, PDF/CSV/Excel export.
- Python: packaged FastAPI sidecar, bound only to loopback on a random per-launch port, with one-time session token and origin validation; do not expose an unauthenticated HTTP port to the LAN.
- DB: MongoDB local replica-set with durable local storage and controlled lifecycle. Standalone MongoDB does **not** satisfy the transaction contract. Package/install MongoDB only after checking redistribution/licensing and operating-system support; never rely on mock storage.
- Web/Render: current server remains unchanged until a separately approved, backward-compatible sync endpoint is implemented and deployed through staging.
- Device identity: per-install signing key stored in OS keychain, rotating credentials, revocation support. Never embed an Atlas URI or global DB credentials in the installer.
- Auto-Order 3.1: forecasts use local native sales and locally authoritative stock; label forecast freshness and **block confirmation** when unacknowledged stock/order conflicts or missing data compromise the snapshot.

## Transport and server contract (future implementation)

**Only through an authenticated sync API**, never direct Atlas access from desktop:

1. `POST /api/sync/v1/push`: bounded batch of signed, ordered, idempotent commands. Requires registered device, tenant authorization, schema negotiation and strict rate limits. Validate offline command ownership/escrow and expected revision in transaction.
2. `GET /api/sync/v1/pull?cursor=...`: authenticated monotonically sequenced committed events and immutable receipts. Durable checkpoint cursor; pagination and replays supported. The cursor is issued by the server, not derived from client wall-clock timestamps.
3. `GET /api/sync/v1/status`: accepted, pending, rejected and conflicting operation counts plus last reliable commit cursor.
4. `POST /api/sync/v1/conflicts/{id}/resolve`: explicit operator action with proof and newly versioned command. No implicit stock balance overwrite.
5. Staged migration/backfill: verified collection counts, inventory and cash totals, original IDs, unique indexes, cutover ledger and point-in-time restore path before any live push.

**Local persistent outbox lifecycle:** PENDING -> IN_FLIGHT -> ACKNOWLEDGED, or CONFLICT / REJECTED. Retries keep the original ID/digest; crash recovery resumes from PENDING/IN_FLIGHT without creating another sale. Apply pulled events idempotently to local inbox and commit pull cursor with the local projection in a single local transaction.

**Concurrency safety:** preflight `decide_incoming` is pure and non-authoritative. The eventual server must verify idempotency and compare-and-swap `revision` **inside the same MongoDB transaction** that creates the business mutation, receipt and outbox record. A second concurrent commit must retry or fail with a conflict.

## Conflict table

| Domain | Conflict example | Required behavior |
|---|---|---|
| Sale / cash | Same operation ID resent | Exact same hash => replay stored receipt; different hash => reject and audit |
| Stock / vending | Two offline withdrawals of same stock | Require stock escrow allocation or block second commit and quarantine |
| Supplier order | Desktop confirmation after cloud receipt | Revision conflict, manual reconcile; do not create or receive twice |
| Inventory adjustment | Physical count while other device sells | Store count evidence and reference timestamp; reconcile movements, do not overwrite total |
| Catalog metadata | Concurrent name edits | Record conflict; allow audited field-level merge only after domain review |
| Auto-Order | Pending unacknowledged orders or sales | Show stale/incomplete state and prevent unsafe confirmation |
| Import CSV vending | Same source event from two devices | Enforce canonical source-event fingerprint and unique index, independent of operation ID |

## Implementation gates

- **Gate 0 — now:** isolated protocol objects, allowlist, canonical hash, duplicate detection and revision conflict preflight with unit tests. No runtime integration or installers yet.
- **Gate 1:** transactional local Mongo replica set, durable file-system path, backup/restore tests, read-only FastAPI desktop adapter, controlled startup/shutdown, loopback authentication.
- **Gate 2:** integrate local write-path outbox/inbox with original domain services through a transaction-bound context; instrument cross-collection invariants; bounded offline writes with one device authority.
- **Gate 3:** create staging-only cloud synchronization API, authentication, idempotency receipt store, CAS revision checks and deterministic event log. No production DB connection.
- **Gate 4:** controlled reconciliation tests: offline writes, concurrent devices, crash before/after commit, retry storms, duplicate CSV vending, stock underflow, supplier receipt conflicts, clock skew, restore, and Auto-Order 3.1 freshness.
- **Gate 5:** package Tauri Windows x64 and macOS arm64/x64 installers, sign/notarize as applicable, verify fresh installation and upgrades without data loss. CI installers published only as pre-release artifacts after OS-specific smoke tests.
- **Gate 6:** separately authorized staging pilot and production enablement. Do not merge this branch until desktop, database and sync gates pass.

## Tests

`python -m pytest backend/tests/test_desktop_sync_protocol.py -q`

This module is intentionally **not wired** into current operational endpoints. It does not provide working sync, desktop packaging, or data migration. It cannot be treated as evidence of offline transactional safety until Gates 1-4 are passed on real replica sets.

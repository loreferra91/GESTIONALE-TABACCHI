# Gate 3C — experimental stock authority / escrow projection (staging only)

This is NOT production synchronization. This is a safety model on an isolated disposable MongoDB replica set. It is not a shop-ready application.

## Model
- An operator explicitly initializes `lab_inventory` with a synthetic stock count. This does **not** read or alter production inventory.
- Before offline sales, an operator reserves finite stock via `lab_grants` for each known device; allocations across all devices cannot exceed the original stock.
- Each manual `sale.create` already received in `sync_receipts` can be projected only by explicit invocation of `EscrowLaboratory.apply_staged(operation_id)`. It uses a MongoDB transaction to atomically deduct from one device's remaining escrow and insert exactly one `lab_sales` record.
- If escrow is insufficient, the event remains `STAGED` and an `OPEN` `lab_conflicts` record is written; no sale is applied to the lab ledger. An open conflict requires future manual reconciliation rather than automatic retry.
- The conservation check is **initial_stock = unallocated + reserved + consumed**. Unique keys enforce idempotent operation and sale IDs.
- `sync_receipts` remain `STAGED` throughout; `business_applied=false` is not changed and no `ACKNOWLEDGED` is emitted to a desktop. This protects against false cloud booking claims.

## Security gates
- Must use a database named `desktop_sync_staging_*`.
- MongoDB replica set must be **exactly** `rsDesktopCert`, and a writable primary with session support.
- Must have BOTH `sync_metadata` markers: `{_id: 'purpose', value: 'desktop-sync-staging'}` AND `{_id: 'escrow-lab-opt-in', value: 'escrow-lab-only'}`, created deliberately in a disposable lab.
- No HTTP endpoint for stock seeding, grant allocation or apply. This module is **not mounted** in `backend.server`, `staging_service`, Render or desktop launcher.
- Real-company stock, cash, vendor orders and accounting collections remain untouched. Never use production or shared databases.

## Limitations before real cloud authority
- Offline devices do not yet receive **signed allocation certificates**, nor have they been modified to enforce the assigned escrow during an offline sale.
- Stock may differ between local desktop snapshots and server-side inventory; staging projections do NOT cure that difference.
- There are no business domain projections, multi-device enrollment, automated syncing, signed receipts, field conflict UI, backup restore drills, or signed installers.
- The test is run against disposable MongoDB 7 through GitHub Actions; production must never be enabled before these are implemented and reviewed.

Test: `backend/tests/test_desktop_escrow_lab.py` via isolated desktop certification CI.

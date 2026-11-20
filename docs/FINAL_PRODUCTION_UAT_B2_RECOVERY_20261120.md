# Production release final checks — cash UAT and offsite B2 recovery

Date: 2026-11-20. Release status: NOT YET CLEARED. This is evidence preparation, not authorization for merge/deploy.

## A. Cash / payment operational UAT

### Evidence verified (read-only and CI)
- Render staging live revision: 05bb7181817e859994270ae7daa94bf67c527a6e.
- GitHub staging Verify on exact revision: successful (run 44723266353).
- Disposable replica-set cash-count transaction certification: successful
  (run 44718089985, revision de0fdeb4; no cash-count file changed later).
- Production release candidate exact SHA 8220691ef988674e875e20dd4541787af6660bab:
  Verify successful (run 44716773910); real MongoDB sales certification
  successful (run 44716773838).
- Live Atlas staging read-only counts: 232 products, zero cash movements,
  zero rapid-cash batches and zero recorded physical cash counts.
- Atlas production read-only counts: 232 products, zero cash movements
  and rapid-cash batches. 148 historical BULK sales have no batch ID.
  No opening cash balance is certified. Do not create fictional baselines.

### Operator UAT still required for sign-off
Use a disposable isolated MongoDB test database. Never mutate Atlas production.
If staging UI is used, isolate synthetic test products and secure a verified
staging backup. Do not overwrite or rewrite existing staging sales and cash.

| Scenario | Pass criterion |
|---|---|
| Manual sale, retry same key and undo | One stock debit, stock restored once, immutable audit, revoked key cannot resell |
| Rapid POS/cash batch, retry and undo | Exact amounts and payment, no duplicate cash or negative stock |
| Direct delete of POS-linked cash movement | Blocked 409; cancellation only through owner batch |
| Delete of independent cash movement | Atomic audited delete with safe repeat |
| Physical count with signed source | Accurate cents total and audit, no booked balance/baseline/stock update |
| Concurrent sale and insufficient stock | Only valid transaction wins, no partial stock/cash debit |
| Financial reconciliation read-only | Missing baseline remains unknown, source gaps reported |
| Operator browser acceptance | Recorded screenshots/results and signed acknowledgement |

Automated tests passed but NO documented operator acceptance exists.
Empty cash collections do not certify live operational cash behavior.

## B. B2 encrypted recovery drill

Local source: ~/Backup-Gestionale-Tabacchi/last-success.json
Backblaze bucket: gestionale-tabacchi-recovery-2026 (Private)
Endpoint: https://s3.eu-central-003.backblazeb2.com
AWS CLI profile on operator Mac only: gestionale-b2

Run from existing Mac checkout:

    cd "$HOME/GESTIONALE-TABACCHI-BACKUP"
    git status --short
    git pull --ff-only origin feature/macos-encrypted-backup-launchd-20261120
    python3 scripts/certify_macos_b2_backup.py
    python3 scripts/certify_macos_b2_backup.py --upload-and-restore

The last command (explicitly opt-in):
1. Validates local encrypted archive against its SHA-256 manifest.
2. Uploads only encrypted bytes to a NEW unique B2 object key under
   gestionale/production/recovery-drills/; never overwrites/deletes remote files.
3. Re-downloads the object and verifies exact size and SHA-256.
4. Pipes GPG decryption into a disposable Docker MongoDB container
   with network disabled and a distinct restore database name.
5. Verifies restored collection, document, audit and index counts.
6. Removes the temporary container; no unencrypted BSON archive is saved locally.
7. Reports B2_REMOTE_RESTORE_VERIFIED only after success.

Limits: a logical restore of a hot mongodump is not a transactionally
consistent point-in-time snapshot. No recurring B2 uploader or retention
policy is enabled by this test. Do not publish secrets or database documents.

Pending: the actual Mac output confirming the B2 restored copy, and a
production-specific limited B2 application key for ongoing production uploads.

## Release decision
DO NOT merge the entire staging branch. Production candidate PR #40 is
selective and remains DRAFT until both UAT and independent B2 evidence
are recorded, rollback owner identified and deployment window approved.

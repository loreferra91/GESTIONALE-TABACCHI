# Operations safety release record — 2026-11-21

## Purpose

Distinguish evidence-backed production changes from staging-only recovery
infrastructure. This document is a release decision record, not a command to
migrate databases or a sign-off of physical cash.

**Production baseline:** `main` commit
`8841e7ee4b373afeef8bc7e0dec795ba8ff5e831` after PR #40,
Render deployment `dep-dc0l4q95efls73e8bp6g` live.
Manual sales, POS, audited reversal, reconciliation and their shared
backend implementations have matching blobs in staging and `main`.

**Staging baseline:** branch `openclaw/render-staging-deploy`,
commit `05bb7181817e859994270ae7daa94bf67c527a6e`,
Render deployment `dep-dc02e5p5efls738fst6g` live.

## Tested versus unverified

| Area | Verified evidence | Remaining gate |
| --- | --- | --- |
| Manual sales, Cassa Rapida POS, reversal and immutable audit | Actual staging operator UAT (21 Nov) + independent Atlas read-back and production release CI | No operational production mutation permitted just for proof |
| Separate cash movement create and audited delete | Operator UAT + Atlas showed one CREATO and one ELIMINATO | Actual disconnected/concurrent retry exercise not documented |
| Physical cash count | 1.50 EUR staged evidence and audit, balances unchanged; isolated real-Mongo transaction CI | Feature remains **staging only**; no certified production opening cash balance |
| Read-only reconciliation | Operator report flagged historic unbatched BULK and unknown cash baseline | Legacy provenance and physical balances remain unresolved |
| Production backup | Real B2 encrypted upload/read-back/GPG isolated restore; 139800 docs, 38 collections, 98 indexes | Not PITR; recurring B2 schedule and recovery objectives not proven |
| Staging JSON restore | Preview/restore with disposable synthetic MongoDB real tests and backup lock/rollback CI | **Live Atlas staging UI restore not signed off**; do not overwrite operational records |
| Vending, Smart Venue, supplier orders, Auto-Order 3.1 | Existing code regression tests in `main`, additional dedicated CI workflow herein | Operator full end-to-end workflow including actual supplier reception, concurrent vending refill, and overrides not certified |

## Security: staging Atlas credential still at excessive privilege

Read-only Atlas verification on 21 Nov 2026 found:

- `gestionale_staging_scoped_20261119`: only `readWrite` on
  `gestionale_staging`, scoped to `gestionale-staging` cluster.
- `gestionale_backup_reader_staging`: dedicated backup read role.
- `gestionale_staging_app`: **still exists**, `readWriteAnyDatabase`
  on `admin`, without cluster scope.

Rendered service logs continue to show `Scoped staging credential check
passed`, most recently on 21 Nov 2026 at 07:54:55 UTC.
This proves successful scoped credential startup, not absence of a
legacy-use path.

The connected Atlas API in this workspace can list/create DB users but
does not expose a revoke/delete/update-user operation; Render cannot
safely read or remove one existing environment variable. **No
revocation or secret rotation has been performed.**

A future authorized admin cutover requires:

1. Verify a current independent encrypted staging backup, key recovery,
   and an isolated restore proof; internal snapshots are not sufficient.
2. Recheck Render active scoped credential (without disclosing secrets),
   `/api/` availability and normal application reads.
3. Revoke only `gestionale_staging_app` in Atlas project
   `Gestionale Staging`; do not touch production users.
4. Verify the scoped app still starts and can perform an appropriate
   isolated canary; check backup tooling.
5. Remove the obsolete `MONGO_URL` fallback from staging Render using
   a supported single-key deletion, without replacing the env set.
6. Record proof and rollback method. Do not silently recreate the
   unrestricted account.

## JSON backup recovery limits

The staging restore route uses a persistent maintenance lock,
pre-restore checkpoint, bounded batches and content comparison. It is
**not multi-collection atomic** and must remain staging-only.
The latest read-only `backup_restore_control` state is `READY`;
internal `backup_snapshots` exists and does not count as independent
backup. A real external JSON restore of live staging operational data
has not been carried out for this release: doing so could overwrite
legitimate cash-count/sales test evidence or operational records.
Use a disposable replica set with synthetic/sanitized data instead.

## Production release policy

The dedicated workflow `production-operations-regression.yml` runs
against test-only `mongomock-motor` fixtures, no Atlas credentials.
Its success means regression invariants pass; it does NOT certify
a live shop, physical inventory, real money, supplier deliveries,
nor production readiness of staging-only routes.

Do not merge full staging (160 unique commits as of this record).
Runtime changes for JSON restore, staging-only migration/RBAC and cash
count must be independently redesigned and reviewed before production.
Never create artificial stock/cash baselines or silently remap the 148
historic BULK sales lacking `batch_id`.

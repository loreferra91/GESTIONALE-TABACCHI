# Production release gate — sales, cash and audit integrity (2026-11-20)

## Release candidate (NOT DEPLOYED)

Branch: `feature/production-sales-integrity-backport-20261120`,
based exclusively on production `main` commit
`2062610ec28604e559b8db73388b463e0cf1c1ce`.

Only the following files are in scope:

- `backend/sales_router.py`: audited manual sale reversals,
  idempotency-key revocation, Cassa Rapida preflight of booked stock and
  money, fractional quantity rejection and conflict-safe POS undo.
- `backend/cash_movements_router.py`: transactional cancellation audit
  and rejection of direct deletion of a POS-linked cash movement.
- `backend/reconciliation.py`: missing stock is UNKNOWN rather than
  zero; check native POS cash links, historical BULK lineage and
  explicit limits of physical cash reconciliation.
- Corresponding backend regression test modules.
- `frontend/src/components/ReconciliationPanel.jsx` and tests:
  document coverage gaps without staging-only cash-count UI.
- `frontend/src/pages/Vendite.jsx`: explain preserved audit evidence.
- `backend/server.py`: one additive index on
  `audit_events(azione, idempotency_key)` for revoked retry lookup.

**Explicit exclusions:** staging-only backup restore API, migration
runner, cash-count entry, S3/Mongo credentials, Render flags,
Desktop sync, and the full staging branch.

## Tests required before approving the production merge

1. Exact release candidate SHA: GitHub Actions Verify = success (backend
   pytest, frontend tests, compile, build and streaming benchmark).
2. Existing staging code equivalent has passed disposable MongoDB real
   transaction certification and isolated 3-node failover testing.
3. Operator UAT in staging: manual sale and undo, repeated submit,
   Cassa Rapida and undo, cash movement delete protections, BULK
   idempotency, product stock, vending cache and read-only financial
   reconciliation.
4. **Production-specific independent backup** must be recorded.
   The operator may verify the downloaded 20 November JSON *locally*
   without disclosing data using:
   `python3 scripts/verify_backup_export.py /secure/path/gestionale-backup.json`.
   The tool requires all 35 per-collection checksums, verifies every
   record and emits only a local SHA-256, counts and non-sensitive status.
   It explicitly does NOT certify that the copy is independent or that
   the database can be recovered. Verify original production source,
   durable independent storage, recovered indexes and a test restore
   into an isolated cluster/namespace. Prefer encrypted off-cluster
   S3 with a recoverable private GPG key and retained versions; no
   production S3 destination is currently verified. The completed
   27,171-document staging restore is strong *functional* evidence
   only when the operator establishes that the JSON came from
   production and was preserved independently.
5. Verify production MongoDB's existing business unique indexes,
   `audit_events` size/performance and compatibility of historical
   records before applying the new additive index.
6. Record production stock/cash baselines **from existing evidence**,
   not invented opening values; determine how to pause operator writes
   during the release if needed.
7. Have a rollback owner with access to the known-good Render revision
   and the separate backup. Code-only rollback does **not** reverse
   economic/inventory movements written by a newer version.

## Decision

**BLOCKED FOR PRODUCTION DEPLOY UNTIL STEPS 1–7 ARE VERIFIED.**
Do not merge this PR, deploy, restart, or change Render production
variables merely because the CI suite passes.

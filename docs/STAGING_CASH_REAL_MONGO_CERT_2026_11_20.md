# Staging cash count — real transactional MongoDB certification (2026-11-20)

## Executed evidence

Isolated GitHub Actions job:
[44717460404](https://github.com/loreferra91/GESTIONALE-TABACCHI/actions/runs/44717460404).

Exact commit `e72e7fd8e35def0b76cadba4918185f215b98077`.
The test suite uses an **ephemeral MongoDB 7 replica set** bound only to
`127.0.0.1:27029` in CI. Database name begins with
`gestionale_cert_cash_`, is dropped before and after the test, and has no
Atlas credentials or business data. General CI:
[44717460294](https://github.com/loreferra91/GESTIONALE-TABACCHI/actions/runs/44717460294) — success.

Two independent certification scenarios **PASSED**:

1. Four concurrent posts with the same idempotency key do not create
   duplicate cash observations or audit events; replay preserves
   the exact observation ID, conflicting reuse returns 409.
2. A deliberately colliding audit primary key aborts the full MongoDB
   transaction: no orphan observation remains, no booked cash,
   sales, product or opening baseline changes. Removing the synthetic
   fault allows one later successful retry.

## Operational guarantees and remaining limits

- This verifies a real Motor/PyMongo transaction, not only a mock.
- The `cash_count_evidence` collection stores **observations**, never
  booking entries or an opening cash baseline.
- The `saldo_cassa_certificato` status remains **false** until a signed
  opening count, dated period, POS/bank documents, closing count and
  independent reconciliation exist.
- CI does not simulate `SIGKILL` in the middle of a journal write,
  actual disk power loss, operator fraud or a physically counted
  cash drawer.
- Never enter invented denominations, false operator signatures
  or synthetic cash observations in the real staging database.
- The app-specific test is staging-only. Never merge it as evidence of
  a certified physical cash balance into production.

## Follow-up operator acceptance

In staging Contabilità open the physical cash count panel.
Read-only UI and validation can be tested without posting any
observation. If a genuine signed physical count is available,
record its denomination counts and document reference once;
preserve the signed paper record separately, then verify that
the app flags it as observed but not certified. Audit must
record exactly one event and the booked balance must remain
unchanged. No fake baseline is permitted.

**No Atlas, Render, production or staging operational database writes
occurred during this CI certification.**

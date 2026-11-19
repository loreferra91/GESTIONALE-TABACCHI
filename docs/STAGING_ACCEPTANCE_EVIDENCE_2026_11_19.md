# Staging acceptance evidence — 2026-11-19

**Environment:** `gestionale-tabacchi-staging.onrender.com`  
**Git branch:** `openclaw/render-staging-deploy`  
**Verified live commit:** `f49cc0971d46e592298255cea08a9db8985a7d4a`  
**Production `main`:** `2062610ec28604e559b8db73388b463e0cf1c1ce`, unchanged by the staging rollout.

## Results

| Check | Evidence | Result |
|---|---|---|
| Full CI | GitHub Actions 44544081390 | 544 backend passed, 34 skipped, 78 frontend passed, React build passed |
| Three-node MongoDB failover | Actions 44544080845 | 1 real test passed on disposable localhost replica set |
| Exact Render live revision + DB health + backup authentication | Actions 44544081028 | passed on exact `f49cc097` |
| Encrypted MongoDB archive + separate restore | Actions 44540915317 | success, 7 synthetic MongoDB documents restored |
| BULK/undo transactional certification | staging sales CI from PR #18/#19 | passing, disposable MongoDB |
| Auto-Order streaming synthetic benchmark | Actions 44544081390 | 5k: 0.147s / 2.74 MiB; 50k: 1.407s / 17.98 MiB; 100k: 2.77s / 35.07 MiB peak |
| External staging backup restore drill | Actions 44528554272 | passed, disposable database |

**Limits:** Benchmarks are synthetic CI measurements, not Render/Atlas p95 production latency. The encrypted restore verified a small synthetic dataset, not a
production-sized operational recovery. The failover test proves majority
acknowledged writes survive a primary stepdown; it is not proof of all
application retries during multi-node network partitions.

## Actual MongoDB Atlas staging — read-only diagnostics

Cluster: dedicated `gestionale-staging`; database `gestionale_staging`.
Checks were run against this cluster with read-only queries: no inserts,
updates, deletes, restores, migrations or index creation.

| Diagnostic | Result |
|---|---:|
| Product documents | 232 |
| Vending column documents | 84 |
| Native sale documents | 5,007 |
| BULK/CSV import batch documents | 92 |
| Audit event documents | 640 |
| Cassa Rapida batches | 0 |
| Cash movement documents | 0 |
| Products with missing/null/negative shop stock | 0 |
| Products with missing/null/negative vending cache stock | 0 |
| Vending columns with missing/negative stock | 0 |
| Products with negative/missing native sold counters or prices | 0 |
| Products represented by vending columns | 84 |
| Product vending cache vs column stock mismatches | 0 |
| Vending column references without a matching product | 0 |
| Business-key duplicate groups: products, columns, parameters | 0 each |
| Legacy H1/H2/H3 vending columns | 0 |
| Canonical H01/H02/H03 columns | 3 |
| Known migration records found | 3 / 3 |

### Historical BULK records requiring review

There are **148 records with `sorgente: "BULK"` and absent `batch_id`**
dated **2026-10-04 through 2026-10-06**. These are grouped into
one missing-batch diagnostic, not 148 independent batch failures.

Shop BULK batches that are marked active and have identifiable batch IDs
match the observed row counts in the read-only comparison. No verified
shop batch marked annulled still contains live sale rows.

**Do not silently attach those 148 sales to arbitrary batches, delete
them or adjust stock.** Their history may predate the current BULK
batch contract. Establish provenance and baseline from original
imports/operations before deciding whether any correction is warranted.

### What was NOT established

- No certification of financial cash baseline, because staging currently
  contains no `cassa` or `cassa_rapida_batches` documents.
- No measured `explain()` and p95 performance benchmark on a large
  operational fixture.
- No operator-role authorization or individual operator migration.
- No independent off-cluster scheduled backup with an approved RPO/RTO.
- **SECURITY BLOCKER CONFIRMED:** the only MongoDB Atlas database user
  in project `Gestionale Staging` currently has `readWriteAnyDatabase`
  on `admin`, broader than required for `gestionale_staging`.
  The project/cluster is separate from the production project; this
  finding does not prove access to the production cluster.
  Restrict runtime credentials to the staging database and separately
  provision a read-only diagnostics user after validating the permission
  changes without outage. Do not change roles blindly while Render
  is using the account.
- No proof that credentials previously shown in a screenshot were rotated.
- No full restore of a sanitized 25k+ document archive into an isolated
  clone after the encrypted archive verification.
- No mutation of historical records or reconciliation baselines.

## Release decision

**Production promotion: BLOCKED pending a targeted approval and
reviewable PR from the selected change set.**

Do **not** merge the entire staging branch: it contains staging-only
restore behavior. In particular, the bounded non-transactional restore
pathway must remain staging-only.

Next gates:
1. Inspect the 148 old BULK sales and establish provenance without
   modifying them.
2. **P0:** replace the broad `readWriteAnyDatabase` staging account with
   a least-privilege `readWrite` account scoped only to
   `gestionale_staging`. Coordinate credential rotation (known screenshot
   exposure) and verify application startup/backup procedures afterward.
3. Split any production-bound changes into minimal PRs with independent
   tests, migrations, rollback and backup evidence.
4. Certify application-level retry/unknown-commit cases and stale stock
   using disposable MongoDB topology.
5. Carry out operator UAT only on disposable synthetic staging records.
6. Approve the precise commit and production release separately.

## Links

- [Full staging CI](https://github.com/loreferra91/GESTIONALE-TABACCHI/actions/runs/44544081390)
- [Real three-node failover](https://github.com/loreferra91/GESTIONALE-TABACCHI/actions/runs/44544080845)
- [Staging live smoke](https://github.com/loreferra91/GESTIONALE-TABACCHI/actions/runs/44544081028)
- [Encrypted MongoDB restore certification](https://github.com/loreferra91/GESTIONALE-TABACCHI/actions/runs/44540915317)

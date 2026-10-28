# Staging Certification Report

**Date:** 2026-10-28
**Worktree:** `openclaw/staging-certification` @ `2aac4f6` (based on `origin/main` `51f94f1`)
**Scope:** Staging-readiness verification against a real MongoDB replica set.
**Verdict:** **CONDITIONAL PASS** — application-level correctness is verified; provider-level backup, process-kill durability and remote-environment parity remain unverified (details in §7).

---

## 1. Environment Under Test

| Component | Configuration |
|---|---|
| MongoDB | `mongo:7` container `mongo-cert-rs`, `--replSet rs0`, published `127.0.0.1:27018` only |
| Replica set | `rs0`, single node, `me: localhost:27017`, `isWritablePrimary: true`, `logicalSessionTimeoutMinutes: 30` |
| Backend | `uvicorn backend.server:app` on `127.0.0.1:8099`, `ENVIRONMENT=development`, `AGENT_INTEGRATION_ENABLED=false` |
| Database | `gestionale_staging_cert`, seeded from tracked `backend/seed/seed_data.json` |
| Data | **Synthetic / seed only.** No production or shared database was contacted. |
| Auth | No keyfile, no auth: disposable single-node set bound to loopback only |

Startup log confirmed the fail-closed topology check:

```
MongoDB transaction capability: {'topology': 'replica_set', 'transactions': True}
Business-key index migration: [...] 'migrated' / 'already_unique'
```

---

## 2. Method

Two independent layers, because they prove different things:

1. **Certification harness** (`backend/tests/certification_harness.py`, `test_certification_harness.py`) — in-process assertions against the real replica set. Refuses to run unless the target proves a replica-set/sharded topology *with logical sessions*, so transaction assertions cannot silently degrade against a standalone mongod.
2. **External HTTP suites** (7 modules gated on `TEST_BACKEND_URL`) — driven per-module serially (`-n 0`) against a live backend on a properly seeded database.

The default unit suite was also run to prove the new harness does not disturb it.

---

## 3. Result Summary

| Suite | Result | Evidence |
|---|---|---|
| Certification harness (replica set) | **18 passed** | `CERT_MONGO_URL=…:27018`, `-n 0` |
| External HTTP suites, 7 modules | **81 passed, 0 failed** | per-module serial, live backend |
| Default unit suite (harness excluded) | **302 passed, 7 skipped** | `pytest backend/tests` |

### External suite breakdown

| Module | Result |
|---|---|
| `backend_test.py` | 20 passed |
| `test_aggio.py` | 4 passed |
| `test_import_excel_full.py` | 8 passed |
| `test_iter10_venduti_proxy.py` | 11 passed |
| `test_iter8_features.py` | 10 passed |
| `test_iter9_copertura.py` | 8 passed |
| `test_parametri_bounds.py` | 20 passed |

---

## 4. Certification Harness — Scenario Detail

All 18 scenarios **PASS**.

### 4.1 Topology and transaction primitives

| # | Scenario | Assertion | Status |
|---|---|---|---|
| 1 | Topology is replica set with sessions | `logicalSessionTimeoutMinutes` non-null, `setName` present, `isWritablePrimary` true | PASS |
| 2 | Multi-document commit persists every write | 2 docs written in one transaction, both present after commit | PASS |
| 3 | Aborted transaction leaves no partial writes | rollback leaves exactly the 2 previously committed docs | PASS |
| 4 | Concurrent transactions do not lose updates | final counter == 4, no lost update, and exactly one of *transparent retry* / *retries exhausted* holds | PASS |

Scenario 4 is the one most likely to pass vacuously, so it now classifies its outcome explicitly: the probe asserts the driver either retried the loser transparently (`final == 4, conflicts == 0`) or surfaced a conflict (`conflicts > 0`). A hung worker thread now fails the assertion instead of being mistaken for success.

### 4.2 Duplicate and business-key concurrency

| # | Scenario | Status |
|---|---|---|
| 5 | Unique business-key indexes migrate cleanly (`prodotti.codice`, `vending.colonna`, `parametri.nome`) | PASS |
| 6 | Duplicate business key rejected (`E11000`) | PASS |
| 7 | Concurrent identical idempotency key → exactly 1 row, 7 rejected | PASS |
| 8 | Dirty collection blocks unique migration and reports offending values | PASS |

Scenario 8 matters operationally: a collection with pre-existing duplicates keeps its non-unique index and reports the duplicate values rather than causing a deploy outage. In production (`_is_production()`), the startup path *raises* instead of continuing — verified by code inspection in `backend/server.py::_validate_required_index_results`, not exercised at runtime here.

### 4.3 Application idempotency through the real routers

| # | Scenario | Status |
|---|---|---|
| 9 | Manual order replay is once-only (`gia_registrato: true`, same `id`, stock decremented once) | PASS |
| 10 | Manual order payload mismatch → 409, no second write | PASS |
| 11 | Cash movement replay is once-only | PASS |
| 12 | Cash movement payload mismatch → 409 | PASS |

These drive the real `add_ordine` / `add_cassa` coroutines with `server.db` rebound to a Motor client on the scenario's own event loop — not mocks.

### 4.4 Interruption, restart and safety guards

| # | Scenario | Status |
|---|---|---|
| 13 | In-flight uncommitted transaction invisible to other sessions | PASS |
| 14 | Interrupted transaction leaves no trace after rollback | PASS |
| 15 | Committed write visible to a brand-new client | PASS |
| 16 | `reset()` refuses non-disposable database names | PASS |
| 17 | `reset()` accepts `gestionale_cert*` names | PASS |

### 4.5 Backup and restore drill

| # | Scenario | Status |
|---|---|---|
| 18 | Disposable backup/restore preserves counts and indexes; RPO/RTO derived from observed timings | PASS |

Observed: 250+ synthetic rows restored, `counts_match: true`, `indexes_preserved: true`, restore under 60s. **This is an in-process snapshot/restore, not a provider snapshot.** See §7.

---

## 5. Defects Found and Fixed During Certification

### 5.1 Missing tracked seed dataset (fixed)

`backend/server.py:395` seeds from `ROOT_DIR / "seed" / "seed_data.json"`. That file is **tracked on `origin/main`** but was **absent from this worktree**. `seed_if_empty()` returns early when the file is missing, so a clean database received only default parameters — no 227 products, 83 vending columns, 594 orders, 4104 listino rows.

Restored with `git checkout origin/main -- backend/seed/seed_data.json`. Verified contents: `prodotti=227, vending=83, storico_ordini=594, listino_adm=4104, parametri=dict`.

**Operational note:** any deployment that relies on `seed_if_empty()` must ship this file. Its absence is silent — the app starts cleanly with an empty catalog.

### 5.2 Destructive `drop_database` without a guard (fixed)

Independent review flagged, and I confirmed, that `CertificationHarness.reset()` dropped whatever `db_name` was configured with no allowlist. A mistyped `CERT_DB_NAME` could destroy a real database.

Fixed: `assert_disposable()` requires the name to start with `gestionale_cert`. No override flag exists, by design. Covered by scenarios 16–17.

### 5.3 Concurrency probes could pass vacuously (fixed)

Both concurrency probes now assert their worker threads terminated and classify the write-conflict outcome explicitly, so a hung thread or a silent lost update can no longer masquerade as a pass.

### 5.4 Earlier test failures were environment, not product defects

`test_iter10_venduti_proxy.py::TestPeriodoEffect::test_periodo_legacy_does_not_change_recent_demand` previously failed with `assert 4.04 == 0.11`, and in isolation with a 409 on `TEST_APP_TRACKED`. Both are explained by (a) a shared database carrying state between modules and (b) the backend process dying mid-run, producing `Connection refused` rather than product assertion failures. Against a live backend on a freshly seeded database, the module is **11/11**. `backend/auto_order_router.py:78-80` derives `domanda_gg` from `AUTO_ORDER_FINESTRA_BREVE_GG`/`AUTO_ORDER_FINESTRA_LUNGA_GG`, never `PERIODO_VENDUTI_GG` — which is the intended behaviour the test asserts.

---

## 6. Fail-Closed Behaviour Verified

| Check | Observation | Status |
|---|---|---|
| Transaction capability at startup | Logs `topology: replica_set, transactions: True`; production raises if unavailable | PASS (dev log) / code-verified for prod |
| Agent integration disabled | `GET /api/agent/v1/status` → **404** `{"detail":"Integration not enabled"}`; unauthenticated access also 404, not 200 | PASS |
| Agent token length | Production requires `AGENT_INTEGRATION_TOKEN` ≥ 32 chars when enabled | Code-verified |
| Production config validation | Missing `APP_USERNAME`/`APP_PASSWORD`, wildcard or empty `CORS_ORIGINS` → `RuntimeError` at startup | Code-verified |
| Unique-index duplicates in production | `RuntimeError` instead of continuing with a non-unique index | Code-verified |
| Harness target validation | Skips (not weakens) on non-replica-set target; refuses non-`gestionale_cert` drops | PASS |

---

## 7. Not Verified — and Why It Matters

These are **not** failures. They are gaps that the disposable local environment cannot close, and the report says so rather than implying coverage it does not have.

| Item | Status | Reason / Next gate |
|---|---|---|
| Provider-level backup/restore (Atlas/Mongo Cloud snapshot) | **NOT VERIFIED** | Drill used in-process snapshots. Next gate: restore a real provider snapshot into an isolated cluster and reconcile counts/indexes. |
| RPO as configured in production | **NOT VERIFIED** | In-process drill cannot observe provider backup cadence. Report only states the *target* (≤24h, daily automatic backup). |
| Process-kill-during-write durability | **NOT VERIFIED** | Session-level interruption and fresh-client visibility are proven; killing the backend mid-transaction was not exercised. Next gate: SIGKILL the app during a write against a replica set, verify no torn state. |
| Application-level retry path for `WriteConflict` | **PARTIAL** | Driver-level conflict/retry proven. The app's own `_run_transactionally` retry behaviour was not forced to exhaust retries. Next gate: fault-inject a conflict through an order route. |
| Multi-node replica set / failover | **NOT VERIFIED** | Single-node `rs0`. Election and step-down behaviour untested. |
| Remote staging parity (TLS, Atlas network, real env vars) | **NOT VERIFIED** | Everything ran on loopback with synthetic data by design. |
| Frontend E2E | **NOT VERIFIED** | Out of scope for this mandate. |

---

## 8. Residual Risks

1. **Silent empty-catalog bootstrap.** If `backend/seed/seed_data.json` is not deployed, the app starts healthy with an empty catalog. No startup warning is emitted. Consider a startup log line when seeding is skipped due to a missing file.
2. **Single-node confidence.** All transaction guarantees were proven on one node. Replica-set *durability* under failover is a different property.
3. **Backup contract unproven against provider.** Application backup routes exist (`/backup/create`, `/backup/{id}/restore`, `/backup/restore-file`), but only an in-process equivalent was exercised.
4. **Order-dependent external suites.** The 7 modules mutate shared backend state. They pass serially against a seeded database; parallel execution across modules would reintroduce the state bleed documented in §5.4.

---

## 8a. Existing-Solutions / Free-Infrastructure Preflight

Per workspace policy, checked whether a maintained free option already covers the staging need instead of building custom infrastructure.

| Need | Existing option | Assessment |
|---|---|---|
| Transaction-capable MongoDB for staging | **MongoDB Atlas M0 (free shared tier)** | Atlas M0 is a replica set and **does** support multi-document transactions. There is a widely-reported `Transaction numbers are only allowed on a replica set member or mongos` error on M0, but it is a *connection-string* problem (missing `replicaSet`/`retryWrites`, or `directConnection=true` against a shared cluster), not a missing capability. Relevant to this repo: a `directConnection=true` URL — the form used for the local drill — is **not** appropriate for Atlas shared tiers. |
| App hosting | Render / Railway / Fly.io free or near-free tiers | Render is already referenced in this project's deployment docs. Adequate for a staging app instance; free tiers sleep, so they are unsuitable for latency or durability testing. |
| Backups | Atlas automated snapshots (paid tiers) | M0 has **no** automated backup/restore. The provider-level backup gate in §10 therefore requires a paid tier or a self-managed dump strategy — it cannot be closed on a free cluster. |

**Conclusion:** no custom infrastructure is warranted. Atlas M0 covers transaction-capable staging; the two things it cannot cover (automated provider backups, multi-node failover) are exactly the two items already listed as NOT VERIFIED in §7 — and they require spend approval, which was not given. No paid resource was provisioned.

## 9. Reproducing This Certification

```zsh
# 1. Disposable replica set (loopback only, no auth, synthetic data)
docker run -d --name mongo-cert-rs -p 127.0.0.1:27018:27017 mongo:7 --replSet rs0 --bind_ip_all
docker exec mongo-cert-rs mongosh --quiet --eval 'rs.initiate({_id:"rs0",members:[{_id:0,host:"localhost:27017"}]})'

# 2. Harness (18 scenarios, real transactions)
CERT_MONGO_URL='mongodb://127.0.0.1:27018/?directConnection=true' \
CERT_DB_NAME='gestionale_cert_harness' \
python3 -m pytest backend/tests/test_certification_harness.py -n 0 -q

# 3. External suites (live backend, seeded DB, serial per module)
#    Start backend on 127.0.0.1:8099, then per module:
TEST_BACKEND_URL='http://127.0.0.1:8099' python3 -m pytest backend/tests/<module>.py -n 0 -q
```

**Serial runs must use `-n 0`, not `-p no:xdist`** — `pytest.ini` `addopts` still injects `-n 2 --dist loadscope`, and disabling the plugin makes pytest reject `-n` as unrecognised.

---

## 10. Verdict and Next Gate

**CONDITIONAL PASS.**

Verified with real transactions on a real replica set: commit/abort atomicity, concurrent write serialisation without lost updates, unique business-key enforcement (clean and dirty data), duplicate rejection, order and cash idempotency including replay and payload-mismatch conflicts, interruption rollback, restart durability, and a disposable backup/restore preserving counts and indexes. External suites: 81/81. Default suite: 302 passed, 7 skipped. Agent integration fails closed when disabled.

**Exact next gate before declaring production-ready:**

1. Restore a **real provider snapshot** into an isolated cluster; reconcile counts and indexes against source; record measured RTO.
2. **SIGKILL the backend** mid-transaction against the replica set; confirm no torn state and no double-decrement on retry.
3. Force the application's own `WriteConflict` retry path to exhaust, and confirm the user-visible error is correct.
4. Prove a **3-node replica set** elects a new primary and that writes resume without lost acknowledged data.
5. Confirm the deployed artifact **ships `backend/seed/seed_data.json`** and emits a warning if absent.

Until 1–5 are closed, staging readiness should be described as *application-verified, infrastructure-unverified*.

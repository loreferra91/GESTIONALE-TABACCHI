# Phase 7 Resilience Certification

**Date:** 2026-10-29

**Baseline:** staging commit `c8fab148a63f8544d9aa842f5931054f0b0857f8`

**Branch:** `codex/phase7-resilience-certification`

**Scope:** disposable local infrastructure and synthetic data only

**Verdict:** **PASS for application/process/failover gates; provider snapshot restore remains NOT VERIFIED**

## Controls added

- `seed_if_empty()` now emits an actionable warning when the tracked seed dataset is absent. Existing non-empty databases are not modified by this diagnostic.
- Exhausted MongoDB transaction errors are classified only through the driver's `TransientTransactionError` and `UnknownTransactionCommitResult` labels. A transient conflict becomes HTTP 503 with `Retry-After: 1`; an uncertain commit tells the caller to verify state before retrying. Neither response exposes database details.
- A subprocess certification worker exercises the real application transaction wrapper and is killed after two uncommitted writes. The test proves that both synthetic documents remain invisible in the probe collection.
- A separately gated three-node test forces primary step-down, waits for a different primary, and verifies majority-acknowledged writes before and after election.

## Infrastructure

Three disposable `mongo:7` containers formed replica set `rsPhase7`. They were bound only to local host ports `27117`–`27119`. The destructive failover and crash probes reject non-loopback MongoDB URLs, and all databases used the mandatory `gestionale_cert*` prefix. No shared, staging, or production database was contacted.

## Results

| Gate | Result | Evidence |
|---|---|---|
| Real replica-set suite, including SIGKILL | **PASS** | `19 passed` |
| Three-node primary step-down | **PASS** | `1 passed`; different primary elected; two majority writes preserved |
| Driver retry exhaustion / user-visible response | **PASS** | targeted deploy-safety suite `40 passed`; labelled transient, uncertain-commit and session-cleanup paths asserted |
| Complete backend regression | **PASS** | `324 passed, 25 skipped` |
| Frontend regression | **PASS** | 13 suites, 35 tests |
| Frontend production build | **PASS** | optimized build compiled |
| Docker production image | **PASS** | image built successfully |
| Seed inside built artifact | **PASS** | `/app/backend/seed/seed_data.json`, 227 products |
| Python production dependency audit | **PASS** | no known vulnerabilities |
| Frontend high/critical dependency audit | **PASS** | zero high/critical |
| Provider snapshot restore | **NOT VERIFIED** | Atlas staging is Free with backups disabled; Flex would cost from $0.011/hour (up to $30/month) and was explicitly declined on 2026-10-29 |

The frontend audit reports two moderate `qs` paths under
`react-scripts > webpack-dev-server` and three low `deepmerge` paths under
SVGR/Jest. These are development/build-test toolchain paths, not packages
served by the generated static production bundle. They remain recorded rather
than forcing an unreviewed framework migration in a resilience-only phase.

## Commands

```text
python3 -m pytest backend/tests/test_deploy_safety.py -n 0 -q
CERT_MONGO_URL='mongodb://127.0.0.1:27117/?directConnection=true' \
  CERT_DB_NAME='gestionale_cert_harness' \
  python3 -m pytest backend/tests/test_certification_harness.py -n 0 -q
CERT_REQUIRE_THREE_NODE=1 CERT_MONGO_NODES='<three loopback node URLs>' \
  CERT_DB_NAME='gestionale_cert_failover' \
  python3 -m pytest backend/tests/test_replica_failover_certification.py -n 0 -q
python3 -m pytest backend/tests -q
yarn test --watchAll=false --runInBand
GENERATE_SOURCEMAP=false yarn build
python3 -m pip_audit -r backend/requirements-prod.txt
yarn audit --groups dependencies --level high
docker build -t gestionale-phase7-cert:local .
```

## Safety and remaining gate

- Production and the deployed staging service were not modified.
- No push, merge, Render configuration change, or deployment occurred.
- The temporary databases contained synthetic data only.
- Full disaster-recovery acceptance still requires restoring a real provider
  snapshot into a separate cluster and reconciling document counts and indexes.
- Atlas `gestionale-staging` remains on the Free tier. The proposed Flex upgrade
  was cancelled before submission; no billing, downtime, or configuration
  change occurred.

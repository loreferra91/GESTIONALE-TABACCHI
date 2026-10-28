# Final engineering acceptance report

Date: 2026-10-28  
Scope: Gestionale security, reliability, OpenClaw read-only integration, production deployment and final acceptance  
Status: **GO with documented residual verification items**

This report supersedes the milestone status statements in `SECURITY_RELIABILITY_REPORT.md`. It records the evidence available after the application hardening, OpenClaw activation, production smoke tests and final dependency audit.

## A. Executive summary

The hardened Gestionale and its optional OpenClaw read-only integration are deployed and operational. Authentication boundaries, production startup checks, MongoDB transaction handling, idempotency, critical stock/cash flows and four projected agent endpoints have automated regression coverage. OpenClaw has one narrow HTTPS read tool, a separate service identity stored through `SecretRef`, no write endpoint and no shell, filesystem, browser, Gateway or agent-spawn authority.

The final acceptance run passed 302 backend tests, 35 frontend tests, the frontend production build, seven OpenClaw plugin tests, Python compilation and both production dependency audits. A newly published PyMongo advisory was detected during the final audit and remediated by upgrading both dependency manifests from 4.18.2 to 4.18.3. The Python production dependency audit is now clean.

The normal Gestionale remained available with the local OpenClaw Gateway fully stopped: the public health endpoint returned HTTP 200. The Gateway was then restored and its connectivity check returned `ok`. This proves that OpenClaw is an optional supervisory layer, not an application dependency.

Release approval remains conditional only for claims that require controlled infrastructure exercises: a real isolated replica-set fault-injection suite and a verified production-backup restore drill have not been performed. No production data was modified to manufacture those proofs.

## B. Baseline, isolation and releases

- Original audit baseline: `main` at `03ae497e56335666e6d5ec3f820d494a7f576f7f`.
- Hardened application release: merge commit `b707914d3b5fd41084e2971e43a800f89188966e`.
- OpenClaw read-only release: merge commit `e9e34c977675fd194e62f69f178488a7ea8b07ea`.
- SecretRef activation hardening: merge commit `56186ed5bd9d090ba8c784f9be4c8e36b25bf15f`.
- Final acceptance branch: `codex/final-engineering-report`.
- Isolated worktree: `/Users/Lorenzo/.codex/worktrees/openclaw-hardening-review/gestionale`.

The user's modified primary checkout at `/Users/Lorenzo/gestionale` was not cleaned, overwritten or included in this work. All final acceptance edits were made in the isolated worktree.

## C. Corrections implemented

### Application security and startup

- Basic Authentication decisions use the ASGI path, with an exact public health exception and an exact GET-only allowlist for the four agent endpoints.
- The agent API has a separate constant-time-compared Bearer identity, bounded queries and timeouts, `Cache-Control: no-store`, projected response schemas and credential-free audit logging.
- Production refuses unsafe or incomplete Basic Auth configuration, wildcard/implicit CORS, unsupported transaction topology, required-index failures and business-key duplicates.
- Agent rate-limit settings are validated and bounded; malformed configuration fails with a controlled service error rather than an internal exception.

### Data integrity and retries

- Multi-document production flows use driver-managed `with_transaction` retries.
- Manual orders, merchandise receipts, Vending restocks, withdrawals and cash-related flows use stable idempotency keys and canonical request fingerprints where applicable.
- Identical retries replay the stored result; conflicting reuse is rejected with HTTP 409.
- Required unique indexes fail closed in production. Duplicate records are never automatically deleted or merged.
- Development and mock environments retain explicit compensation paths; they are not represented as equivalent to production transactions.

### Dependency security

- `backend/requirements-prod.txt` and `backend/requirements.txt` now pin PyMongo 4.18.3.
- This resolves CVE-2026-106435, a malformed BSON regular-expression decoding issue fixed in the official 4.18.3 release.
- The complete production dependency set was reinstalled together. Installed Motor 3.7.1 declares `pymongo>=4.9,<5.0`; imports with PyMongo 4.18.3 succeed and the complete backend suite passes.
- The frontend remains on a legacy Create React App dependency graph. There are no high or critical advisories; the residual transitive advisories are recorded in section H.

## D. Security acceptance

### PASS

- Authentication bypass and traversal regression coverage.
- Dedicated agent Bearer identity; operator Basic Auth is not reused.
- No application, MongoDB, Render or OpenClaw secret committed to the repository.
- Agent routes are read-only and reject unknown paths and mutation methods.
- PyMongo upgraded to the fixed release; `pip-audit` reports no known vulnerabilities.
- OpenClaw plugin production audit reports no known vulnerabilities.
- Frontend audit reports zero high and zero critical advisories.

### Architectural limitations

- The operator-facing application still uses one shared Basic Auth identity; it does not provide per-user RBAC, MFA or individual attribution.
- The application agent limiter is per process. Horizontal/multi-instance activation requires a shared edge or distributed limiter.
- Trusted Host enforcement remains deferred until the authoritative Render host allowlist is known.

## E. Data-integrity acceptance

The code paths for transactions, idempotency, unique indexes, rollback/compensation and accounting regressions pass the local automated suite. No accounting formula was intentionally changed.

Production read-only OpenClaw evidence reported 229 inventory products, 40 exhausted products, 84 Vending columns and 667 active order rows. Two capacity anomalies were reported without modifying data:

- `G11 / AMMS997`: stock 9, capacity 5.
- `D04 / AMMS2423`: stock 6, capacity 5.

These are operational observations for manual validation, not automatically corrected defects.

Real replica-set commit, abort, write-conflict, network-failure and process-crash exercises remain **NOT VERIFIED**. Mock/standalone tests do not prove those behaviors. A backup restore drill is also **NOT VERIFIED**; performing it against production solely for acceptance would create unjustified data risk.

## F. OpenClaw acceptance

- OpenClaw 2026.10.7 runs as a loopback-only LaunchAgent at `127.0.0.1:18789`.
- `gestionale-supervisor` is isolated with zero skills, a minimal deny-first profile and exactly one optional tool: `gestionale_read`.
- The tool accepts only `status`, `inventory`, `vending` or `orders`; it constructs the HTTPS paths internally, refuses redirects and bounds timeout and response size.
- The credential is held separately in Render and the OpenClaw secret store. Source configuration uses a capability-owned `SecretRef` and fails closed when resolution fails.
- Production activation and all four authenticated reads succeeded with zero tool failures.
- Anonymous, wrong-token and unknown-path checks failed closed; rate limiting returned HTTP 429 at the configured boundary and later recovered to HTTP 200.
- With the Gateway stopped, `https://gestionale-tabacchi.onrender.com/api/` returned HTTP 200. After restart, the Gateway deep status returned connectivity `ok`.
- No automation, proposal execution or approved-action/write profile is enabled.

The OpenClaw service status reports a non-standard LaunchAgent PATH because it contains the NVM binary directory. This does not prevent operation, but a future maintenance window should reinstall or normalize the service PATH after preserving the current configuration.

## G. Test evidence

### PASS

- Backend: `python3 -m pytest backend/tests -q -rs --tb=short` — **302 passed, 7 skipped**, 2 deprecation warnings.
- Python compilation: `python3 -m compileall -q backend` — passed.
- Frontend: `yarn test:ci` — **13 suites, 35 tests passed**.
- Frontend: `yarn build` — production build succeeded.
- Python production audit: `pip-audit -r backend/requirements-prod.txt` — no known vulnerabilities after PyMongo 4.18.3.
- OpenClaw plugin: **7 tests passed**, build and package validation passed.
- OpenClaw plugin production audit: zero known vulnerabilities.
- Production agent smoke: four allowlisted reads returned HTTP 200 with zero tool failures.
- Gestionale independence smoke: public API returned HTTP 200 while the OpenClaw Gateway was stopped.

### FAIL

None in the executed compilation, unit/regression, build, audit or runtime smoke checks.

### SKIPPED

Seven suites require an explicitly isolated `TEST_BACKEND_URL` and were not pointed at production:

- `backend_test.py`
- `test_aggio.py`
- `test_import_excel_full.py`
- `test_iter10_venduti_proxy.py`
- `test_iter8_features.py`
- `test_iter9_copertura.py`
- `test_parametri_bounds.py`

### NOT VERIFIED

- Real MongoDB replica-set fault injection and crash recovery.
- Write-conflict behavior under production-like concurrency.
- Provider-level snapshot restore and application-backup restore drill.
- Multi-instance distributed agent rate limiting.
- Mutation-capable OpenClaw profiles, intentionally not implemented or enabled.

The two backend warnings are from Starlette's deprecated `httpx` TestClient compatibility path. They do not fail the suite but should be removed in a future test-stack upgrade.

## H. Residual risks

### Frontend dependency graph

`yarn audit --groups dependencies` reports five affected dependency paths representing two unique transitive advisories and no high/critical finding:

- Moderate: `qs` CVE-2026-110816 through `react-scripts > webpack-dev-server > express/body-parser`; fixed upstream in `qs` 6.16.1. Exploitation requires the non-default comma serialization path on attacker-shaped objects.
- Low: `deepmerge` CVE-2026-93753 through the build/test toolchain; the advisory currently provides no patched 4.x release.

Blind lockfile overrides were not applied because these packages are nested in the legacy CRA graph and could invalidate the tested build. The durable remediation is a coordinated `react-scripts` replacement/upgrade followed by full browser regression testing.

### Runtime and operations

- Standalone compensations remain best-effort and cannot supply crash atomicity.
- External clients that omit optional idempotency keys must be inventoried before those keys become universally mandatory.
- OpenClaw global-policy warnings outside the isolated Supervisor remain separate host-hardening work.
- The two Vending capacity anomalies require human validation.
- Restore ownership, recovery time objective and evidence should be recorded during a controlled drill.

## I. Independent OpenCode review

OpenCode V2 was used read-only during the hardening and OpenClaw phases. Confirmed findings led to exact agent route allowlisting, no-store response handling, audit logging, validated rate-limit configuration, an additional Cassa Rapida fingerprint conflict test and stronger SecretRef failure behavior. Speculative incompatibility findings were rejected only after clean dependency installation and complete tests.

The final branch receives one additional read-only OpenCode pass before merge. Its accepted and rejected findings are appended to `OPENCODE_REVIEW.md`; OpenCode output is treated as reviewer input, not as proof by itself.

## J. Deployment and rollback

The hardened application, OpenClaw API and SecretRef activation are live on Render through merge commit `56186ed5bd9d090ba8c784f9be4c8e36b25bf15f`. The final PyMongo 4.18.3 pin will be deployed only after pull-request CI succeeds.

Rollback remains recoverable:

- Application: redeploy the previously verified Render commit/image.
- OpenClaw: set `AGENT_INTEGRATION_ENABLED=false`, deploy, revoke/rotate the dedicated credential and restart the loopback Gateway if secret reload does not retire the old plugin instance cleanly.
- Database: stop traffic-producing operations and use a separately approved restore incident plan; do not improvise destructive data rollback.

Final release decision: **GO** for the reviewed read-only production architecture and PyMongo security patch, subject to green CI. Do not claim full disaster-recovery or real replica-set fault-injection acceptance until the two controlled drills are completed and evidenced.

## Sources

- [PyMongo 4.18.3 release notes](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/release-notes/)
- [PyMongo 4.18.3 official release](https://github.com/mongodb/mongo-python-driver/releases/tag/4.18.3)
- [qs advisory GHSA-6pq8-jhm2-2vcw](https://github.com/advisories/GHSA-6pq8-jhm2-2vcw)
- [deepmerge advisory GHSA-gcqx-w48m-w2h4](https://github.com/advisories/GHSA-gcqx-w48m-w2h4)


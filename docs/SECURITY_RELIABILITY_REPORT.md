# Security, reliability and OpenClaw report

Date: 2026-10-27
Status: implementation complete in an isolated worktree; **not certified for production**.

## A. Executive summary

The work hardens Basic Authentication, dependency constraints, production startup, MongoDB transaction handling, idempotency for orders/Vending/cash flows, and introduces a disabled-by-default read-only OpenClaw integration API. Production now refuses to start when required credentials/CORS settings are unsafe, required business indexes cannot be established, or MongoDB cannot provide multi-document transactions.

Automated validation is green: 300 backend tests (7 skipped), 35 frontend tests, frontend production build, Python compilation, whitespace checks, and Python/frontend dependency vulnerability audits with zero known advisories. A real MongoDB replica set, Render staging/production, production data, and OpenClaw activation were not touched or verified.

## B. Baseline and isolation

- Baseline branch/commit: `main` at `03ae497e56335666e6d5ec3f820d494a7f576f7f`.
- Isolated worktree: `/Users/Lorenzo/.codex/worktrees/security-reliability-openclaw/gestionale`.
- Working branch: `codex/security-reliability-openclaw`, created from the baseline commit; the completed change set is recorded in a local commit and has not been pushed or merged.
- The original checkout had six modified backend files plus `mongodb-prodotti-ripristinati.png`. They remain present and were not overwritten, cleaned, committed, or deployed.
- The relevant pre-existing local fixes were copied into the isolated worktree and extended there.
- Initial focused validation of inherited changes: 37 tests passed. Final results are in section G.

## C. Corrections implemented

### Authentication and startup

- `backend/server.py`: auth decisions use ASGI `scope["path"]`; only `GET /api/` and `HEAD /` bypass Basic Auth. The agent prefix has its own Bearer boundary and rejects dot-segment traversal. Host-manipulation, method, route, credential, admin and write tests were added.
- Production requires complete Basic Auth credentials and explicit non-wildcard CORS origins.
- `TrustedHostMiddleware` was evaluated but not enabled because the actual Render default/custom host allowlist was not verified. Enabling it with guessed hosts could break health checks or the live service.

### Dependencies

- Production pins include FastAPI 0.135.1, Starlette 1.3.1, Pydantic 2.13.5, Motor 3.7.1 and PyMongo 4.18.2.
- Development pins include Starlette's `httpx2` TestClient dependency.
- Frontend vulnerable transitive packages were constrained where the existing toolchain remained compatible. Zod is 4.6.6. The lockfile was regenerated and the application tests/build pass.
- The legacy Create React App loader was constrained to `@svgr/webpack` 8.1.0 and patched SVGO 3.3.5. Tests and the production build validate the compatibility boundary, and the frontend audit now reports zero advisories.

### MongoDB transactions and indexes

- Startup performs a read-only `hello` topology check. Production requires logical sessions plus replica-set or sharded topology.
- Multi-document operations use the driver's `with_transaction` retry helper. Unsupported transactions may use explicit compensations only outside production.
- Production re-raises startup/migration/index failures. Required business-key duplicates block startup and are never auto-merged or deleted.

### Orders, Vending and money flows

- Manual orders and merchandise receipts use stable idempotency keys, canonical SHA-256 fingerprints, unique indexes, transaction/compensation paths, replay of identical results, and HTTP 409 for key/payload mismatch.
- Single and complete Vending restocks use the same guarantees, including stock/capacity checks and rollback tests.
- Vending withdrawals and cash creation/deletion are wired through the transaction wrapper. Versamenti, prelievi, scontrini, cassa and Cassa Rapida compare request fingerprints when a key is supplied. Cassa Rapida has an explicit conflicting-reuse regression test.
- Frontend operations store a stable key (and stable creation time where needed) in `sessionStorage`, retain it across failure/retry/refresh, and clear it only after success.
- Existing accounting formulas were not changed.

### Imports and Auto-Order

Existing transaction, backup, idempotency and rollback paths were inspected and exercised by the complete regression suite. This work did not remove backup protections or introduce destructive migrations. No claim is made that mocks prove crash behavior for every import on a real replica set.

## D. Security results

- Basic Auth bypass regression: fixed and covered, including malformed Host and encoded traversal.
- OpenClaw identity: separate constant-time-compared Bearer token, minimum production length, optional expiry, bounded query sizes/timeouts, per-process rate limit, projected responses and GET-only routes.
- Secrets are environment-only; none were added to the repository or logs.
- `pip-audit -r backend/requirements-prod.txt`: no known vulnerabilities.
- Frontend audit: zero known advisories after the controlled @svgr/SVGO upgrade.
- The service still uses one shared application Basic Auth identity. Per-user RBAC/MFA is outside this change and remains a broader authorization limitation.

## E. Data integrity results

Production multi-document writes now depend on verified transaction capability and fail closed rather than silently degrading. Driver-managed retry covers transient transaction errors and uncertain commit results. Unique keys plus fingerprints prevent duplicate effects and ambiguous replay across the newly hardened critical flows.

Development/mocked standalone Mongo uses compensating writes. These paths are regression-tested but cannot guarantee atomicity after process death, network partition, or failure of the compensation itself. That limitation is intentionally not presented as production assurance.

## F. OpenClaw

- Installed application: `/Applications/OpenClaw.app`; CLI `OpenClaw 2026.10.7 (157d8c2)`.
- Gateway: LaunchAgent running on loopback `ws://127.0.0.1:18789`, token authentication reported, Tailscale off, no channels configured, telemetry and host desktop disabled.
- The CLI configuration fails current-schema validation on several legacy/current fields. As a result, deep status, plugin, skill, agent and deep-security inventories could not be completed. Gateway health is reachable, but the CLI lacks a valid read-scope health credential.
- No config repair, plugin install, permission change, Gateway exposure or automation was performed.
- Implemented API: disabled by default, read-only `GET /api/agent/v1/{status,inventory,vending,orders}` with filtered schemas and explicit unauthorized actions.
- Proposed `GESTIONALE SUPERVISOR`: deny-first, no shell/filesystem/process/Gateway/agent-spawn powers. Proposal and Approved Actions profiles are design-only; there are no mutation endpoints.
- Full architecture, prompt-injection boundary and activation prerequisites: `docs/OPENCLAW_INTEGRATION.md`.

## G. Test evidence

### PASS

- `python -m pytest backend/tests -q`: **300 passed, 7 skipped**, 2 Starlette/AnyIO deprecation warnings.
- `yarn test:ci`: **13 suites, 35 tests passed**.
- `yarn build`: production bundle compiled successfully.
- `yarn audit --groups dependencies`: zero known advisories.
- `pip-audit -r backend/requirements-prod.txt --progress-spinner off`: no known vulnerabilities.
- `git diff --check`: passed.
- `python -m compileall -q backend`: passed.
- Clean isolated production-requirements installation: passed.
- Independent OpenCode review script: executed read-only; disposition in `docs/OPENCODE_REVIEW.md`.

### FAIL

- None in the executed application/test/build checks.

### SKIPPED

- Seven integration modules are conditionally skipped because `TEST_BACKEND_URL` was intentionally not set: `backend_test.py`, `test_aggio.py`, `test_import_excel_full.py`, `test_iter10_venduti_proxy.py`, `test_iter8_features.py`, `test_iter9_copertura.py`, and `test_parametri_bounds.py`. They are not counted as passes.

### NOT VERIFIED

- Real MongoDB replica-set commit, rollback, write-conflict, network-error and crash tests (no local daemon/container runtime was available).
- Render environment, host allowlist, deployment, runtime smoke checks and production Mongo topology.
- Production/staging backup restoration.
- OpenClaw deep audit and actual Supervisor connectivity while its local config remains invalid.
- Multi-instance distributed rate limiting.

## H. Residual risks

### Application

- Standalone compensation is best-effort and cannot equal transactional crash safety.
- Legacy callers may omit optional idempotency keys on some cash/sales APIs; updated first-party UI sends stable keys, but external clients must be inventoried before making keys universally mandatory.
- Shared Basic Auth lacks individual accountability.
- Yarn reports compatibility/peer warnings for several intentionally constrained legacy CRA dependencies. The tested build is green, but eventual replacement of CRA remains desirable maintenance.

### Infrastructure

- Production Mongo transaction support and duplicate-free index preconditions are unknown until staging/production-safe checks run.
- Agent rate limiting is in-process; horizontal scaling requires a shared limiter.
- Trusted Host enforcement needs the authoritative Render host list.
- OpenClaw configuration is invalid and should be backed up/repaired only with explicit approval.

### Missing verification

- Real replica-set fault injection, staging deployment, verified restore drill, Render smoke tests and OpenClaw end-to-end activation.

## I. Independent review

OpenCode was actually executed with the local read-only review skill. It reviewed the key auth, transaction, idempotency, OpenClaw and dependency diffs and produced 12 findings. Confirmed actionable findings were fixed; speculative dependency incompatibility was rejected through clean install and complete tests; intentional development fallbacks and distributed-rate-limit concerns remain documented. See `docs/OPENCODE_REVIEW.md`.

Review target: branch `codex/security-reliability-openclaw`, compared with baseline `03ae497e56335666e6d5ec3f820d494a7f576f7f`. OpenCode reviewed the working diff before the final local commit; no push or merge was performed.

## J. Deployment

No deployment was performed. The controlled sequence, backup/restore requirements, rollback strategy, post-deploy checks and stop conditions are in `docs/DEPLOYMENT_PLAN.md`. Release remains blocked until real replica-set/staging verification, duplicate/index preflight, backup restoration evidence, and explicit human authorization.

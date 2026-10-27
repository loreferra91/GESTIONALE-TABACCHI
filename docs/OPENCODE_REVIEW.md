# Independent OpenCode review

OpenCode V2 was run read-only on 2026-10-27 through the repository skill script against the uncommitted diff in `codex/security-reliability-openclaw`. It reached its step limit after inspecting the principal backend, frontend and dependency changes, and returned a prioritized written review. It did not modify files or run the test suite. Codex then checked every finding against the source and executed the tests independently.

## Findings and disposition

| # | OpenCode finding | Disposition |
|---|---|---|
| 1 | FastAPI/Starlette, `httpx2` and Zod pins might be incompatible | Rejected after a clean production-requirements install, 300 backend tests, 35 frontend tests and a production build. `httpx2` is Starlette 1.3.1's TestClient transport dependency. The subsequent @svgr/SVGO security upgrade also passes the same frontend checks. |
| 2 | Standalone Mongo compensation can itself fail | Confirmed residual risk. Production is fail-closed unless transactions are available; compensations remain for development/mocks and are tested, but cannot provide crash atomicity. |
| 3 | Idempotency uniqueness depends on index creation | Confirmed premise; production behavior is fail-closed for business-key duplicates and startup/index errors. Development intentionally logs/falls back to preserve standalone workflows. |
| 4 | Cassa Rapida backend idempotency was not visible | Rejected after source verification; backend already used a unique key. Review prompted an additional canonical payload fingerprint and HTTP 409 conflict test. |
| 5 | Agent auth exemption used a string prefix | Confirmed and fixed. Dot-segment suffixes no longer qualify; encoded traversal has a regression test. |
| 6 | Agent rate limit is shared by all clients using the token | Confirmed design limitation. Appropriate for one supervisor/process; multi-instance activation requires a shared edge limiter. |
| 7 | Invalid rate-limit environment value caused HTTP 500 | Confirmed and fixed with validated bounded settings and HTTP 503. |
| 8 | Development can fall back from transactions | Confirmed intentional behavior. It is prohibited in production and documented as incapable of proving production atomicity. |
| 9 | Concurrent duplicate request may temporarily return 409 | Confirmed acceptable behavior: it prevents duplication while the winner is still processing; later identical replay returns the stored result. |
| 10 | Browser canonicalization supports JSON values only | Confirmed low risk. All current operation payloads are JSON primitives/arrays/objects; the backend fingerprint is authoritative. |
| 11 | A refresh between response and key clearing can replay | Confirmed safe behavior. The backend returns the original result without repeating effects. |
| 12 | Local standalone Mongo differs from production | Confirmed and documented; real replica-set verification remains mandatory before release. |

## Additional change after review

The Mongo wrapper now uses the driver's `with_transaction` helper, which retries transient transaction callbacks and uncertain commits. A focused test verifies use of that helper. All read-only agent database operations now have bounded timeouts, not only the status ping.

## Suggested reviewer focus

- Transaction retry semantics and idempotency under a real MongoDB replica set.
- Required unique-index creation against a sanitized production-like dataset.
- Compensation failure telemetry in non-production standalone mode.
- Eventual migration away from the legacy Create React App stack; the immediate SVGO advisories are resolved with the tested @svgr 8/SVGO 3 constraint.

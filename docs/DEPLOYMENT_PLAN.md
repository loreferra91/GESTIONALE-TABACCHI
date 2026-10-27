# Controlled deployment plan

No deployment was performed as part of this work.

## Preconditions

1. Independent review of `git diff 03ae497e56335666e6d5ec3f820d494a7f576f7f..HEAD` plus uncommitted changes on `codex/security-reliability-openclaw`.
2. Green backend suite using the production dependency set, green frontend tests and production build, and zero known vulnerabilities in `requirements-prod.txt`.
3. Staging MongoDB `hello` result proves replica-set or sharded topology with logical sessions; execute real commit, rollback, duplicate-key and write-conflict tests there.
4. Inventory duplicate audit for `prodotti.codice`, `vending.colonna`, and `parametri.nome`. Do not auto-delete or merge duplicates.
5. Confirm Render has explicit `CORS_ORIGINS`, complete Basic Auth credentials, and a supported MongoDB topology. Keep `AGENT_INTEGRATION_ENABLED=false` for the first application deployment.
6. Take and verify both the existing application backup and a provider-level snapshot/export appropriate to the production tier. Record restore ownership and expected recovery time.

## Deployment sequence

1. Freeze operational imports and bulk operations for the deployment window.
2. Deploy to a staging service using production pins and a non-production replica set.
3. Run smoke tests for public health checks, protected reads/writes, manual orders, Vending restocks, cash operations, imports and rollback paths.
4. Deploy one reviewed commit to Render only after explicit authorization.
5. Observe startup logs: transaction capability must be true and every required index must succeed. Startup failure is a stop condition, not a reason to bypass a check.
6. Run read-only post-deploy checks, then one explicitly approved low-risk idempotency canary in each critical write flow.

## Rollback

- Application: redeploy the previously verified image/commit. Do not use `git reset --hard` on a working checkout.
- Database: no schema/data rollback is normally required because changes add indexes/fields. If a canary reveals incorrect writes, stop traffic-producing operations and restore only from a verified snapshot under a separate incident plan.
- OpenClaw: leave integration disabled. If later enabled, revoke the service token and set `AGENT_INTEGRATION_ENABLED=false`; the Gestionale remains independent.

## Stop conditions

- Standalone/unknown MongoDB topology in production.
- Required unique index blocked by duplicates or any essential index creation error.
- Authentication bypass, non-explicit production CORS, failed rollback/idempotency test, unexplained balance/stock change, backup verification failure, or log exposure of credentials.
- Any regression in accounting formulas, especially Vending gross/net withdrawal rules recorded in project memory.

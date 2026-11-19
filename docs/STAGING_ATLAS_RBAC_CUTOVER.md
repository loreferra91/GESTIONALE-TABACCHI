# Staging Atlas — least-privilege credential cutover (approval-gated)

## Verified current state (2026-11-19)

- Atlas project: `Gestionale Staging` (separate from production).
- Cluster: `gestionale-staging`.
- Application database: `gestionale_staging`.
- The project currently has **one database user**,
  `gestionale_staging_app`, with **`readWriteAnyDatabase` on
  `admin`**. This is broader than the single staging database.
- Atlas network access list contains **74.220.48.0/24** and
  **74.220.56.0/24**, labeled Render Oregon egress; no global
  `0.0.0.0/0` entry was observed.
- Render staging service: `srv-dbh081k9v7es73bh2i0g`,
  Git branch `openclaw/render-staging-deploy`. Production is
  a different Render service using `main`.

This audit does **not** reveal the user's password and cannot prove
whether earlier credential screenshots have been rotated.

## Preflight available in code

Run manually using **existing protected staging environment variables**:

```bash
python -m scripts.staging_rbac_check
```

The script reads only MongoDB `connectionStatus` and returns a
sanitized JSON summary of effective roles. It accepts **only**
`readWrite` on `gestionale_staging`, rejects any role on `admin`
or another database, and exits nonzero otherwise. It refuses
production/default database names or a non-staging cluster URI.

**Important:** do not enforce this as a startup health gate while
the existing broad account is still in use; that would cause an outage.

## Safest execution sequence

1. **Capture rollback first.** Using Render's authorized secret-management
   interface, preserve the existing `MONGO_URL` in a secure vault
   accessible to the operator. Never put it in GitHub, logs,
   issues, messages or the repository. Verify that the exact
   value can be restored and that the current staging service
   is healthy.
2. In the **staging Atlas project only**, create a *new* user
   with `readWrite` on database `gestionale_staging` and no
   inherited roles on `admin` or other databases. Generate a strong
   credential using Atlas/password manager and restrict its cluster scope.
   Leave the existing user untouched as a rollback path.
3. Test the candidate credential using the read-only
   `scripts.staging_rbac_check` command, pointing only at the
   known staging cluster. A wrong password or wider permission
   is a **failed gate**.
4. Explicitly approve the environment cutover. Replace **only**
   `MONGO_URL` in the Render staging service. Keep `DB_NAME`,
   `ENVIRONMENT`, `APP_USERNAME`, `APP_PASSWORD`, CORS and
   all unrelated variables unchanged. Never use a *replace-all*
   env action.
5. Validate the new Render deploy, `GET /api/`, exact Git
   revision, MongoDB health, HTTP 401 on unauthenticated backup
   endpoints, and a read-only balance/inventory reconciliation.
   Verify the staging restore mechanism's required access before
   declaring the rotation completed.
6. **Rollback on ANY failure:** restore the securely saved
   prior `MONGO_URL`, re-deploy staging, verify `GET /api/`
   and database connectivity. Do not touch Atlas data to
   make the deploy appear healthy.
7. After a stable observation window and explicit confirmation,
   revoke the previous overprivileged account and invalidate
   any exposed credential. Retest access with the scoped account.
   Keep a named, tested rollback/incident path.

## Why the cutover is not automated by this PR

The currently connected Render interface can update environment
variables but does **not** expose a verified way to retrieve and
securely preserve the previous credential for rollback. A blind
`MONGO_URL` replacement could strand staging during a restart.
An independent, off-cluster secure copy and a deliberately approved
credential cutover are prerequisites.

**No Atlas users, secrets, IP ranges, Render variables, or
production resources are changed by this PR.**

# Staging Atlas credential rotation with preserved rollback

## Preconditions

Only project `Gestionale Staging`, cluster `gestionale-staging`, database
`gestionale_staging`, and Render service
`srv-dbh081k9v7es73bh2i0g` are in scope.

Before changing credentials, deploy the code enabling
`STAGING_MONGO_ROLE_MODE`. It accepts:

- `legacy` (default): old `MONGO_URL` is active;
- `probe`: old `MONGO_URL` remains active, new credential is checked
  via `connectionStatus`, `ping`, and one read-only product query;
- `active`: runtime uses `STAGING_SCOPED_MONGO_URL` and fails startup
  if the exact `readWrite` role on `gestionale_staging` is not granted.

The production environment is unaffected even if the variables exist.

## Cutover

1. Preserve old `MONGO_URL` **untouched** in Render staging throughout
   the candidate validation; do not print, copy, or log its value.
2. Create a new Atlas user restricted to `gestionale-staging` and
   `readWrite` on `gestionale_staging`. Let Atlas generate the password.
3. Store the resulting URI *directly* in Render staging as
   `STAGING_SCOPED_MONGO_URL` using a merge environment update.
   No credentials belong in GitHub, test logs or messages.
4. Set `STAGING_MONGO_ROLE_MODE=probe`. The existing Atlas user stays
   active, so an unsuccessful candidate check does not interrupt staging.
   Verify logs show `Scoped staging credential check passed` with the
   expected database and role, without a credential payload.
5. Set `STAGING_MONGO_ROLE_MODE=active` on the same Render staging service.
   Verify the exact deployed commit, Mongo topology and read-only startup,
   health endpoint, authenticated routes and a read-only reconciliation.
6. **Rollback immediately if health fails:** set
   `STAGING_MONGO_ROLE_MODE=legacy`. The original `MONGO_URL`
   remains in Render and resumes without needing to retrieve its value.
   Do not delete the old Atlas user until a verified observation period.
7. After a stable cutover and independent recovery credentials, an Atlas
   administrator must revoke the obsolete overprivileged account and
   rotate any leaked password. That revocation requires a separate
   supported administrator action; this code cannot do it.

## Limits and security

- The legacy URI remains stored as a fallback during the observation
  period. Until the old Atlas user is revoked, exposure risk persists.
- A `probe` failure must be investigated, not ignored as proof the
  scoped account works.
- No writes or migration run during the credential probe.
- Never log Mongo URLs, usernames, passwords or Atlas API tokens.
- Use the existing `STAGING_STARTUP_MODE=read_only` so all normal
  staging restarts avoid hidden legacy migrations.
- No changes to the production MongoDB cluster or Render service.

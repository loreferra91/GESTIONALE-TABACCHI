# Continuous public health watch

GitHub Actions checks the public, credential-free /api/ health endpoints for
both production and staging every six hours. A failed run generates a GitHub
Actions failure record and can trigger standard repository Actions
notifications if the repository owner has enabled them.

The workflow uses only HTTP GET requests, retries cold starts, validates
both status and database fields, and has no MongoDB, Render or B2 secrets.
It never writes business records, reboots services or deploys code.

Limitations:
- The GitHub schedule is best effort; it can be delayed under load.
- HTTP health checks do not prove application-level workflow correctness.
- Email delivery depends on GitHub notification preferences.
- This is not a Backblaze B2 freshness monitor and cannot detect stale B2
  backups without actual read access. The local B2 monitor remains separate.

Review the Actions runs and configure repository notification preferences
to ensure operators are alerted on failures. Removing the workflow file
reverses the schedule; no schema migration or application rollback needed.

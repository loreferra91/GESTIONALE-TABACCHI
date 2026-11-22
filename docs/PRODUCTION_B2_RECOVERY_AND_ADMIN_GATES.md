# Production offsite recovery: release gate (2026-11-22)

Scope: repository tooling and **operator opt-in only**. Merge of backup scripts does not create any scheduled backup, modify Render environment, change Atlas access control, or establish a recovery point.

## Must pass before claiming B2 production backup active

1. Confirm private Backblaze B2 account and a dedicated private bucket, region, billing limits, usage caps and independent notification email. Keep staging and production prefixes and credential scopes separate.
2. On the operator-owned **Mac**, ensure `mongodump`, GPG and AWS CLI are installed and the private, non-symlinked configuration files are owned by the operator with mode `0600`. Use Atlas *read-only* backup user. Do not copy URI, key ID, application secret or passphrase to GitHub, Render or chat.
3. Verify full GPG fingerprint and export the private recovery key and passphrase to **independent** offline storage. Test that the separate recovery operator can decrypt.
4. Perform the staging backup with `bash scripts/run_local_b2_backup.sh`. Inspect its safe output; check B2 remote readback and manifest SHA-256, and then **restore the archive into a disposable, isolated MongoDB instance**, validating counts, indexes, product stock and sample native-sales relations. Do **not** restore into the operational staging database.
5. Confirm the B2 monitor output using `python3 -m scripts.b2_backup_monitor --db-name gestionale_staging --bucket ... --prefix gestionale/staging --endpoint https://s3.REGION.backblazeb2.com --max-age-hours 36`. A healthy timestamp and matching remote object size are **not** proof of decryption or restore.
6. Only after a successful staging recovery drill, configure a separate production read-only Atlas credential, private Mac environment and B2 prefix. The production runner requires `B2_PRODUCTION_ENABLED=I_HAVE_VERIFIED_PRODUCTION_BACKUP`, and offsite script requires `--allow-production`. Validate a **second isolated production-source restore** without altering the original production Atlas data.
7. Install the local macOS LaunchAgent only on the computer that will run the backup and confirm a scheduled execution and failure notifications. Mac power/network availability and backup freshness must be monitored. This tool does not install a recurring B2 monitor or wire notification delivery automatically.
8. Retention: begin with **no automatic remote deletes**; after independently verified multiple recovery points, configure a conservative B2 bucket lifecycle policy manually, retaining at least the minimum independently recoverable versions. Inspect bucket-wide versioned usage and independent B2 billing alerts. Do not enable auto-delete solely to remain in a free plan.

## Atlas staging credential decommission gate

- Verify the live staging service is using the scoped account `gestionale_staging_scoped_20261119` for database `gestionale_staging`, and ensure external recovery access still works.
- Only an Atlas project administrator can revoke `gestionale_staging_app`. The connected Atlas tool supports listing/creating users, not revocation. Revoke in **Atlas staging project only** through Security → Database Access; verify immediately after.
- The connected Render tool cannot safely remove just the legacy `MONGO_URL` key. Remove it through Render staging Environment only **after** the independent recovery point and completed scoped cutover. Never use full variable-set replacement.
- Production Atlas `atlasAdmin` privilege reduction requires identifying the actual service DB credential and a scoped replacement; never rotate or revoke blindly.

## GitHub branch protection gate

The connected GitHub integration cannot write protection/rulesets. A repository administrator must use Settings → Rules → Rulesets, targeting `main` and requiring a pull request and relevant green status checks (at minimum `Verify`, and certification workflows when they run), while explicitly considering emergency break-glass and Render auto-deploy. Do not treat a CI workflow file alone as an enforced merge policy.

## Evidence and rollback

For each checkpoint retain timestamp, environment, non-secret command output, exact Git SHA, relevant CI run, Render deploy ID, recovery counts/indexes, B2 object version and evidence of monitoring alerts. Never save credentials or raw customer data in issue comments. Until verified, mark B2 backup **NOT OPERATIONAL** and Atlas legacy credential **NOT REVOKED**. No database migration is bundled with this tooling.

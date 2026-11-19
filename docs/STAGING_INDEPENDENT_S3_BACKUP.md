# Staging — off-cluster encrypted S3 backup (activation gate)

**Not active until an independently owned destination, a recoverable
private GPG key and a separate limited MongoDB backup account exist.**
Never treat Render's local filesystem or snapshots in the same Atlas
cluster as disaster-recovery backups.

## Implemented flow

`scripts/offsite_backup_s3.py` composes the existing
`scripts/encrypted_backup.py` and AWS CLI (no new Python dependency).

1. A privileged backup runner outside the webapp reads a **0600**
   `mongodump` configuration; it never logs or passes database credentials
   on command-line arguments.
2. `mongodump --archive --gzip` is encrypted using a pinned full
   public GPG recipient fingerprint. No plaintext dump file is written.
3. The encrypted archive is uploaded to a designated S3 bucket with
   server-side encryption on top of GPG encryption.
4. The job **downloads that encrypted object back from S3** into temporary
   storage and verifies its SHA-256 and exact byte count.
5. Only after that verification does it publish `last-success.json`
   in S3, then read back and verify the manifest as well.
6. Temporary files are removed. Failed uploads or readbacks cannot
   replace the previous success manifest.

An S3-uploaded and hash-verified archive is **not** a tested restore.
The manifest retains `restore_certified: false`. A separate periodic
restore drill against a completely isolated MongoDB target remains
mandatory. `mongodump` on an active replica set does not provide
a transactionally consistent multi-collection point-in-time snapshot.

## Required before activation

- Create a private off-cluster **AWS S3 bucket**, owned separately from
  Render and Atlas. Enable bucket versioning, object encryption,
  retention/Object Lock if supported, deny public access and protect
  against accidental deletion (access policy must not permit a routine
  backup writer to delete old archives).
- Create an IAM identity restricted to writing the chosen prefix,
  reading its own uploaded objects and reading manifests.
  Deny permissions for all other buckets. Set AWS credentials only as
  protected runtime secrets or use short-lived IAM roles.
- Create an Atlas **backup reader**, scoped to the staging cluster and
  required backup privileges; do not reuse `gestionale_staging_app`
  or the webapp's credential.
- Generate an **externally recoverable** GPG key pair. Provision only
  its public key in the job, keep the private key offline and separately
  recoverable by the authorized operator. Verify fingerprint out of band.
- Install MongoDB Database Tools, `gpg`, AWS CLI on the runner; don't
  add them to the webapp container unless needed for some other purpose.
- Choose schedule and retention explicitly; avoid overlapping jobs.
  A daily 02:00 UTC schedule is a *proposal*, not an approved RPO/RTO.
- Perform the first job on **synthetic** source data with a disposable
  target, verify hash, indexes, records and stock. Then perform an
  operational staging backup and **independent real restore drill**
  before declaring backups usable.

## Example command (never with production secrets in GitHub)

```bash
python3 scripts/offsite_backup_s3.py \
  --db-name gestionale_staging \
  --mongodump-config /private/mongo-staging-backup.yaml \
  --recipient VERIFIED_FULL_GPG_FINGERPRINT \
  --s3-bucket PRIVATE_BUCKET_NAME \
  --s3-prefix gestionale/staging
```

The `--mongodump-config` file must be `0600` and contain connection
details in the official MongoDB Database Tools YAML format.
AWS credentials must not appear in this command or in Git. The
`gestionale` production database requires a separate
`--allow-production` confirmation, and is outside this rollout.

## CI coverage and honest limits

- Unit tests simulate S3 storage only; verify full readback, hash,
  no false success manifest, and no upload on invalid hash.
- The existing isolated `staging-encrypted-backup-real-restore.yml`
  separately certifies true `mongodump` + GPG + `mongorestore`
  on 25,108 **synthetic** documents, not the user's real Atlas data.
- No bucket, storage credentials, GPG recovery key, or recurring cron
  job is provisioned by this PR. Neither a verified real-world
  backup nor a physical recovery RPO/RTO may be claimed.

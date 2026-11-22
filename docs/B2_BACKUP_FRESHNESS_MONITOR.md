# B2 off-cluster backup freshness monitor (staging-first)

This opt-in monitor reads Backblaze's private **last-success.json** and checks
that it was verified within 36 hours and that the referenced encrypted object
still exists with the expected byte count. It is **read-only** for B2 and never
contacts MongoDB Atlas, downloads or decrypts database content, or modifies
business records. Failure returns exit code 2 and a non-sensitive JSON reason;
success returns 0.

## Prerequisites and manual first run

Use the protected AWS CLI profile already configured for the staging B2 backup
with read permissions, and a known private bucket/prefix. On the operator's
machine, with no credentials in command history:

    export AWS_PROFILE=gestionale-b2
    python3 -m scripts.b2_backup_monitor \
      --db-name gestionale_staging \
      --bucket YOUR_PRIVATE_BUCKET \
      --prefix gestionale/staging \
      --endpoint https://s3.YOUR-REGION.backblazeb2.com \
      --max-age-hours 36

Supply the real bucket and regional endpoint **locally**; don't commit
configuration or credentials. Use a distinct profile and prefix for production.
The monitor deliberately rejects non-Backblaze endpoints.

Schedule this read-only command on the operator's Mac only after its first
successful authenticated check. Route a **nonzero exit code** to the operator's
notification mechanism and retain logs securely (no credentials in logs).
Check every 6–12 hours; an ordinary once-daily backup with a 36-hour
freshness threshold permits one delayed execution. Do not claim any monitor
is active until its local schedule and notification delivery are observed.

## What a pass does / does not establish

- Pass: last-success's database, filename, SHA-256 field, destination and
  timestamps are internally consistent; a prior upload/read-back was reported
  verified; the named remote encrypted object currently has the expected size.
- Not proven: current archive *content* checksum, successful decryption, index
  recovery, snapshot consistency, current RPO/RTO or an isolated restore.
- A recent manifest is not a substitute for keeping at least one **independently
  restored** recovery point and a separately stored GPG private key.
- Retention is not executed by this monitor. No automatic remote deletion is
  implemented; don't shorten retention until the last recoverable backup and
  off-site recovery keys have been independently checked.

Safety: No migration, Atlas mutation, restore or Render secret changes.

#!/usr/bin/env bash
# Free-tier backup runner for an operator-owned macOS machine.
# It never writes MongoDB data, deletes remote objects, or prints credentials.
set -euo pipefail
umask 077

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${GESTIONALE_BACKUP_ENV:-$HOME/.config/gestionale-tabacchi/b2-backup.env}"

if [[ ! -f "$CONFIG_FILE" || -L "$CONFIG_FILE" ]]; then
  echo "Backup configuration missing or symlinked" >&2
  exit 2
fi
CONFIG_MODE="$(stat -f '%Lp' "$CONFIG_FILE")"
if [[ "$CONFIG_MODE" != "600" ]]; then
  echo "Backup configuration must have permission 0600" >&2
  exit 2
fi

# This configuration is a private 0600 operator-owned file, never a repo file.
# shellcheck disable=SC1090
source "$CONFIG_FILE"

: "${B2_BUCKET:?Missing B2_BUCKET}"
: "${B2_ENDPOINT:?Missing B2_ENDPOINT}"
: "${B2_GPG_FINGERPRINT:?Missing B2_GPG_FINGERPRINT}"
: "${B2_MONGODUMP_CONFIG:?Missing B2_MONGODUMP_CONFIG}"
: "${B2_DB_NAME:?Missing B2_DB_NAME}"
: "${B2_PREFIX:?Missing B2_PREFIX}"

if [[ "$B2_DB_NAME" != "gestionale_staging" && "$B2_DB_NAME" != "gestionale" ]]; then
  echo "Unsupported MongoDB database" >&2
  exit 2
fi

for command in python3 mongodump gpg aws; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing required executable: $command" >&2
    exit 2
  fi
done

# Two operators or launchd invocations cannot overlap on this computer.
# Keep the lock outside ephemeral directories. A stale lock must be
# inspected and removed manually only after confirming no backup runs.
LOCK_DIR="$HOME/.config/gestionale-tabacchi/backup-${B2_DB_NAME}.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another backup or a stale lock exists; refusing overlapping run" >&2
  exit 3
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

ARGS=(
  --db-name "$B2_DB_NAME"
  --mongodump-config "$B2_MONGODUMP_CONFIG"
  --recipient "$B2_GPG_FINGERPRINT"
  --s3-bucket "$B2_BUCKET"
  --s3-prefix "$B2_PREFIX"
  --s3-endpoint "$B2_ENDPOINT"
)
if [[ "$B2_DB_NAME" == "gestionale" ]]; then
  if [[ "${B2_PRODUCTION_ENABLED:-}" != "I_HAVE_VERIFIED_PRODUCTION_BACKUP" ]]; then
    echo "Production backup requires explicit operator opt-in" >&2
    exit 4
  fi
  ARGS+=(--allow-production)
fi

# No secret values or MongoDB URI passed through shell command arguments.
# AWS CLI reads a restricted local profile; mongodump reads its 0600 config.
export AWS_PROFILE="${B2_AWS_PROFILE:-gestionale-b2}"
python3 "$REPO_ROOT/scripts/offsite_backup_s3.py" "${ARGS[@]}"

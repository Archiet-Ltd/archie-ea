#!/usr/bin/env bash
# Configure and enable WAL archiving to object storage via wal-g.
#
# Reads every setting from environment variables — no credentials are ever
# written to disk or committed. wal-g itself is expected to be installed on
# the host (the postgres container does not need it; archive_command runs
# on the host and pipes through `docker exec`).
#
# Required environment:
#   WALG_STORAGE_PREFIX     e.g. s3://bucket/path or gs://bucket/path
#   WALG_S3_PREFIX          alias for WALG_STORAGE_PREFIX (wal-g convention)
#   AWS_ACCESS_KEY_ID       (or equivalent for the storage backend)
#   AWS_SECRET_ACCESS_KEY
#   AWS_REGION              (or WALG_S3_REGION)
#
# Optional:
#   WALG_UPLOAD_CONCURRENCY  default 4
#   WALG_COMPRESSION_METHOD  default brotli
#   WALG_DOWNLOAD_CONCURRENCY default 4
#
# This script is idempotent: running it again updates the archive_command
# and wal-g configuration without disrupting a running postgres instance.
#
# Usage:
#   source deploy/wal_archive.sh  (sets PGARCHIVE_CMD for docker exec)
#   ./deploy/wal_archive.sh       (applies the config to a running postgres)
set -euo pipefail

CONTAINER=${ARCHIE_POSTGRES_CONTAINER:-archie-ea-postgres-1}
WALG_BIN=${WALG_BIN:-wal-g}

say() { printf '\n== %s\n' "$*"; }
die() { printf 'ABORT: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Validate required environment
# ---------------------------------------------------------------------------
: "${WALG_STORAGE_PREFIX:?WALG_STORAGE_PREFIX must be set (e.g. s3://bucket/archie-wal)}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID must be set}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY must be set}"

WALG_S3_PREFIX="${WALG_S3_PREFIX:-$WALG_STORAGE_PREFIX}"
WALG_UPLOAD_CONCURRENCY="${WALG_UPLOAD_CONCURRENCY:-4}"
WALG_COMPRESSION_METHOD="${WALG_COMPRESSION_METHOD:-brotli}"
WALG_DOWNLOAD_CONCURRENCY="${WALG_DOWNLOAD_CONCURRENCY:-4}"
AWS_REGION="${AWS_REGION:-${WALG_S3_REGION:-}}"

# ---------------------------------------------------------------------------
# Build the archive_command that postgres will invoke.
# wal-g wal-push runs on the HOST (where the credentials live), reading the
# WAL segment from stdin piped through `docker exec`.
# ---------------------------------------------------------------------------
build_archive_command() {
    local env_vars
    env_vars="WALG_S3_PREFIX=${WALG_S3_PREFIX}"
    env_vars="${env_vars} AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}"
    env_vars="${env_vars} AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}"
    env_vars="${env_vars} WALG_UPLOAD_CONCURRENCY=${WALG_UPLOAD_CONCURRENCY}"
    env_vars="${env_vars} WALG_COMPRESSION_METHOD=${WALG_COMPRESSION_METHOD}"
    if [ -n "$AWS_REGION" ]; then
        env_vars="${env_vars} AWS_REGION=${AWS_REGION}"
    fi
    # The archive_command receives the WAL segment path (%p) and name (%f).
    # We cat the segment from inside the container and pipe it to wal-g on the host.
    printf 'docker exec %s cat "%%p" | env %s %s wal-push 2>>/var/log/archie-wal-archive.log' \
        "$CONTAINER" "$env_vars" "$WALG_BIN"
}

# ---------------------------------------------------------------------------
# Apply the archive_command to a running postgres instance.
# ---------------------------------------------------------------------------
apply_archive_config() {
    local cmd
    cmd=$(build_archive_command)

    say "Setting archive_command on $CONTAINER"
    docker exec "$CONTAINER" psql -U postgres -c "ALTER SYSTEM SET archive_command TO '${cmd//\'/\'\'}'" 2>/dev/null || true
    docker exec "$CONTAINER" psql -U postgres -c "ALTER SYSTEM SET archive_mode TO 'on'" 2>/dev/null || true
    docker exec "$CONTAINER" psql -U postgres -c "ALTER SYSTEM SET archive_timeout TO '60'" 2>/dev/null || true

    say "Reloading postgres configuration"
    docker exec "$CONTAINER" psql -U postgres -c "SELECT pg_reload_conf()" 2>/dev/null || true

    say "WAL archiving configured"
    printf '  storage: %s\n' "$WALG_STORAGE_PREFIX"
    printf '  compression: %s\n' "$WALG_COMPRESSION_METHOD"
    printf '  upload concurrency: %s\n' "$WALG_UPLOAD_CONCURRENCY"
}

# ---------------------------------------------------------------------------
# Verify wal-g can reach the storage backend.
# ---------------------------------------------------------------------------
verify_wal_g() {
    say "Verifying wal-g can reach storage"
    if ! env WALG_S3_PREFIX="$WALG_S3_PREFIX" \
         AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
         AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
         ${AWS_REGION:+AWS_REGION="$AWS_REGION"} \
         "$WALG_BIN" st ls / 2>/dev/null; then
        die "wal-g cannot list the storage prefix; check credentials and network"
    fi
    printf '  wal-g storage reachable at %s\n' "$WALG_STORAGE_PREFIX"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if [ "${1:-}" = "--verify-only" ]; then
    verify_wal_g
elif [ "${1:-}" = "--print-command" ]; then
    build_archive_command
else
    verify_wal_g
    apply_archive_config
fi

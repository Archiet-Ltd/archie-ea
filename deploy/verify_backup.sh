#!/usr/bin/env bash
# Prove the backups can actually be restored. Runs on the application host.
#
#   ./deploy/verify_backup.sh
#   ./deploy/verify_backup.sh --wal-g
#
# An untested backup is not a backup - it is a file that resembles one. The
# default path takes a fresh dump, restores it into a scratch database, and
# compares row counts against the live one. The live database is only ever
# READ.
#
# The --wal-g path reuses the same row-count comparison and timing report, but
# restores from object storage into a disposable scratch Postgres container.
set -euo pipefail

BACKUPS=${ARCHIE_BACKUPS:-/root/deploy-backups}
SCRATCH=${ARCHIE_RESTORE_SCRATCH_DB:-archie_restore_drill}
DB=${POSTGRES_DB:-archie}
PGUSER_=${POSTGRES_USER:-postgres}
COMPOSE="docker compose"
MODE=${1:-}
SCRATCH_CONTAINER_NAME=${SCRATCH_CONTAINER_NAME:-archie-restore-drill}
SCRATCH_PORT=${SCRATCH_PORT:-5435}
SCRATCH_DATA_DIR=${SCRATCH_DATA_DIR:-/tmp/archie-restore-drill-data}
SCRATCH_POSTGRES_PASSWORD=${SCRATCH_POSTGRES_PASSWORD:-restore-drill}
WALG_BIN=${WALG_BIN:-wal-g}
RESTORE_TARGET_TIME=${RESTORE_TARGET_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}
MAX_RESTORE_SECONDS=${MAX_RESTORE_SECONDS:-14400}

cd /root/archie-ea

say() { printf '\n== %s\n' "$*"; }
die() { printf 'ABORT: %s\n' "$*" >&2; exit 1; }
psqlx() { $COMPOSE exec -T postgres psql -U "$PGUSER_" -tAc "$1" "${2:-$DB}"; }
psql_scratch() {
    PGPASSWORD="$SCRATCH_POSTGRES_PASSWORD" psql \
        -h 127.0.0.1 -p "$SCRATCH_PORT" -U postgres -tAc "$1" "${2:-postgres}"
}

wal_g_env() {
    : "${WALG_STORAGE_PREFIX:?WALG_STORAGE_PREFIX must be set for --wal-g restore drills}"
    : "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID must be set for --wal-g restore drills}"
    : "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY must be set for --wal-g restore drills}"
    env \
        WALG_S3_PREFIX="${WALG_S3_PREFIX:-$WALG_STORAGE_PREFIX}" \
        AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
        AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
        ${AWS_REGION:+AWS_REGION="$AWS_REGION"} \
        ${WALG_S3_REGION:+WALG_S3_REGION="$WALG_S3_REGION"} \
        WALG_DOWNLOAD_CONCURRENCY="${WALG_DOWNLOAD_CONCURRENCY:-4}" \
        "$@"
}

check_existing_archives() {
    say "1. existing archives decompress"
    local found=0 broken=0 f
    for f in "$BACKUPS"/db-*.sql.gz; do
        [ -e "$f" ] || continue
        found=$((found + 1))
        if gzip -t "$f" 2>/dev/null; then
            printf '  ok      %-46s %s\n' "$(basename "$f")" "$(du -h "$f" | cut -f1)"
        else
            printf '  CORRUPT %s\n' "$(basename "$f")"
            broken=$((broken + 1))
        fi
    done
    echo "  $found archive(s), $broken corrupt"
    [ "$broken" -eq 0 ] || die "corrupt archives present"
}

compare_row_counts() {
    local live_tables copy_tables mismatch=0 table live copy
    say "4. comparing the restored copy against live"
    while IFS= read -r table; do
        live=$(psqlx "SELECT count(*) FROM \"$table\"" "$DB" 2>/dev/null || echo skip)
        [ "$live" = "skip" ] && continue
        copy=$($1 "SELECT count(*) FROM \"$table\"" "$2" 2>/dev/null || echo missing)
        if [ "$live" != "$copy" ]; then
            printf '  MISMATCH %-42s live=%s restored=%s\n' "$table" "$live" "$copy"
            mismatch=$((mismatch + 1))
        fi
    done < <(psqlx "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
    live_tables=$(psqlx "SELECT count(*) FROM pg_tables WHERE schemaname='public'" "$DB")
    copy_tables=$($1 "SELECT count(*) FROM pg_tables WHERE schemaname='public'" "$2")
    echo "  tables: live=$live_tables restored=$copy_tables"
    echo "  row-count mismatches: $mismatch"

    if [ "$mismatch" -ne 0 ] || [ "$live_tables" != "$copy_tables" ]; then
        echo
        echo "RESTORE DRILL FAILED - the backup does not reproduce the live database." >&2
        exit 1
    fi

    echo
    echo "RESTORE DRILL PASSED - $live_tables tables restored with matching row counts."
}

restore_with_dump() {
    local ts dump restore_rc errs
    say "2. taking a fresh single-database dump"
    ts=$(date +%Y%m%d-%H%M%S)
    dump="$BACKUPS/restore-drill-$ts.dump"
    $COMPOSE exec -T postgres pg_dump -U "$PGUSER_" -Fc "$DB" > "$dump"
    echo "  $(du -h "$dump" | cut -f1)  $dump"
    [ -s "$dump" ] || die "dump is empty"

    say "3. restoring into a scratch database (live database untouched)"
    psqlx "SELECT 1" postgres >/dev/null
    $COMPOSE exec -T postgres dropdb -U "$PGUSER_" --if-exists "$SCRATCH"
    $COMPOSE exec -T postgres createdb -U "$PGUSER_" "$SCRATCH"
    set +e
    $COMPOSE exec -T postgres pg_restore -U "$PGUSER_" -d "$SCRATCH" --no-owner --no-privileges \
        < "$dump" 2> "$BACKUPS/restore-drill-$ts.log"
    restore_rc=$?
    set -e
    errs=$(grep -ci "error" "$BACKUPS/restore-drill-$ts.log" 2>/dev/null || echo 0)
    echo "  pg_restore exit=$restore_rc, $errs error line(s) (see restore-drill-$ts.log)"

    compare_row_counts psqlx "$SCRATCH"

    say "5. cleaning up the scratch database"
    $COMPOSE exec -T postgres dropdb -U "$PGUSER_" --if-exists "$SCRATCH"
    rm -f "$dump"
    echo "  removed"
}

latest_walg_backup_before_target() {
    wal_g_env "$WALG_BIN" backup-list --detail 2>/dev/null | awk -v target="$RESTORE_TARGET_TIME" '
        /^base_/ {
            ts = $2
            if (ts <= target) {
                latest = $1
            }
        }
        END {
            if (latest) {
                print latest
            } else {
                exit 1
            }
        }
    '
}

cleanup_walg_scratch() {
    docker rm -f "$SCRATCH_CONTAINER_NAME" >/dev/null 2>&1 || true
    rm -rf "$SCRATCH_DATA_DIR"
}

restore_with_wal_g() {
    local backup_name restore_start restore_end restore_seconds
    trap cleanup_walg_scratch EXIT

    say "2. locating the latest base backup before $RESTORE_TARGET_TIME"
    backup_name=$(latest_walg_backup_before_target) || \
        die "no wal-g base backup found on or before $RESTORE_TARGET_TIME"
    echo "  using base backup: $backup_name"

    say "3. restoring into a disposable scratch Postgres container"
    cleanup_walg_scratch
    mkdir -p "$SCRATCH_DATA_DIR"
    restore_start=$(date +%s)

    wal_g_env "$WALG_BIN" backup-fetch "$SCRATCH_DATA_DIR" "$backup_name" \
        > /dev/null 2>"$BACKUPS/restore-drill-fetch.log" || \
        die "wal-g backup-fetch failed for $backup_name"
    chmod 700 "$SCRATCH_DATA_DIR" >/dev/null 2>&1 || true

    docker run -d \
        --name "$SCRATCH_CONTAINER_NAME" \
        -v "$SCRATCH_DATA_DIR:/var/lib/postgresql/data" \
        -e POSTGRES_PASSWORD="$SCRATCH_POSTGRES_PASSWORD" \
        -p "127.0.0.1:$SCRATCH_PORT:5432" \
        pgvector/pgvector:pg15 >/dev/null

    local deadline=$(( $(date +%s) + 120 ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if docker exec "$SCRATCH_CONTAINER_NAME" pg_isready -U postgres -q 2>/dev/null; then
            break
        fi
        sleep 2
    done
    docker exec "$SCRATCH_CONTAINER_NAME" pg_isready -U postgres -q || \
        die "scratch postgres did not become ready within 120s"

    restore_end=$(date +%s)
    restore_seconds=$((restore_end - restore_start))
    say "5. timing report"
    printf '  base backup:      %s\n' "$backup_name"
    printf '  target time:      %s\n' "$RESTORE_TARGET_TIME"
    printf '  restore duration: %ss\n' "$restore_seconds"
    [ "$restore_seconds" -le "$MAX_RESTORE_SECONDS" ] || \
        die "restore took ${restore_seconds}s, exceeding ${MAX_RESTORE_SECONDS}s"

    compare_row_counts psql_scratch "$DB"

    say "6. cleaning up the scratch container"
    cleanup_walg_scratch
    trap - EXIT
    echo "  removed"
}

case "$MODE" in
    "" )
        check_existing_archives
        restore_with_dump
        ;;
    --wal-g)
        check_existing_archives
        say "wal-g restore drill mode"
        wal_g_env "$WALG_BIN" backup-list >/dev/null 2>&1 || \
            die "wal-g cannot list the configured storage prefix"
        restore_with_wal_g
        ;;
    *)
        die "usage: ./deploy/verify_backup.sh [--wal-g]"
        ;;
esac

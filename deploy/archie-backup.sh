#!/bin/bash
# Archie database backup.
#
# Until 2026-07-30 this system had NO automated backup of any kind - the only
# dumps in existence were taken by hand during that day's change window. A bad
# migration, a mistaken DELETE or a disk failure had no recovery path.
#
# Takes a verified custom-format dump plus globals, prunes by age, and records
# a success marker so a silently-failing backup can be detected rather than
# assumed. Verification matters: an unreadable dump is worse than no dump,
# because it is mistaken for protection.
set -uo pipefail

DB=archie
CONTAINER=archie-ea-postgres-1
DIR=/var/backups/archie
LOG=/var/log/archie-backup.log
MARKER=$DIR/LAST_SUCCESS
KEEP_DAILY=14
MIN_BYTES=100000          # a dump smaller than this is not a real database

ts()  { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$(ts) $*" >> "$LOG"; }
fail(){ log "FAILED: $*"; exit 1; }

mkdir -p "$DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$DIR/archie.$STAMP.dump"

docker exec "$CONTAINER" pg_isready -U postgres -q || fail "postgres not ready"

if ! docker exec "$CONTAINER" pg_dump -U postgres -Fc -d "$DB" > "$OUT" 2>>"$LOG"; then
    rm -f "$OUT"; fail "pg_dump returned non-zero"
fi

SIZE=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
[ "$SIZE" -ge "$MIN_BYTES" ] || { rm -f "$OUT"; fail "dump implausibly small ($SIZE bytes)"; }

# Prove it is restorable, not merely present.
OBJECTS=$(docker exec -i "$CONTAINER" pg_restore --list < "$OUT" 2>>"$LOG" | wc -l)
[ "$OBJECTS" -gt 100 ] || { rm -f "$OUT"; fail "dump unreadable or near-empty ($OBJECTS objects)"; }

docker exec "$CONTAINER" pg_dumpall -U postgres --globals-only > "$DIR/globals.$STAMP.sql" 2>>"$LOG" \
    || log "WARN: globals dump failed (roles/permissions not captured this run)"

find "$DIR" -name 'archie.*.dump'  -mtime +$KEEP_DAILY -delete
find "$DIR" -name 'globals.*.sql'  -mtime +$KEEP_DAILY -delete

# Shared with the production backup step (deploy/README.md) so the marker
# format is written in exactly one place, not reimplemented a second time.
sh "$(dirname "$0")/write-backup-marker.sh" "$MARKER" "$SIZE" "$OBJECTS" "$OUT"
log "OK  size=$(numfmt --to=iec "$SIZE" 2>/dev/null || echo "$SIZE") objects=$OBJECTS retained=$(ls -1 "$DIR"/archie.*.dump 2>/dev/null | wc -l)"

# NOTE: these backups live on the SAME DISK as the database they protect. That
# is not a real disaster-recovery position - it survives a bad query, not a lost
# droplet. Shipping them off-box needs object-storage credentials (e.g. DO
# Spaces); add an `aws s3 cp`/`rclone copy` here once those exist.

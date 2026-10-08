#!/bin/sh
# Writes a verified-backup success marker in deploy/archie-backup.sh's exact
# format: one line, "<timestamp> size=<bytes> objects=<count> file=<dump path>",
# overwriting the marker on every call (never appended, so a stale run cannot
# be mistaken for a fresh one).
#
# scripts/database/deploy-schema.sh reads this marker (ARCHIE_BACKUP_MARKER,
# through scripts/database/backup_marker_to_manifest.sh) to build the manifest
# `flask cutover-capability-tenancy --apply` requires. One canonical writer,
# called from two places, so the format never drifts into a second shape:
#   - deploy/archie-backup.sh (archie-backup.timer), in this repository.
#   - the production backup step (not in this repository: it runs as
#     /opt/archie/backup.sh on the application host). Call this script with
#     the verified dump's size, object count and path right after that step's
#     own pg_restore --list verification succeeds, the same way
#     deploy/archie-backup.sh does below. See deploy/README.md.
#
# Usage: write-backup-marker.sh <marker-path> <size-bytes> <object-count> <dump-file-path>
set -eu

MARKER_PATH=${1:?marker path required}
SIZE=${2:?dump size in bytes required}
OBJECTS=${3:?pg_restore --list object count required}
FILE=${4:?verified dump file path required}

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

mkdir -p "$(dirname "$MARKER_PATH")"
echo "$(ts) size=$SIZE objects=$OBJECTS file=$FILE" > "$MARKER_PATH"

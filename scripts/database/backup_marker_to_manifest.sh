#!/bin/sh
# Turn a backup success marker into the JSON manifest
# `flask cutover-capability-tenancy --apply` requires.
#
# The marker is the format deploy/archie-backup.sh (and deploy/write-backup-marker.sh,
# which that script and the production backup step both call) write: a single
# line ending `file=<dump path>`. Only the last such line counts, matching the
# marker file being overwritten on every successful run, never appended.
#
# This is the one place that line is parsed; scripts/database/deploy-schema.sh
# calls it so the real production marker and a test marker are read the same
# way. Writes nothing and exits 0 when the marker is absent or has no file=
# line -- the caller decides what "no backup yet" means (deploy-schema.sh
# skips the cutover and refuses to apply it unprotected).
#
# Usage: backup_marker_to_manifest.sh <marker-path> <manifest-path>
set -eu

MARKER_PATH=$1
MANIFEST_PATH=$2

BACKUP_FILE=""
if [ -f "$MARKER_PATH" ]; then
    BACKUP_FILE=$(sed -n 's/.*file=//p' "$MARKER_PATH" | tail -1)
fi

if [ -n "$BACKUP_FILE" ]; then
    printf '{"backup_path": "%s"}\n' "$BACKUP_FILE" > "$MANIFEST_PATH"
fi

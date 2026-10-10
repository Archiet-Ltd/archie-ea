#!/usr/bin/env bash
# Compatibility wrapper for the restore drill.
#
# The restore logic lives in deploy/verify_backup.sh so the scratch restore and
# row-count comparison have one implementation. This wrapper keeps the old CI
# entrypoint name while routing it through wal-g mode.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/../deploy/verify_backup.sh" --wal-g

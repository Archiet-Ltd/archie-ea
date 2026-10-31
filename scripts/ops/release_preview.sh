#!/usr/bin/env bash
# Start the release candidate under the production configuration, with the job worker running and real
# mail delivered to a local sink on 127.0.0.1 (nothing leaves the machine).
#
#   scripts/ops/release_preview.sh <port> <database-url> [sink-port]
#
# SECRET_KEY and TRANSFORMATION_COMMAND_CAPABILITY_SECRET come from the environment (the preview env file);
# nothing secret is written here. The log (default ~/verify/release-preview-<port>.log) carries the one
# ENTELIM-BOOT line that the release gate reads.
set -euo pipefail

PORT="${1:?port}"
DB="${2:?database url}"
SINK_PORT="${3:-1025}"
: "${SECRET_KEY:?SECRET_KEY must be set in the environment}"
: "${TRANSFORMATION_COMMAND_CAPABILITY_SECRET:?TRANSFORMATION_COMMAND_CAPABILITY_SECRET must be set in the environment}"

LOG="${RELEASE_PREVIEW_LOG:-$HOME/verify/release-preview-${PORT}.log}"
SINK_LOG="${RELEASE_PREVIEW_SINK_LOG:-$HOME/verify/mail-sink-${SINK_PORT}.log}"
mkdir -p "$(dirname "$LOG")"

if ! (exec 3<>"/dev/tcp/127.0.0.1/${SINK_PORT}") 2>/dev/null; then
  nohup python -m aiosmtpd -n -l "127.0.0.1:${SINK_PORT}" -c aiosmtpd.handlers.Debugging stdout >>"$SINK_LOG" 2>&1 &
  sleep 1
fi

export FLASK_CONFIG=production
export DATABASE_URL="$DB"
export RUN_JOB_WORKER=1
export DISABLE_JOB_QUEUE_WORKER=
export MAIL_SUPPRESS_SEND=0
export MAIL_SERVER=127.0.0.1
export MAIL_PORT="$SINK_PORT"
export MAIL_USE_TLS=0
export MAIL_USE_SSL=0
# Mail is offered only when a sender is named; the sink accepts any address.
export MAIL_DEFAULT_SENDER="${MAIL_DEFAULT_SENDER:-Entelim Preview <preview@localhost>}"
export RELEASE_WAVE="${RELEASE_WAVE:-1}"
export PREFERRED_URL_SCHEME="${PREFERRED_URL_SCHEME:-https}"
# The preview is reached over plain http on the build machine's network; the cookie flag follows the address.
export SESSION_COOKIE_SECURE="${SESSION_COOKIE_SECURE:-false}"
export REMEMBER_COOKIE_SECURE="${REMEMBER_COOKIE_SECURE:-false}"

exec gunicorn -b "0.0.0.0:${PORT}" -w 1 --threads 8 "manage:app" >>"$LOG" 2>&1

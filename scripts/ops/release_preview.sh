#!/usr/bin/env bash
# Start the release candidate under the production configuration, with the job worker running and real
# mail delivered to a local sink on 127.0.0.1 (nothing leaves the machine).
#
#   scripts/ops/release_preview.sh <port> <database-url> [sink-port] [bind-address]
#   scripts/ops/release_preview.sh --check-sink [sink-port]
#
# The server binds 127.0.0.1 unless a bind address is given as the fourth argument; production sits behind
# nginx on loopback. The mail sink always binds 127.0.0.1. The sink runs from the app's own python (aiosmtpd
# when it is installed, scripts/ops/mail_sink.py otherwise); the launcher exits non-zero if it does not come up.
# --check-sink starts only the sink, reports that it is listening and exits.
#
# SECRET_KEY and TRANSFORMATION_COMMAND_CAPABILITY_SECRET come from the environment (the preview env file);
# nothing secret is written here. The log (default ~/verify/release-preview-<port>.log) carries the one
# ENTELIM-BOOT line that the release gate reads.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The app's python: an explicit PYTHON, else the active virtualenv, else the python on the path.
if [ -n "${PYTHON:-}" ]; then
  APP_PYTHON="$PYTHON"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  APP_PYTHON="$VIRTUAL_ENV/bin/python"
else
  APP_PYTHON="$(command -v python3 || command -v python)"
fi

sink_listening() { (exec 3<>"/dev/tcp/127.0.0.1/${SINK_PORT}") 2>/dev/null; }

start_sink() {
  if sink_listening; then
    return 0
  fi
  mkdir -p "$(dirname "$SINK_LOG")"
  if "$APP_PYTHON" -c "import aiosmtpd" 2>/dev/null; then
    nohup "$APP_PYTHON" -m aiosmtpd -n -l "127.0.0.1:${SINK_PORT}" -c aiosmtpd.handlers.Debugging stdout >>"$SINK_LOG" 2>&1 &
  else
    nohup "$APP_PYTHON" "$HERE/mail_sink.py" "$SINK_PORT" 127.0.0.1 >>"$SINK_LOG" 2>&1 &
  fi
  for _ in $(seq 1 50); do
    sink_listening && return 0
    sleep 0.2
  done
  echo "release_preview: the mail sink did not come up on 127.0.0.1:${SINK_PORT} (see ${SINK_LOG})" >&2
  return 1
}

if [ "${1:-}" = "--check-sink" ]; then
  SINK_PORT="${2:-1025}"
  SINK_LOG="${RELEASE_PREVIEW_SINK_LOG:-$HOME/verify/mail-sink-${SINK_PORT}.log}"
  start_sink
  echo "mail sink listening on 127.0.0.1:${SINK_PORT}"
  exit 0
fi

PORT="${1:?port}"
DB="${2:?database url}"
SINK_PORT="${3:-1025}"
BIND="${4:-127.0.0.1}"
: "${SECRET_KEY:?SECRET_KEY must be set in the environment}"
: "${TRANSFORMATION_COMMAND_CAPABILITY_SECRET:?TRANSFORMATION_COMMAND_CAPABILITY_SECRET must be set in the environment}"

LOG="${RELEASE_PREVIEW_LOG:-$HOME/verify/release-preview-${PORT}.log}"
SINK_LOG="${RELEASE_PREVIEW_SINK_LOG:-$HOME/verify/mail-sink-${SINK_PORT}.log}"
mkdir -p "$(dirname "$LOG")"

start_sink || exit 1

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

exec gunicorn -b "${BIND}:${PORT}" -w 1 --threads 8 "manage:app" >>"$LOG" 2>&1

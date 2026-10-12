#!/usr/bin/env bash
# Reproduce CI's backend test run in a cloud/sandbox session in minutes.
#
#   scripts/ci/cloud_test_env.sh setup                 # once per session (idempotent)
#   scripts/ci/cloud_test_env.sh shard 3               # CI shard 3 of 8, own fresh database
#   scripts/ci/cloud_test_env.sh run mydb -- tests/test_x.py -k name
#
# What it fixes, each learned the hard way on a bare Ubuntu sandbox:
#   * Python 3.12, as CI uses. 3.13 cannot build MarkupSafe<3 from source.
#   * No torch/transformers & co: the tests do not need them and they take ~20 minutes.
#   * PostgreSQL max_locks_per_transaction: create_all() on the ~800-table schema dies with
#     "out of shared memory" at the default of 64.
#   * fsync off and a bigger max_connections: parallel shards, and 3x faster schema creation.
#   * Playwright is pinned to the version whose browser revision is the one preinstalled in
#     $PLAYWRIGHT_BROWSERS_PATH, so `playwright install` (which is not allowed) is not needed.
#     Only Chromium is installed in the sandbox, so Firefox/WebKit parametrisations are
#     deselected (CI runs them; they cannot be run here).
#
# Environment overrides: VENV (default /home/user/venv), PG_PASSWORD (default postgres),
# CLOUD_ALL_BROWSERS=1 to keep the Firefox/WebKit tests.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="${VENV:-/home/user/venv}"
PG_PASSWORD="${PG_PASSWORD:-postgres}"
PG_VERSION="${PG_VERSION:-16}"
BROWSERS="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}"
SHARD_TOTAL="${CI_SHARD_TOTAL:-8}"

# Chromium revision preinstalled in the sandbox -> playwright release that expects it.
playwright_for_revision() {
  case "$1" in
    1194) echo "1.56.0" ;;
    *) echo "" ;;
  esac
}

as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }

setup_postgres() {
  local conf="/etc/postgresql/${PG_VERSION}/main/conf.d/cloud-tests.conf"
  as_root mkdir -p "$(dirname "$conf")"
  as_root tee "$conf" >/dev/null <<'CONF'
max_locks_per_transaction = 2048
max_connections = 300
fsync = off
synchronous_commit = off
full_page_writes = off
CONF
  # Restart only when the server is down or the settings are not live yet.
  if ! as_root su postgres -c "psql -Atc 'show max_locks_per_transaction'" 2>/dev/null | grep -qx 2048; then
    as_root service postgresql restart >/dev/null
  fi
  for _ in $(seq 1 30); do
    as_root su postgres -c "pg_isready -q" 2>/dev/null && break
    sleep 1
  done
  as_root su postgres -c "psql -qc \"alter user postgres password '${PG_PASSWORD}'\"" >/dev/null
}

setup_python() {
  if [ ! -x "${VENV}/bin/python" ]; then
    command -v python3.12 >/dev/null || { echo "python3.12 not found" >&2; exit 1; }
    python3.12 -m venv "${VENV}"
  fi
  local stamp="${VENV}/.cloud-deps-$(sha256sum "${REPO}/requirements.txt" "${REPO}/requirements-test.txt" | sha256sum | cut -c1-12)"
  if [ ! -f "$stamp" ]; then
    grep -viE '^(torch|sentence-transformers|transformers|Flask-RQ|flasgger|raygun4py|jsmin)' \
      "${REPO}/requirements.txt" > /tmp/cloud-req.txt
    "${VENV}/bin/pip" install -q -r /tmp/cloud-req.txt -r "${REPO}/requirements-test.txt" pytest-cov
    touch "$stamp"
  fi
  local rev pw
  rev="$(ls "${BROWSERS}" 2>/dev/null | sed -n 's/^chromium-\([0-9]*\)$/\1/p' | head -1)"
  pw="$(playwright_for_revision "${rev:-}")"
  if [ -n "$pw" ]; then
    "${VENV}/bin/pip" install -q "playwright==${pw}"
  else
    echo "warning: no known playwright release for chromium revision '${rev:-none}'; browser tests may not launch" >&2
  fi
}

new_database() {
  as_root su postgres -c "psql -qc 'drop database if exists \"$1\"' -c 'create database \"$1\"'" >/dev/null
}

run_pytest() { # <database> [pytest args...]
  local db="$1"; shift
  local filter=()
  # A caller's own -k replaces the default filter (pytest keeps only the last -k).
  if [ -z "${CLOUD_ALL_BROWSERS:-}" ] && [[ " $* " != *" -k "* ]]; then
    filter=(-k "not firefox and not webkit")
  fi
  cd "$REPO"
  env PATH="${VENV}/bin:$PATH" FLASK_CONFIG=testing SECRET_KEY=ci-only-not-secret \
      TEST_DATABASE_URL="postgresql://postgres:${PG_PASSWORD}@127.0.0.1:5432/${db}" \
      PYTHONDONTWRITEBYTECODE=1 \
      pytest -q -p no:cacheprovider -W ignore "${filter[@]}" "$@"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  setup)
    setup_postgres
    setup_python
    echo "ready: venv=${VENV} postgres=${PG_VERSION} (max_locks_per_transaction=2048)"
    ;;
  shard)
    idx="${1:?usage: cloud_test_env.sh shard <index> [-- pytest args]}"; shift
    [ "${1:-}" = "--" ] && shift
    new_database "archie_shard${idx}"
    CI_SHARD_INDEX="$idx" CI_SHARD_TOTAL="$SHARD_TOTAL" \
      run_pytest "archie_shard${idx}" --ignore=tests/smoke -p scripts.ci.test_sharding "$@"
    ;;
  run)
    db="${1:?usage: cloud_test_env.sh run <database> [-- pytest args]}"; shift
    [ "${1:-}" = "--" ] && shift
    new_database "$db"
    run_pytest "$db" "$@"
    ;;
  *)
    sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac

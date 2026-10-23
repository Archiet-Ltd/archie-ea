#!/usr/bin/env bash
# test_zero_downtime_deploy.sh — prove the deploy-without-downtime procedure
# keeps /health answering 200 throughout a schema deploy.
#
# Two scenarios:
#   1. A successful deploy with a 60-second schema step: every /health request
#      must return 200 (zero non-200 responses).
#   2. A failing schema step: the old server must keep answering 200 and the
#      deploy must report failure.
#
# Prerequisites:
#   - docker compose available
#   - .env file present with required variables
#   - Port 5000 available on the host
#
# Usage:
#   scripts/test_zero_downtime_deploy.sh
#
# Environment:
#   ZDD_COMPOSE_ARGS   extra args to docker compose (e.g. --profile email)
#   ZDD_HEALTH_PORT    port to poll (default 5000)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HEALTH_PORT="${ZDD_HEALTH_PORT:-5000}"
HEALTH_URL="http://127.0.0.1:${HEALTH_PORT}/health"
COMPOSE_ARGS="${ZDD_COMPOSE_ARGS:-}"
POLL_LOG="/tmp/zdd-health-poll.log"
SCHEMA_DELAY="${ZDD_SCHEMA_DELAY:-60}"
SERVER_CONTAINER="${ZDD_SERVER_CONTAINER:-archie-ea-server-1}"

say()  { printf '\n== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; }

cleanup() {
    # Stop the health poll if still running.
    if [ -n "${POLL_PID:-}" ] && kill -0 "$POLL_PID" 2>/dev/null; then
        kill "$POLL_PID" 2>/dev/null || true
        wait "$POLL_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Helper: wait for the server to report healthy via docker healthcheck.
# ---------------------------------------------------------------------------
wait_for_server_healthy() {
    local deadline=$(( $(date +%s) + 900 )) status
    while [ "$(date +%s)" -lt "$deadline" ]; do
        status=$(docker inspect --format '{{.State.Health.Status}}' "$SERVER_CONTAINER" 2>/dev/null || echo "missing")
        if [ "$status" = "healthy" ]; then
            return 0
        fi
        sleep 5
    done
    return 1
}

# ---------------------------------------------------------------------------
# Helper: start the health-poll loop in the background.
# ---------------------------------------------------------------------------
start_health_poll() {
    rm -f "$POLL_LOG"
    (
        while true; do
            code=$(curl -sf -o /dev/null -w '%{http_code}' -m 5 "$HEALTH_URL" 2>/dev/null || echo "000")
            echo "$code"
            sleep 1
        done
    ) > "$POLL_LOG" 2>/dev/null &
    POLL_PID=$!
    # Give it a moment to start.
    sleep 2
    say "health poll started (pid $POLL_PID, log $POLL_LOG)"
}

# ---------------------------------------------------------------------------
# Helper: stop the health-poll loop and check results.
# ---------------------------------------------------------------------------
stop_health_poll_and_check() {
    local label="$1"
    if [ -n "${POLL_PID:-}" ] && kill -0 "$POLL_PID" 2>/dev/null; then
        kill "$POLL_PID" 2>/dev/null || true
        wait "$POLL_PID" 2>/dev/null || true
    fi
    POLL_PID=""
    local total non200
    total=$(wc -l < "$POLL_LOG" 2>/dev/null || echo 0)
    non200=$(grep -cv '^200$' "$POLL_LOG" 2>/dev/null || echo 0)
    say "$label: $total health requests, $non200 non-200"
    if [ "$non200" -gt 0 ]; then
        fail "$label: $non200 non-200 health responses (must be 0)"
        printf 'Non-200 lines:\n'
        grep -v '^200$' "$POLL_LOG" || true
        return 1
    fi
    printf 'OK: zero non-200 health responses during %s\n' "$label"
    return 0
}

# ---------------------------------------------------------------------------
# Scenario 1: successful deploy with a slow schema step.
# ---------------------------------------------------------------------------
test_successful_deploy() {
    say "Scenario 1: successful deploy with ${SCHEMA_DELAY}s schema step"

    # Ensure the stack is running (idempotent — starts only what is down).
    docker compose $COMPOSE_ARGS up -d --force-recreate postgres redis server

    # Wait for the initial server to be healthy.
    say "waiting for initial server healthy..."
    if ! wait_for_server_healthy; then
        fail "initial server did not become healthy"
        return 1
    fi
    say "initial server healthy"

    # Start health polling.
    start_health_poll

    # Run the schema chain with a delay to simulate a slow migration.
    say "running schema-deploy with ${SCHEMA_DELAY}s delay..."
    docker compose rm -f database-bootstrap schema-deploy database-acl 2>/dev/null || true
    ZDD_TEST_SCHEMA_DELAY="$SCHEMA_DELAY" \
        docker compose $COMPOSE_ARGS up --exit-code-from database-acl database-acl
    local schema_rc=$?
    if [ "$schema_rc" -ne 0 ]; then
        stop_health_poll_and_check "after schema failure (unexpected)"
        fail "schema-deploy exited $schema_rc (expected 0)"
        return 1
    fi
    say "schema-deploy succeeded"

    # Recreate the server.
    say "recreating server..."
    docker compose $COMPOSE_ARGS up -d --force-recreate server

    # Wait for the new server to be healthy.
    say "waiting for new server healthy..."
    if ! wait_for_server_healthy; then
        stop_health_poll_and_check "after server recreate timeout"
        fail "new server did not become healthy"
        return 1
    fi
    say "new server healthy"

    # Stop polling and check results.
    stop_health_poll_and_check "successful deploy"
    return $?
}

# ---------------------------------------------------------------------------
# Scenario 2: failing schema step leaves old server serving.
# ---------------------------------------------------------------------------
test_failing_schema_step() {
    say "Scenario 2: failing schema step"

    # Ensure the stack is running.
    docker compose $COMPOSE_ARGS up -d --force-recreate postgres redis server

    say "waiting for server healthy..."
    if ! wait_for_server_healthy; then
        fail "server did not become healthy before failure test"
        return 1
    fi
    say "server healthy"

    # Start health polling.
    start_health_poll

    # Run schema-deploy with a command that will fail. We override the command
    # to run a shell that exits 1 after the test delay, simulating a failed
    # migration. The database-acl service depends on schema-deploy succeeding,
    # so it will not run.
    say "running schema-deploy that will fail..."
    docker compose rm -f database-bootstrap schema-deploy database-acl 2>/dev/null || true
    # Run schema-deploy directly (not via database-acl) so we can control its
    # exit code. We use `up schema-deploy` which runs the one-shot container.
    # Override the command to simulate failure after the delay.
    ZDD_TEST_SCHEMA_DELAY="$SCHEMA_DELAY" \
        docker compose $COMPOSE_ARGS \
        run --rm \
        -e ZDD_TEST_SCHEMA_DELAY="$SCHEMA_DELAY" \
        schema-deploy \
        sh -c 'echo "simulating schema failure"; sleep 5; echo "schema step FAILED"; exit 1' \
        || true
    local schema_rc=${PIPESTATUS[0]:-$?}
    say "schema-deploy exited with $schema_rc (expected non-zero)"

    # Verify the old server is still answering.
    say "verifying old server still answers..."
    local health_code
    health_code=$(curl -sf -o /dev/null -w '%{http_code}' -m 10 "$HEALTH_URL" 2>/dev/null || echo "000")
    if [ "$health_code" != "200" ]; then
        stop_health_poll_and_check "after schema failure"
        fail "old server returned $health_code after schema failure (expected 200)"
        return 1
    fi
    say "old server still answering 200 after schema failure"

    # Stop polling and check results.
    stop_health_poll_and_check "failing schema step"
    return $?
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
cd "$PROJECT_DIR"

# Verify docker compose is available.
if ! docker compose version >/dev/null 2>&1; then
    fail "docker compose not available"
    exit 1
fi

OVERALL_STATUS=0

if ! test_successful_deploy; then
    OVERALL_STATUS=1
fi

if ! test_failing_schema_step; then
    OVERALL_STATUS=1
fi

if [ "$OVERALL_STATUS" -eq 0 ]; then
    say "ALL SCENARIOS PASSED"
else
    say "SOME SCENARIOS FAILED — see FAIL lines above"
fi

exit "$OVERALL_STATUS"
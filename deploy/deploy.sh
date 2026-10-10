#!/usr/bin/env bash
# Deploy one CI-produced Archie image by immutable registry digest.
# Usage: ./deploy/deploy.sh ghcr.io/anioko/archie@sha256:<64 hex> <40-char commit>
set -euo pipefail

REPO=${ARCHIE_REPO:-/root/archie-ea}
BACKUPS=${ARCHIE_BACKUPS:-/root/deploy-backups}
STATE_DIR=${ARCHIE_RELEASE_STATE:-/root/deploy-releases}
RELEASE_FILE="$STATE_DIR/release.env"
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:5000/health}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-900}
PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-https://165-22-125-156.sslip.io}
PUBLIC_HEALTH_TIMEOUT=${PUBLIC_HEALTH_TIMEOUT:-300}
IMAGE_REF=${1:-}
EXPECTED_COMMIT=${2:-}
COMPOSE=(docker compose -f docker-compose.yml -f deploy/docker-compose.production.yml)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_VERIFIED_SCRIPT=${DEPLOY_VERIFIED_SCRIPT:-"$SCRIPT_DIR/../scripts/deploy_verified.sh"}

say() { printf '\n== %s\n' "$*"; }
die() { printf 'ABORT: %s\n' "$*" >&2; exit 1; }

[[ "$IMAGE_REF" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$ ]] || \
    die "image must be a lowercase GHCR reference pinned by sha256:[0-9a-f]{64}"
[[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || \
    die "commit must be the full [0-9a-f]{40} Git SHA"

cd "$REPO"
mkdir -p "$BACKUPS" "$STATE_DIR"
exec 9>"$STATE_DIR/deploy.lock"
flock -n 9 || die "another deployment is already running"

PREVIOUS_IMAGE=""
PREVIOUS_COMMIT=""
if [ -f "$RELEASE_FILE" ]; then
    PREVIOUS_IMAGE=$(sed -n 's/^ARCHIE_IMAGE=//p' "$RELEASE_FILE")
    PREVIOUS_COMMIT=$(sed -n 's/^ARCHIE_COMMIT=//p' "$RELEASE_FILE")
fi

image_revision() {
    docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$1"
}

verify_image() {
    local ref=$1 expected=$2 revision
    revision=$(image_revision "$ref")
    [ "$revision" = "$expected" ] || \
        die "image revision $revision does not equal requested commit $expected"
}

check_pull_capacity() {
    # Operational floor for compressed layers, extraction and live-service
    # headroom; see deploy/README.md. Never prune images to meet this budget.
    local minimum=${ARCHIE_DEPLOY_MIN_FREE_MIB-20480} docker_root disk available
    [[ "$minimum" =~ ^[1-9][0-9]{0,8}$ ]] || \
        die "ARCHIE_DEPLOY_MIN_FREE_MIB must be a positive decimal integer (1..999999999 MiB, no leading zeros)"
    docker_root=$(docker info --format '{{.DockerRootDir}}') || \
        die "cannot determine Docker storage directory"
    [[ "$docker_root" = /* && "$docker_root" != *$'\n'* ]] || \
        die "Docker storage directory is missing or not an absolute local path"
    disk=$(LC_ALL=C df -Pk -- "$docker_root") || \
        die "cannot measure free space on Docker storage filesystem: $docker_root"
    available=$(printf '%s\n' "$disk" | awk 'NR == 2 {print $4}')
    [[ "$available" =~ ^[0-9]{1,18}$ ]] || \
        die "invalid available-space measurement for Docker storage: $docker_root"
    # df -Pk reports KiB. Base 10 avoids interpreting a leading zero as octal.
    available=$((10#$available))
    say "Docker storage $docker_root: $((available / 1024)) MiB free; require $minimum MiB before pull"
    (( available >= minimum * 1024 )) || \
        die "insufficient free space on Docker storage filesystem ($docker_root): $((available / 1024)) MiB available, $minimum MiB required; review disk use and retain current/rollback images before retrying"
}

compose_with() {
    ARCHIE_IMAGE="$1" "${COMPOSE[@]}" "${@:2}"
}

verify_running_identity() {
    local ref=$1 expected=$2 cid running_id local_id running_revision
    cid=$(compose_with "$ref" ps -q server)
    [ -n "$cid" ] || return 1
    running_id=$(docker inspect --format '{{.Image}}' "$cid")
    local_id=$(docker image inspect --format '{{.Id}}' "$ref")
    running_revision=$(docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$cid")
    [ "$running_id" = "$local_id" ] && [ "$running_revision" = "$expected" ]
}

wait_for_health() {
    local deadline=$(( $(date +%s) + HEALTH_TIMEOUT )) payload
    while [ "$(date +%s)" -lt "$deadline" ]; do
        payload=$(curl -fsS -m 10 "$HEALTH_URL" 2>/dev/null || true)
        if printf '%s' "$payload" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit(1)
raise SystemExit(0 if data.get("status") == "healthy" and data.get("environment") == "production" else 1)
'; then
            return 0
        fi
        sleep 10
    done
    return 1
}

# The external load balancer deliberately needs successful probes after the
# server is replaced.  During schema deployment it can mark the backend down,
# so a one-shot public request immediately after local health is a race.  Wait
# for the public route to serve this exact production-mode application before
# running the broader page checks.
wait_for_public_health() {
    local base=$1 deadline=$(( $(date +%s) + PUBLIC_HEALTH_TIMEOUT )) payload
    while [ "$(date +%s)" -lt "$deadline" ]; do
        payload=$(curl -kfsS -m 10 "${base%/}/health" 2>/dev/null || true)
        if printf '%s' "$payload" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit(1)
raise SystemExit(0 if data.get("status") == "healthy" and data.get("environment") == "production" else 1)
'; then
            return 0
        fi
        sleep 10
    done
    return 1
}

activate() {
    local ref=$1 expected=$2
    compose_with "$ref" up -d --no-build --force-recreate server
    verify_running_identity "$ref" "$expected" && wait_for_health
}

CANDIDATE_LOG_FILE=""
CANDIDATE_LOG_ATTEMPTED=0
CANDIDATE_LOG_STATUS=1
capture_candidate_logs() {
    # Capture once: the gate and any rollback must refer to the same evidence.
    if [ "$CANDIDATE_LOG_ATTEMPTED" -eq 1 ]; then
        return "$CANDIDATE_LOG_STATUS"
    fi
    CANDIDATE_LOG_ATTEMPTED=1
    CANDIDATE_LOG_FILE=$(umask 077; mktemp "$STATE_DIR/candidate-$EXPECTED_COMMIT-XXXXXX.log" 2>/dev/null) || return 1
    chmod 600 "$CANDIDATE_LOG_FILE" || return 1
    if compose_with "$IMAGE_REF" logs --since 15m server > "$CANDIDATE_LOG_FILE" 2>&1; then
        CANDIDATE_LOG_STATUS=0
    fi
    return "$CANDIDATE_LOG_STATUS"
}

rollback() {
    # Recreating server can delete the failed container's json-file logs.
    # Keep diagnostics private; only the artifact path belongs in output.
    if ! capture_candidate_logs; then
        printf 'could not capture complete candidate logs; evidence may be partial: %s\n' \
            "${CANDIDATE_LOG_FILE:-unavailable}" >&2
    fi
    printf 'deployment failed; candidate log evidence: %s\n' \
        "${CANDIDATE_LOG_FILE:-unavailable}" >&2
    [ -n "$PREVIOUS_IMAGE" ] && [ -n "$PREVIOUS_COMMIT" ] || \
        die "deployment failed and no previous digest is recorded for rollback"
    say "rolling back to $PREVIOUS_IMAGE"
    docker pull "$PREVIOUS_IMAGE"
    verify_image "$PREVIOUS_IMAGE" "$PREVIOUS_COMMIT"
    activate "$PREVIOUS_IMAGE" "$PREVIOUS_COMMIT" || \
        die "rollback image failed identity or health verification"
    die "deployment failed; verified rollback to $PREVIOUS_COMMIT is serving"
}

check_pull_capacity
say "pre-pulling immutable release"
docker pull "$IMAGE_REF"
verify_image "$IMAGE_REF" "$EXPECTED_COMMIT"

# Parse the overlay before touching data or the running container. This proves
# the host Compose supports !reset and ARCHIE_IMAGE is set.
compose_with "$IMAGE_REF" config --quiet

say "backing up the live database"
TS=$(date +%Y%m%d-%H%M%S)
docker compose exec -T postgres pg_dumpall -U postgres | gzip > "$BACKUPS/db-$TS.sql.gz"
[ -s "$BACKUPS/db-$TS.sql.gz" ] || die "database backup is empty"

say "activating exact image digest"
if ! activate "$IMAGE_REF" "$EXPECTED_COMMIT"; then
    rollback
fi

say "running deploy_verified.sh for image-pipeline verification"
if ! IMAGE_PIPELINE_TOPOLOGY=1 AUTO_ROLLBACK=0 bash "$DEPLOY_VERIFIED_SCRIPT" "$EXPECTED_COMMIT" --skip-deploy; then
    rollback
fi

say "running post-deploy product checks"
if ! wait_for_public_health "$PUBLIC_BASE_URL"; then
    printf 'public load balancer did not route a healthy production response within %ss\n' \
        "$PUBLIC_HEALTH_TIMEOUT" >&2
    rollback
fi
if ! python3 scripts/post_deploy_verify.py --base "$PUBLIC_BASE_URL"; then
    rollback
fi
if ! capture_candidate_logs; then
    rollback
fi
log_scan_status=0
log_errors=$(grep -cE 'ERROR|CRITICAL|Traceback|safe-query failed|Failed to enrich' \
    "$CANDIDATE_LOG_FILE" 2>/dev/null) || log_scan_status=$?
if [ "$log_scan_status" -gt 1 ]; then
    printf 'could not inspect candidate log evidence: %s\n' "$CANDIDATE_LOG_FILE" >&2
    rollback
fi
# `server` depends_on database-acl: service_completed_successfully, but the
# runtime role's LOGIN grant (the last thing database-acl commits, restoring
# what it revoked to safely reconfigure ACLs) is not always visible to the
# very first connections gunicorn's freshly-forked workers open. Measured
# directly on 6 Sep 2026: `archie_runtime is not permitted to log in` for a
# handful of seconds is reproducible on a plain rollback to the SAME commit
# already serving production, with health checks returning 200 throughout
# except that exact window - it is a boot-sequence race in every deploy
# through this container chain, not a candidate-specific regression, and
# scanning it as fatal would make deploying ANY commit, including a
# no-op redeploy of the current release, permanently impossible.
if [ "$log_errors" -ne 0 ]; then
    log_errors=$(grep -E 'ERROR|CRITICAL|Traceback|safe-query failed|Failed to enrich' \
        "$CANDIDATE_LOG_FILE" 2>/dev/null | grep -vc 'role "archie_runtime" is not permitted to log in')
fi
if [ "$log_errors" -ne 0 ]; then
    printf '%s production error signal(s) found in the last 15 minutes; evidence: %s\n' \
        "$log_errors" "$CANDIDATE_LOG_FILE" >&2
    rollback
fi

tmp="$STATE_DIR/release.env.tmp.$$"
{
    printf 'ARCHIE_IMAGE=%s\n' "$IMAGE_REF"
    printf 'ARCHIE_COMMIT=%s\n' "$EXPECTED_COMMIT"
    printf 'DEPLOYED_AT=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'BACKUP=%s\n' "$BACKUPS/db-$TS.sql.gz"
} > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$RELEASE_FILE"

say "deployed and identity-verified $EXPECTED_COMMIT"
printf 'image: %s\nbackup: %s\n' "$IMAGE_REF" "$BACKUPS/db-$TS.sql.gz"

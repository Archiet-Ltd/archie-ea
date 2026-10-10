#!/usr/bin/env bash
# deploy_verified.sh — deploy + PROVE the deploy landed, for the bind-mount
# source-checkout topology that production actually runs today.
#
# WHY THIS EXISTS (incident, 8 Sep 2026):
#   `docker compose restart` was run 6+ times against production. `restart`
#   only restarts a container with its EXISTING config — it never re-reads
#   docker-compose.yml — so it silently kept the same stale, already-running
#   container alive while `git log` on the host and `curl /health` both kept
#   reporting success. `docker inspect archie-ea-server-1` eventually showed
#   the running container had ZERO bind mounts, despite docker-compose.yml
#   declaring `volumes: - './:/app'`. Every verification used in that session
#   (curl 200, host git log) proved nothing about what the CONTAINER was
#   running. Fixing it required `docker compose up -d --force-recreate
#   server`, which also surfaced a second bug: a 600-root-owned `.env` file
#   unreadable by the container's non-root `appuser`, killing the one-shot
#   schema-deploy container with a permission error that read like an app bug.
#
#   This script makes that failure mode structurally impossible to repeat
#   SILENTLY: it never trusts `docker compose ps`/health alone, always forces
#   a real container recreation, and independently proves — from OUTSIDE the
#   container's own self-report — that the bind mount is real and the code
#   running inside it is the commit that was asked for.
#
# AUTO-ROLLBACK (added 13 Sep 2026, Fortune-500-rigor phase 4):
#   Before this, a deploy that failed verification left production on
#   whatever half-applied state the failed attempt produced, for a human to
#   notice and fix by hand. Now: on verification failure, if a previous
#   deploy by this script fully verified (recorded in DEPLOY_STATE_FILE) and
#   it isn't the SHA that just failed, the script automatically redeploys
#   that previous known-good commit and re-verifies it. A successful rollback
#   still exits non-zero (the REQUESTED ref did not ship — that's a real
#   failure to report), but says clearly that production is stable on the
#   prior commit rather than broken. If the rollback attempt ALSO fails to
#   verify, that is reported distinctly and loudly: production may be in a
#   genuinely broken state and needs a human, not another automatic retry —
#   this script never rolls back more than once per invocation, deliberately,
#   to avoid flapping between two bad commits or masking a droplet-wide
#   problem (postgres down, disk full) as a code regression.
#   Disable with AUTO_ROLLBACK=0 (e.g. while deliberately testing a deploy
#   that is expected to fail, such as this feature's own verification runs).
#
# RELATIONSHIP TO deploy/deploy.sh (read before adding a third script):
#   deploy/deploy.sh + scripts/deploy.sh are a SEPARATE, more advanced
#   pipeline for a different topology: an immutable image built by CI and
#   pulled by exact `ghcr.io/...@sha256:` digest, with the production Compose
#   overlay (deploy/docker-compose.production.yml) stripping ALL bind mounts
#   (`volumes: !reset []`). That pipeline is real and committed, but as of
#   8 Sep 2026 it is NOT what is actually running: `docker inspect
#   archie-ea-server-1` on the droplet still shows `/root/archie-ea -> /app`,
#   the running image is the locally-built `archie-ea-server` (not a
#   `ghcr.io` digest), and `/root/deploy-releases/release.env` is stale
#   (points at a commit several deploys behind current HEAD). Production is
#   still deployed by checking out a ref on the host and recreating the
#   bind-mounted container — the exact topology this incident happened in.
#   THIS script is the deploy+verify tool for THAT live topology. It does not
#   duplicate deploy/deploy.sh's job (immutable-digest activation/rollback);
#   it replaces the ad-hoc `git pull && docker compose restart` that caused
#   the incident. If/when the ghcr pipeline becomes the live path, this
#   script's verification half (steps 2-5) should be folded into that one and
#   this file retired — do not let both remain the "current" answer at once.
#
#   29 Sep 2026: The image-pipeline topology (deploy/deploy.sh) is
#   now the live path. The bind-mount checks (verify_mount, step 3) are
#   RETIRED for the image-pipeline topology — they only apply to the
#   bind-mount checkout topology this script was written for. When running
#   against the image pipeline, set IMAGE_PIPELINE_TOPOLOGY=1 to skip the
#   bind-mount checks. The cross-organisation check (step 6) is the new
#   post-deploy verification for the image-pipeline topology.
#
# USAGE:
#   scripts/deploy_verified.sh <ref> [--skip-deploy]
#
#   <ref>           branch or commit to deploy (e.g. main, or a full SHA).
#                   Resolved to a full SHA via `git rev-parse` on the droplet
#                   AFTER `git fetch`, so a branch name always means its
#                   current tip, never a stale local ref.
#   --skip-deploy   run verification only, against whatever is already
#                   running (used for testing this script itself, and for
#                   re-checking a deploy without repeating it). Never
#                   triggers a rollback — it is a read-only check.
#
# ENVIRONMENT:
#   DROPLET                 ssh target (default root@134.122.105.56)
#   APP_DIR                 checkout path on droplet (default /root/archie-ea)
#   SERVER_CONTAINER        container name (default archie-ea-server-1)
#   EXPECTED_MOUNT_SOURCE   host path expected as the bind-mount source
#                           (default: same as APP_DIR)
#   EXPECTED_MOUNT_DEST     container path expected as the bind-mount target
#                           (default /app)
#   HEALTH_TIMEOUT_SECONDS  how long to wait for the container to report
#                           healthy before giving up (default 900 = 15 min;
#                           this project's boot chain is documented as
#                           taking 8-12 minutes)
#   DEPLOY_VERIFY_EMAIL / DEPLOY_VERIFY_PASSWORD
#                           if BOTH are set, an authenticated Playwright
#                           smoke check is run at the end. If either is
#                           unset, step 5 is SKIPPED with a printed warning —
#                           never silently treated as a pass.
#   AUTO_ROLLBACK           1 (default) or 0 — see AUTO-ROLLBACK above.
#   DEPLOY_STATE_FILE       where the last verified SHA is recorded (default
#                           <repo>/.deploy-state/last-verified-sha, gitignored
#                           — this is local machine state, not shared via git,
#                           since it describes what THIS script has personally
#                           watched verify from THIS machine).
#
# EXIT CODE is 0 only if the REQUESTED ref ended up verified and running. A
# successful rollback to a DIFFERENT commit still exits non-zero — see
# AUTO-ROLLBACK above for why that is correct, not a bug.
set -euo pipefail

DROPLET=${DROPLET:-root@134.122.105.56}
APP_DIR=${APP_DIR:-/root/archie-ea}
SERVER_CONTAINER=${SERVER_CONTAINER:-archie-ea-server-1}
EXPECTED_MOUNT_SOURCE=${EXPECTED_MOUNT_SOURCE:-$APP_DIR}
EXPECTED_MOUNT_DEST=${EXPECTED_MOUNT_DEST:-/app}
HEALTH_TIMEOUT_SECONDS=${HEALTH_TIMEOUT_SECONDS:-900}
AUTO_ROLLBACK=${AUTO_ROLLBACK:-1}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_STATE_FILE=${DEPLOY_STATE_FILE:-"$SCRIPT_DIR/../.deploy-state/last-verified-sha"}
SSH_OPTS=(-o ConnectTimeout=20 -o BatchMode=yes)

REQUESTED_REF=${1:-}
MODE=${2:-}

say()  { printf '\n== %s\n' "$*"; }
fail() { printf 'DEPLOY-VERIFY FAIL: %s\n' "$*" >&2; }
die()  { fail "$*"; exit 1; }

[ -n "$REQUESTED_REF" ] || die "usage: scripts/deploy_verified.sh <ref> [--skip-deploy]"
[ "$MODE" = "" ] || [ "$MODE" = "--skip-deploy" ] || die "unknown argument: $MODE"

remote() {
    # Runs a command on the droplet over ssh, positional args passed through
    # to the remote script safely (no local interpolation into remote quoting).
    ssh "${SSH_OPTS[@]}" "$DROPLET" bash -s -- "$@"
}

RESOLVED_COMMIT=""
EXPECTED_SHORT=""

# ---------------------------------------------------------------------------
# Step 1: deploy — fetch, checkout the resolved ref, run the one-shot schema
# chain to completion, then force-recreate the server container. `up -d`
# alone is NOT sufficient (see incident above): if nothing in the compose
# *config* changed, `up -d` will not recreate a container either, only
# `restart`'s cousin. `--force-recreate` is what actually guarantees a fresh
# container is created from current config on every run, deploy after
# deploy, regardless of whether the compose file itself changed.
#
# ACL-RACE INCIDENT (18 Sep 2026): `docker compose up -d --force-recreate
# server` targets ONLY the server service. Even though server declares
# `depends_on: database-acl: condition: service_completed_successfully` in
# docker-compose.yml, that dependency was NOT reliably re-run/blocked on by
# this targeted invocation — measured directly on the droplet via `docker
# inspect`, server started serving (StartedAt 09:29:13, healthy) a full
# ~8 minutes BEFORE database-acl even started (09:36:58), let alone finished
# granting the runtime role's privileges on a schema change that added a new
# table. A request landing in that window got a real, user-visible
# `psycopg2.errors.InsufficientPrivilege` on the new table, which then
# poisoned that request's transaction and cascaded into unrelated page
# failures for the rest of it. depends_on is a startup-ordering hint for a
# full `docker compose up`, not a guarantee under a single targeted
# `--force-recreate <service>` call — so the one-shot chain
# (database-bootstrap -> schema-deploy -> database-acl) is now run
# explicitly and synchronously in the foreground first. Each is `restart:
# "no"`, so `docker compose up <service>` (no `-d`) blocks until it exits
# and propagates its exit code — `up database-acl` alone is enough to also
# run its own upstream dependencies (schema-deploy, database-bootstrap) in
# order first, since compose still resolves depends_on within one `up`
# invocation for services actually being started this call.
# ---------------------------------------------------------------------------
do_deploy() {
    local ref=$1
    say "deploying $ref to $DROPLET:$APP_DIR"
    remote "$APP_DIR" "$ref" <<'REMOTE'
set -euo pipefail
APP_DIR=$1
REF=$2
cd "$APP_DIR"
git fetch --prune origin
# Resolve to a full SHA now, after fetch, so a branch name means its current
# tip and the caller gets back exactly what was deployed.
#
# A full 40-hex SHA names an object, never a ref, so it is resolved directly and
# origin/<sha> is never tried. Trying it first let a ref named like the SHA
# (a remote branch, or a tag literally called origin/<sha>, which git resolves
# ahead of refs/remotes/origin/<sha>) substitute a different commit for the one
# that was requested. This applies to the rollback path too, which calls
# do_deploy with the last verified SHA. If the object does not resolve to
# exactly that commit the deploy stops before anything is checked out.
if [[ "$REF" =~ ^[0-9a-f]{40}$ ]]; then
    TARGET=$(git rev-parse --verify --quiet "$REF^{commit}") || TARGET=""
    if [ "$TARGET" != "$REF" ]; then
        echo "DEPLOY-VERIFY FAIL: $REF does not resolve to that exact commit on the droplet" >&2
        exit 1
    fi
elif git rev-parse --verify --quiet "origin/$REF" >/dev/null; then
    TARGET=$(git rev-parse "origin/$REF")
else
    TARGET=$(git rev-parse --verify "$REF")
fi
CURRENT=$(git rev-parse HEAD)
if [ "$CURRENT" != "$TARGET" ]; then
    git checkout --detach "$TARGET"
    git reset --hard "$TARGET"
else
    echo "already at $TARGET; checkout skipped (verification still runs)"
fi
echo "RESOLVED_COMMIT=$TARGET"
# Force a fresh run of the whole one-shot schema chain on every deploy —
# these are restart:"no" containers, so an already-Exited container from a
# prior deploy would otherwise satisfy depends_on's completed_successfully
# condition without re-running against the NEW schema at all.
#
# --exit-code-from is required, not cosmetic: `docker compose up <service>`
# on its own always exits 0 regardless of whether the SERVICE's container
# exited 0 or failed — verified directly (18 Sep 2026) by a database-acl run
# that crashed with a real RuntimeError inside configure_roles.py yet the
# surrounding `docker compose up` command still reported exit code 0. Without
# --exit-code-from, `set -e` above would never see the failure and this
# script would go on to force-recreate server anyway, on a schema that may
# not have its grants applied — the exact failure mode this whole synchronous
# reordering exists to close.
docker compose rm -f database-bootstrap schema-deploy database-acl
docker compose up --exit-code-from database-acl database-acl
docker compose up -d --force-recreate server
REMOTE
}

# ---------------------------------------------------------------------------
# Step 2: wait for the container to report healthy. Necessary, not
# sufficient — a container can be "healthy" while running yesterday's code
# (that is exactly what happened in the incident).
# ---------------------------------------------------------------------------
wait_for_health() {
    say "waiting up to ${HEALTH_TIMEOUT_SECONDS}s for $SERVER_CONTAINER to report healthy"
    local deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SECONDS )) status
    while [ "$(date +%s)" -lt "$deadline" ]; do
        status=$(remote "$SERVER_CONTAINER" <<'REMOTE'
docker inspect --format '{{.State.Health.Status}}' "$1" 2>/dev/null || echo missing
REMOTE
        ) || status="unreachable"
        status=$(printf '%s' "$status" | tail -1 | tr -d '\r')
        if [ "$status" = "healthy" ]; then
            return 0
        fi
        if [ "$status" = "missing" ]; then
            fail "container $SERVER_CONTAINER does not exist"
            return 1
        fi
        sleep 10
    done
    fail "container did not report healthy within ${HEALTH_TIMEOUT_SECONDS}s (last status: ${status:-unknown})"
    return 1
}

# ---------------------------------------------------------------------------
# Step 3: verify the bind mount is REAL. This is the exact check that would
# have caught the incident immediately: the running container must actually
# have the source mount, not just the compose file declaring one.
# ---------------------------------------------------------------------------
verify_mount() {
    say "verifying bind mount on $SERVER_CONTAINER"
    local mounts
    mounts=$(remote "$SERVER_CONTAINER" <<'REMOTE'
docker inspect "$1" --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
REMOTE
    ) || { fail "could not inspect container mounts"; return 1; }
    printf '%s' "$mounts"
    if ! printf '%s' "$mounts" | grep -qF "${EXPECTED_MOUNT_SOURCE} -> ${EXPECTED_MOUNT_DEST}"; then
        fail "expected mount '${EXPECTED_MOUNT_SOURCE} -> ${EXPECTED_MOUNT_DEST}' not found; container has zero or wrong bind mounts (this is precisely the 8 Sep 2026 incident)"
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Step 4: verify the container is running the TARGET COMMIT's code, not just
# that files exist at the mount point. Use the app's own /version endpoint
# (app/_bootstrap/routes.py, build_id from app/_bootstrap/build_info.py,
# git short-8 SHA) rather than `git rev-parse` inside the container — the
# container's git identity check is documented (build_info.py's own
# docstring) to fail with "detected dubious ownership" for the appuser/root
# ownership mismatch, so a content/build-id check is the reliable path, not
# the fallback.
# ---------------------------------------------------------------------------
verify_running_code() {
    say "verifying running build_id via /version"
    local payload build_id
    payload=$(remote <<'REMOTE'
curl -fsS -m 10 http://127.0.0.1:5000/version
REMOTE
    ) || { fail "/version did not respond on the droplet"; return 1; }
    printf 'version endpoint: %s\n' "$payload"
    build_id=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("build_id",""))' 2>/dev/null) || {
        fail "/version returned unparseable JSON: $payload"
        return 1
    }
    [ -n "$build_id" ] || { fail "/version returned no build_id"; return 1; }
    if [ "$build_id" != "$EXPECTED_SHORT" ]; then
        fail "/version reports build_id=$build_id, expected $EXPECTED_SHORT (git HEAD is not what the running process sees — a stale gunicorn master under preload_app is exactly this symptom, see deploy/README.md)"
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Step 5 (optional): authenticated Playwright reachability check. Skipped,
# with a loud warning (not a silent pass), if credentials are not supplied.
# Never hardcode credentials — read only from environment.
# ---------------------------------------------------------------------------
run_smoke_check() {
    if [ -z "${DEPLOY_VERIFY_EMAIL:-}" ] || [ -z "${DEPLOY_VERIFY_PASSWORD:-}" ]; then
        printf 'WARNING: DEPLOY_VERIFY_EMAIL/DEPLOY_VERIFY_PASSWORD not set; skipping authenticated smoke check (this step proves end-to-end reachability, not just container health — skipping it is a real gap, not a pass)\n' >&2
        return 0
    fi
    say "running authenticated Playwright smoke check"
    DEPLOY_VERIFY_EMAIL="$DEPLOY_VERIFY_EMAIL" \
    DEPLOY_VERIFY_PASSWORD="$DEPLOY_VERIFY_PASSWORD" \
    DEPLOY_VERIFY_BASE_URL="${DEPLOY_VERIFY_BASE_URL:-https://165-22-125-156.sslip.io}" \
    python3 "$SCRIPT_DIR/deploy_verify_smoke.py"
}

# ---------------------------------------------------------------------------
# Step 6 (optional): cross-organisation tenant isolation check. Signs in as
# each production test organisation and asserts neither can read the other's
# records. Skipped, with a loud warning, if the test org credentials are not
# supplied. Fails the deploy on any cross-organisation read.
#
# Requires the two production test organisations to be seeded first:
#   flask seed-production-test-organisations
# ---------------------------------------------------------------------------
run_cross_org_check() {
    if [ -z "${PROD_TEST_ORG_A_EMAIL:-}" ] || [ -z "${PROD_TEST_ORG_B_EMAIL:-}" ] || \
       [ -z "${PROD_TEST_ORG_PASSWORD:-}" ]; then
        printf 'WARNING: PROD_TEST_ORG_A_EMAIL / PROD_TEST_ORG_B_EMAIL / PROD_TEST_ORG_PASSWORD not set; skipping cross-organisation tenant isolation check (this step proves tenant isolation holds after deploy — skipping it is a real gap, not a pass)\n' >&2
        return 0
    fi
    say "running cross-organisation tenant isolation check"
    DEPLOY_VERIFY_BASE_URL="${DEPLOY_VERIFY_BASE_URL:-https://165-22-125-156.sslip.io}" \
    python3 "$SCRIPT_DIR/deploy_verify_cross_org.py"
}

# ---------------------------------------------------------------------------
# Runs steps 2-6 against whatever is currently running and returns their
# combined status. Does not touch $RESOLVED_COMMIT/$EXPECTED_SHORT itself —
# the caller sets those before calling this, since step 1 (or reading the
# droplet's current HEAD, in --skip-deploy mode) is what determines them.
#
# When IMAGE_PIPELINE_TOPOLOGY=1, the bind-mount check (step 3) is skipped:
# the image pipeline strips all bind mounts, so the check would always fail.
# ---------------------------------------------------------------------------
run_verification() {
    local status=0
    if wait_for_health; then printf 'OK: %s\n' "container reports healthy"; else status=1; fi
    if [ "${IMAGE_PIPELINE_TOPOLOGY:-0}" = "1" ]; then
        printf 'OK: %s\n' "bind-mount check skipped (image-pipeline topology has no bind mounts)"
    else
        if verify_mount; then printf 'OK: %s\n' "bind mount is present and correct"; else status=1; fi
    fi
    if verify_running_code; then printf 'OK: %s\n' "running build_id matches target commit"; else status=1; fi
    if run_smoke_check; then
        # run_smoke_check also returns 0 when it deliberately skipped (no
        # credentials set) -- only claim success when it actually ran.
        if [ -n "${DEPLOY_VERIFY_EMAIL:-}" ] && [ -n "${DEPLOY_VERIFY_PASSWORD:-}" ]; then
            printf 'OK: %s\n' "authenticated smoke check reached a real page"
        fi
    else
        status=1
    fi
    if run_cross_org_check; then
        if [ -n "${PROD_TEST_ORG_A_EMAIL:-}" ] && [ -n "${PROD_TEST_ORG_B_EMAIL:-}" ] && \
           [ -n "${PROD_TEST_ORG_PASSWORD:-}" ]; then
            printf 'OK: %s\n' "cross-organisation tenant isolation holds"
        fi
    else
        status=1
    fi
    return "$status"
}

record_verified_state() {
    # $1 = the SHA that just verified clean. Local-machine bookkeeping only —
    # never pushed, never read by anything on the droplet.
    mkdir -p "$(dirname "$DEPLOY_STATE_FILE")"
    printf '%s\n' "$1" > "$DEPLOY_STATE_FILE"
}

read_last_verified_sha() {
    [ -f "$DEPLOY_STATE_FILE" ] && cat "$DEPLOY_STATE_FILE" || true
}

# ---------------------------------------------------------------------------
# Primary attempt: deploy (unless --skip-deploy) and verify the requested ref.
# ---------------------------------------------------------------------------
if [ "$MODE" != "--skip-deploy" ]; then
    DEPLOY_OUTPUT=$(do_deploy "$REQUESTED_REF") || { fail "deploy step (checkout/compose up) failed for $REQUESTED_REF"; DEPLOY_OUTPUT=""; }
    if [ -n "$DEPLOY_OUTPUT" ]; then
        printf '%s\n' "$DEPLOY_OUTPUT"
        RESOLVED_COMMIT=$(printf '%s\n' "$DEPLOY_OUTPUT" | sed -n 's/^RESOLVED_COMMIT=//p' | tail -1)
    fi
else
    say "--skip-deploy: verifying the currently-running deployment only"
    RESOLVED_COMMIT=$(remote "$APP_DIR" <<'REMOTE'
cd "$1"
git rev-parse HEAD
REMOTE
    ) || die "could not read the current commit on the droplet"
fi

PRIMARY_STATUS=1
if [ -n "$RESOLVED_COMMIT" ]; then
    EXPECTED_SHORT=${RESOLVED_COMMIT:0:8}
    say "target commit: $RESOLVED_COMMIT (short: $EXPECTED_SHORT)"
    if run_verification; then
        PRIMARY_STATUS=0
    fi
else
    fail "could not determine the commit that was actually checked out on the droplet"
fi

OVERALL_STATUS=$PRIMARY_STATUS
ROLLBACK_ATTEMPTED=0
ROLLBACK_STATUS=""
ROLLBACK_SHA=""

if [ "$PRIMARY_STATUS" -ne 0 ] && [ "$MODE" != "--skip-deploy" ] && [ "$AUTO_ROLLBACK" = "1" ]; then
    LAST_GOOD=$(read_last_verified_sha)
    if [ -n "$LAST_GOOD" ] && [ "$LAST_GOOD" != "$RESOLVED_COMMIT" ]; then
        ROLLBACK_ATTEMPTED=1
        ROLLBACK_SHA="$LAST_GOOD"
        say "AUTO-ROLLBACK: $REQUESTED_REF (resolved $RESOLVED_COMMIT) failed verification -- redeploying last known-good commit $LAST_GOOD"
        ROLLBACK_OUTPUT=$(do_deploy "$LAST_GOOD") || { fail "rollback deploy step failed"; ROLLBACK_OUTPUT=""; }
        if [ -n "$ROLLBACK_OUTPUT" ]; then
            printf '%s\n' "$ROLLBACK_OUTPUT"
            RESOLVED_COMMIT=$(printf '%s\n' "$ROLLBACK_OUTPUT" | sed -n 's/^RESOLVED_COMMIT=//p' | tail -1)
            EXPECTED_SHORT=${RESOLVED_COMMIT:0:8}
            if run_verification; then
                ROLLBACK_STATUS=0
                record_verified_state "$RESOLVED_COMMIT"
            else
                ROLLBACK_STATUS=1
            fi
        else
            ROLLBACK_STATUS=1
        fi
    else
        say "AUTO-ROLLBACK: no different known-good commit on record ($DEPLOY_STATE_FILE) -- nothing to roll back to"
    fi
elif [ "$PRIMARY_STATUS" -eq 0 ]; then
    record_verified_state "$RESOLVED_COMMIT"
fi

say "summary"
if [ "$PRIMARY_STATUS" -eq 0 ]; then
    printf 'DEPLOY VERIFIED: commit %s is running, mounted and reachable.\n' "$RESOLVED_COMMIT"
elif [ "$ROLLBACK_ATTEMPTED" -eq 1 ] && [ "$ROLLBACK_STATUS" = "0" ]; then
    printf 'DEPLOY FAILED, ROLLED BACK SUCCESSFULLY: %s did not verify; production is stable and verified on the prior commit %s. The requested deploy did NOT ship -- this is still a failure to fix and redeploy, not a pass.\n' "$REQUESTED_REF" "$ROLLBACK_SHA" >&2
elif [ "$ROLLBACK_ATTEMPTED" -eq 1 ]; then
    printf 'CRITICAL: %s did not verify, AND the automatic rollback to %s ALSO failed to verify. Production may be in a broken state that is NOT a code regression (check postgres, disk space, the droplet itself) -- this needs a human now, not another automatic retry.\n' "$REQUESTED_REF" "$ROLLBACK_SHA" >&2
else
    printf 'DEPLOY NOT VERIFIED — see FAIL lines above. Do not report this deploy as done.\n' >&2
fi
exit "$PRIMARY_STATUS"

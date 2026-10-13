# Runbook: Deploy without downtime

Replaces the previous deploy procedure (`docker compose up -d --force-recreate`)
which took the site down for 8–12 minutes while the schema-deploy container ran.
The new procedure keeps the old server answering throughout.

## Background

Before this change, `docker compose up -d --force-recreate` recreated every
service. The server container declared `depends_on: database-acl` (which
transitively depended on `schema-deploy`), so the server did not start until the
full schema chain completed. During that window — typically 8 to 12 minutes —
`/` and `/health` returned 502.

The fix decouples the server from the schema-deploy chain in
`docker-compose.yml`. The server now depends only on `postgres` (healthy) and
`redis` (started). The schema chain (`database-bootstrap` → `schema-deploy` →
`database-acl`) still exists and runs as a one-off before the server is
recreated.

## Backward compatibility rule

Every schema change must be backward-compatible for one release: add before use,
drop after. Concretely:

- **Add** a column as nullable (or with a default) in release N. Code in release
  N+1 may rely on it.
- **Drop** a column no earlier than release N+1, after confirming no code in
  release N reads it.
- **Never** rename a column or table in a single release. Add the new name, dual-write
  for one release, then drop the old name.
- **Never** change a column type in place. Add a new column, backfill, switch
  readers, then drop the old column.

This rule ensures the old server (running release N) can serve against the new
schema (release N+1) while the new server starts. Without it, a schema-deploy
that adds a NOT NULL column or drops a column the old code reads would break the
running old server mid-deploy.

## Pre-deploy gate

```bash
python scripts/verify.py --tag static
```

Must be green before proceeding.

## Deploy sequence (production host)

All commands run on the app droplet (`ssh root@10.106.0.6` via the proxy),
repo at `/root/archie-ea`.

### Step 1 — fetch and checkout

```bash
cd /root/archie-ea
git fetch --prune origin
git checkout --detach origin/main
git reset --hard origin/main
```

Record the deployed SHA:

```bash
git rev-parse HEAD
```

### Step 2 — run the schema chain while the old server keeps serving

The old server is still running and answering requests. Run the one-shot schema
chain in the foreground. It blocks until complete and propagates the exit code.

```bash
docker compose rm -f database-bootstrap schema-deploy database-acl
docker compose up --exit-code-from database-acl database-acl
```

If this command exits non-zero, **stop**. The schema step failed. The old server
is still running and answering requests. Investigate the failure, fix it, and
retry from Step 2. Do not proceed to Step 3.

### Step 3 — recreate the server

```bash
docker compose up -d --force-recreate server
```

If the worker profile is enabled:

```bash
docker compose --profile email up -d --force-recreate worker
```

### Step 4 — wait for the new server to report healthy

```bash
deadline=$(( $(date +%s) + 900 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    status=$(docker inspect --format '{{.State.Health.Status}}' archie-ea-server-1 2>/dev/null)
    if [ "$status" = "healthy" ]; then
        echo "server healthy"
        break
    fi
    sleep 10
done
```

The 900-second (15-minute) timeout accommodates the full boot chain
(init-db → schema-upgrade → reconcile-schema → backfills → gunicorn).

### Step 5 — verify the deploy

```bash
curl -fsS -m 10 http://127.0.0.1:5000/health
curl -fsS -m 10 http://127.0.0.1:5000/
```

Both must return 200 and the health endpoint must report `"status": "healthy"`.

Confirm the running code matches the deployed SHA:

```bash
curl -fsS -m 10 http://127.0.0.1:5000/version | python3 -c "import json,sys; print(json.load(sys.stdin)['build_id'])"
```

### Step 6 — write the deploy log

Append to the deploy log with the SHA, timestamp, and schema-deploy exit code:

```bash
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) deployed $(git rev-parse HEAD) schema-exit=0" >> /root/deploy.log
```

## Cold start (no server running)

When no server is running (first boot after host restart, or a deliberate
shutdown), run the schema chain first, then start the server:

```bash
cd /root/archie-ea
docker compose up -d postgres redis
# Wait for postgres healthy:
until docker compose exec -T postgres pg_isready -U postgres; do sleep 2; done
# Run schema chain:
docker compose up --exit-code-from database-acl database-acl
# Start server:
docker compose up -d --force-recreate server
```

## Rollback

```bash
cd /root/archie-ea
git checkout --detach <previous-commit>
git reset --hard <previous-commit>
docker compose rm -f database-bootstrap schema-deploy database-acl
docker compose up --exit-code-from database-acl database-acl
docker compose up -d --force-recreate server
```

Schema changes are backward-compatible (see rule above), so the old code runs
against the new schema without error. Rolling back does not require reversing
the schema step.

## Verifying zero downtime during a deploy

From a separate terminal, start a health-check loop before Step 2:

```bash
while true; do
    curl -sf -o /dev/null -w '%{http_code}\n' -m 5 http://127.0.0.1:5000/health || echo "FAIL"
    sleep 1
done | tee /tmp/health-check.log
```

After the deploy completes, confirm no line in `/tmp/health-check.log` is
anything other than `200`:

```bash
grep -v '^200$' /tmp/health-check.log
```

This must produce no output. Any non-200 line is a downtime event.
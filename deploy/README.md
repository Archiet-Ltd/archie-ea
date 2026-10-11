# Deployment artefacts (host-level, not container-level)

These live outside Docker on the app droplet. They are checked in here because
they otherwise exist on exactly one machine with no version history.

| File | Installed to | Purpose |
|---|---|---|
| `archie-watchdog.sh` | `/usr/local/bin/` | Restarts `archie-ea-server-1` when `/health` stops answering |
| `archie-watchdog.{service,timer}` | `/etc/systemd/system/` | Runs the watchdog every 60s |
| `Caddyfile.proxy` | `/etc/caddy/Caddyfile` on the **proxy** droplet (165.22.125.156) | TLS termination, timeouts, HSTS, upstream health checks; proxies to the app over the VPC address 10.106.0.6 |
| `caddy/caddy-restart-override.conf` | `/etc/systemd/system/caddy.service.d/override.conf` on the **proxy** droplet | `Restart=on-failure`. The unit shipped with no `Restart=` at all, so systemd's default of `Restart=no` applied to the single ingress point |
| `archie-backup.sh` | `/usr/local/bin/` | Verified 6-hourly `pg_dump` with retention and a success marker |
| `archie-backup.{service,timer}` | `/etc/systemd/system/` | Schedules the backup |
| `write-backup-marker.sh` | next to whichever backup script calls it (`/usr/local/bin/` for `archie-backup.sh`) | Writes the one `LAST_SUCCESS` marker format; called by `archie-backup.sh` and by the production backup step, so the format is never duplicated |

## Backups

`archie-backup.sh` runs every 6 hours when `archie-backup.timer` is installed.
It verifies each dump is restorable (`pg_restore --list`) rather than merely
present, prunes at 14 days, and writes `/var/backups/archie/LAST_SUCCESS` in
the format `write-backup-marker.sh` writes (`<timestamp> size=<bytes>
objects=<count> file=<dump path>`). The watchdog warns if that marker is more
than 18 hours old, because a backup that stops silently looks exactly like one
that works.

These dumps are on the SAME DISK as the database. That survives a bad query, not
a lost droplet. Shipping them off-box needs object-storage credentials.

Install:

    cp archie-backup.sh write-backup-marker.sh /usr/local/bin/
    chmod +x /usr/local/bin/archie-backup.sh /usr/local/bin/write-backup-marker.sh
    cp archie-backup.service archie-backup.timer /etc/systemd/system/
    systemctl daemon-reload && systemctl enable --now archie-backup.timer

### The capability tenancy cutover needs to see a backup

`scripts/database/deploy-schema.sh`'s `cutover-capability-tenancy --apply` step
refuses to run without a backup it can name (`ARCHIE_BACKUP_MARKER`, default
`/var/backups/archie/LAST_SUCCESS` inside the `schema-deploy` container). The
`docker-compose.yml` `schema-deploy` service mounts `ARCHIE_BACKUP_HOST_DIR`
read-only at that path; locally it defaults to a project directory that stays
empty, so the step correctly skips with nothing further to configure.

**Production step (one-time, and whenever the backup location changes):** this
droplet's real backups are not `archie-backup.sh` -- a separate script,
`/opt/archie/backup.sh`, already runs a verified `pg_dump` into
`/opt/archie/backups` before every deploy and is not in this repository.
Point the mount at it and give it the one marker-writing call:

1. In the `.env` used by `docker compose` on this host, set
   `ARCHIE_BACKUP_HOST_DIR=/opt/archie/backups`.
2. Install `write-backup-marker.sh` next to `/opt/archie/backup.sh` and add one
   line at the end of that script's own verified-dump step, after it has
   confirmed the dump restores (the same way `archie-backup.sh` calls it
   below): `write-backup-marker.sh /opt/archie/backups/LAST_SUCCESS <size>
   <object count> <dump path>`.

No second marker format: both call the same `write-backup-marker.sh`.

## Why the watchdog exists

Gunicorn's `timeout` cannot detect wedged `gthread` request threads: the worker's
main thread keeps answering the arbiter heartbeat while every request thread is
blocked. On 24-30 July 2026 all 8 request threads were pinned this way and the
site returned nothing for six days while the container reported healthy.

Install:

    install -d -m 0700 /root/deploy-releases
    cp archie-watchdog.sh /usr/local/bin/ && chmod +x /usr/local/bin/archie-watchdog.sh
    cp archie-watchdog.service archie-watchdog.timer /etc/systemd/system/
    systemctl daemon-reload && systemctl enable --now archie-watchdog.timer

Create the release directory before enabling the watchdog, even on a host that
has not deployed an immutable release yet. The directory command is idempotent.
The watchdog and `deploy/deploy.sh` coordinate through
`/root/deploy-releases/deploy.lock`; the watchdog deliberately refuses to restart
when it cannot open that lock. It holds the lock through its health decision,
forensic capture and restart, and never starts a created or stopped container.

If using a custom directory, create it with `install -d -m 0700 /absolute/path`
and set **the identical absolute `ARCHIE_RELEASE_STATE` value in both processes**.
Set it in the host deployment process environment and, before enabling the timer,
in a systemd drop-in for `archie-watchdog.service` (via
`systemctl edit archie-watchdog.service`):

    [Service]
    Environment="ARCHIE_RELEASE_STATE=/absolute/path"

Then run `systemctl daemon-reload`. An interactive shell export does not configure
the systemd service, and the workstation deploy wrapper does not forward this
setting. Preserve the shared lock file during operation; deleting it can let the
two processes lock different files. A busy lock defers the watchdog; if the
deployment encounters an active watchdog check, its nonblocking lock aborts
safely and deployment can be retried after that check finishes.

## Deploying code changes — read this first

`gunicorn.conf.py` sets `preload_app = True`. The application is imported once in
the gunicorn MASTER process, and workers are forked from it.

**`SIGHUP` does NOT reload Python code under preload.** It reloads configuration
and gracefully re-forks workers — from a master still holding the old modules.
Worker PIDs change, the site stays up, and every check looks like a successful
deploy while the running code is unchanged.

This was discovered on 2026-07-31: the master had been running since 13:14 the
previous day while the tree on disk was hours newer, so several Python fixes
appeared deployed and were not.

    Python code (.py)          -> docker restart archie-ea-server-1   (~180s)
    New blueprints/routes      -> docker restart archie-ea-server-1
    Every application change   -> deploy a new CI-produced image digest

Production does not mount the host checkout into `/app`. Templates, static
assets, Python code and dependencies are all part of the same immutable image.

## The deploy procedure

The CI `release-image` job runs only after every release gate succeeds. It
builds once, pushes the image to GHCR, and retains `release.json` containing the
full commit, registry digest and workflow run. A tag is never a production
deploy input.

From a workstation, use those exact values:

    ./scripts/deploy.sh \
      ghcr.io/anioko/archie@sha256:<64-hex-digest> \
      <40-character-commit> --yes

The host script pre-pulls the digest, checks its OCI revision label against the
commit, validates the source-free production Compose overlay, dumps the
database, and recreates the application with `--no-build`. It then proves the
running container image ID and revision, checks health, public pages and local
container errors, and atomically records `deploy-releases/release.env`.
Failure restores and verifies the previously recorded digest.

Before the pull, the host script requires **20 GiB (20480 MiB) free** on the
filesystem containing the local daemon's `DockerRootDir`, measured with
`docker info` and `df -Pk`. Missing or invalid measurements abort deployment,
before pulling, dumping the database or changing services. The script never
prunes images automatically; retain the running and rollback digests when
reviewing disk usage.

This is an operational headroom floor, not a computed image-size guarantee.
A 4.41 GB release pull exhausted a host that began with 3.1 GB free while
extracting Torch; the 20 GiB floor budgets room for compressed downloads,
expanded layers and continued live writes. Registry compressed sizes alone
cannot bound extraction space. Reassess this floor when dependencies grow.
Set `ARCHIE_DEPLOY_MIN_FREE_MIB` in the **host deploy process environment** to
an assessed positive decimal MiB budget (1..999999999, without leading zeros);
an explicitly empty value is invalid. The workstation wrapper does not forward
this setting. Lowering it reduces the protection.

This check assumes deployment runs beside the local Docker daemon. It does not
reserve disk, measure inode availability, size database backups, or account for
concurrent writers or a separately mounted containerd image store. Those layouts
need their own capacity assessment; a successful check is no guarantee against
disk exhaustion. Cached releases still require the floor so the host retains
operating headroom.

The deployment gate saves candidate server logs once to a unique
`deploy-releases/candidate-<commit>-<random>.log` file (under `ARCHIE_RELEASE_STATE`
when overridden). The root-run host deployer creates it with mode **0600**.
The error gate counts that saved evidence, and rollback captures it first even
when an earlier health or product check fails. Recreating the container therefore
does not erase the evidence. Failure output includes the path, never log contents.
Failed retrieval or inspection fails deployment closed; a retrieval failure may
leave only partial diagnostics. These files are not automatically expired: review
retention and disk usage, and treat them as sensitive operational records. The
snapshot covers the existing 15-minute window; it is not continuous log shipping
and cannot recover logs Docker has already removed.

The first immutable cutover has no previous digest yet. The operator wrapper
therefore records the healthy pre-cutover Git commit before checkout and runs
`deploy/remote-cutover.sh`. If that first digest fails before a release record
exists, the controller restores the recorded legacy checkout, recreates the
existing source-mounted server with `--no-build`, and requires health to return
before reporting the failed deployment. After the first successful cutover,
rollback is digest-to-digest through `deploy/deploy.sh` as normal.

The host checkout supplies only versioned Compose and deployment-control files;
`deploy/docker-compose.production.yml` removes every application build context
and `/app` bind mount. Runtime bytes therefore come only from the recorded
digest.

Then verify what users actually get, in a browser, from a workstation:

    python deploy/verify_production.py https://165-22-125-156.sslip.io

    # optionally walk signed-in pages too
    SMOKE_EMAIL=... SMOKE_PASSWORD=... python deploy/verify_production.py https://...

### Why both, and why the second one matters

`tests/smoke/` runs in CI against a seeded database and proves the **code** is
good. `deploy/verify_production.py` proves the **deployment** is good — that the
bytes being served behave in a browser.

Those are not the same thing. CI verifies SRI before building, and the
post-deployment browser check proves the exact image still behaves correctly in
the deployed topology.

### Topology

    https://165-22-125-156.sslip.io   ->  165.22.125.156  archie-proxy  (Caddy)
                                              |  reverse_proxy 10.106.0.6:5000
                                              v
                                          10.106.0.6      archie-oss    (the app)

The app host has no public address; reach it with
`ssh -J root@165.22.125.156 root@10.106.0.6`. The proxy's own key is not
authorised on the app host, so use ProxyJump from a workstation rather than
hopping manually.

### Registry access

The application host needs read-only GHCR package access. Authenticate once
with a narrowly scoped token and retain the current and previous images locally
so rollback remains available during a registry outage. Never prune those two
digests before the new release has passed its observation window.

## Backup restore drill

An untested backup is not a backup. `deploy/verify_backup.sh` runs on the
application host and proves the current backup reproduces the live database:

    ssh -J root@165.22.125.156 root@10.106.0.6 \
        'cd /root/archie-ea && ./deploy/verify_backup.sh'

It checks every archive decompresses, takes a fresh single-database dump,
restores it into a scratch database, compares row counts table by table against
live, and drops the scratch copy. **The live database is only ever read.**

Last run (4 Sep 2026): 789 tables restored, 0 row-count mismatches, 0 restore
errors; all 75 retained archives passed decompression checks.

Run it after any change to the schema or the backup mechanism, and on a schedule
if nothing else forces it — the failure mode is silent until an incident.

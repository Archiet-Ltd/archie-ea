#!/usr/bin/env python
"""Ask production whether it is actually working, after the deploy says it is.

Every gate in this repository runs BEFORE merge. `scripts/deploy.sh` then
verifies exactly one thing after shipping: that `/health` returns 200. Nothing
has ever checked that a page works, and nothing has ever run against production
on a schedule -- there is one CI workflow and it has no cron.

That is the gap this closes, and it is not theoretical. On 31 Aug 2026 the owner
opened /capability-maturity/search on the deployed site and got a page that
rendered correctly, returned HTTP 200, and said "This page could not load its
data". The cause was raw SQL naming a column that does not exist. An audit of
21,978 page loads had passed that page, because it checked status codes -- and
a page telling the user it is broken IS a 200.

So this checks the two things a status code cannot see:

1. **Does any page tell the user it is broken?** An error banner served with 200
   is the signature of a swallowed exception. This is the symptom check: it
   catches the whole family (bad SQL, dead API, failed fetch, missing column)
   without knowing the cause.

2. **Is production logging errors nobody reads?** Several failures found today
   were logged at DEBUG and vanished. They are now WARNING -- but nothing was
   listening to WARNING either. Counting them turns the log into a signal.

Anonymous by design. It signs in as nobody, so it only sees public and
login-gated-redirect surfaces. That is a real limit and is reported as one
rather than glossed: the deep authenticated pages are covered by
tests/smoke/test_no_error_banners.py against a seeded database, and this is the
production-side complement, not a replacement.

    python scripts/post_deploy_verify.py                        # the live site
    python scripts/post_deploy_verify.py --base https://host    # somewhere else
    python scripts/post_deploy_verify.py --logs                 # + container log scan
    python scripts/post_deploy_verify.py --json

Exit code is 1 when production is failing, so scripts/deploy.sh can roll back on
it. That is the point: a deploy that serves /health but 500s the dashboard is a
failed deploy, and until now it was a successful one.
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "https://entelim.org"
DEFAULT_DROPLET = "root@134.122.105.56"
DEFAULT_APP_DIR = "/root/archie-ea"

# Surfaces reachable without signing in. A redirect to the login page is a PASS
# -- it means the app is routing and the guard works.
PUBLIC_PATHS = [
    "/health",
    "/",
    "/account/login",
]

# Copy that means "this screen failed", as opposed to "there is nothing here".
# The distinction is the whole difficulty: an empty state is the product
# working. Sourced from the actual flash(..., "error") call sites and the
# load_error partials rather than invented.
BROKEN_COPY = [
    "could not load its data",
    "could not be run",
    "Please try again",
    "Something went wrong",
    "Internal Server Error",
    "Traceback (most recent call last)",
]

# Deliberately NOT treated as failure: these are healthy empty states.
EMPTY_STATE_COPY = [
    "No capabilities found",
    "Get started by",
    "no results",
]


def _fetch(url: str, timeout: int = 30):
    """Return (status, body). Never raises for an HTTP error status."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # tolerate whatever front-end cert is in place
    request = urllib.request.Request(url, headers={"User-Agent": "archie-post-deploy"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # network-level failure
        return 0, "CONNECTION FAILED: %s" % exc


def check_pages(base: str) -> list:
    problems = []
    for path in PUBLIC_PATHS:
        url = base.rstrip("/") + path
        status, body = _fetch(url)
        if status == 0:
            problems.append("%s -- unreachable: %s" % (url, body[:120]))
            continue
        if status >= 500:
            problems.append("%s -- HTTP %d" % (url, status))
            continue
        if status >= 400 and path != "/account/login":
            problems.append("%s -- HTTP %d" % (url, status))
            continue
        for phrase in BROKEN_COPY:
            if phrase.lower() in body.lower():
                # An empty state that happens to contain a listed phrase is not
                # a failure; require the phrase to appear WITHOUT empty-state
                # framing nearby.
                if any(ok.lower() in body.lower() for ok in EMPTY_STATE_COPY):
                    continue
                problems.append(
                    "%s -- HTTP %d but the page says %r" % (url, status, phrase)
                )
                break
    return problems


def check_logs(droplet: str, app_dir: str, minutes: int = 30) -> list:
    """Count real errors in the running container since the deploy.

    Not a pass/fail on every WARNING -- the app logs plenty legitimately. What
    matters is ERROR/CRITICAL and the specific swallowed-read warning, because
    those mean a user saw an empty screen and nobody was told.
    """
    command = (
        "cd %s && docker compose logs --since %dm server 2>/dev/null "
        "| grep -cE 'ERROR|CRITICAL|Traceback' || true" % (app_dir, minutes)
    )
    swallowed = (
        "cd %s && docker compose logs --since %dm server 2>/dev/null "
        "| grep -cE 'safe-query failed|Failed to enrich' || true" % (app_dir, minutes)
    )
    problems = []
    for label, cmd in (("errors", command), ("swallowed reads", swallowed)):
        proc = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=20", "-o", "StrictHostKeyChecking=no",
             droplet, cmd],
            capture_output=True, text=True, timeout=120,
        )
        digits = re.findall(r"\d+", proc.stdout or "")
        count = int(digits[-1]) if digits else 0
        if count:
            problems.append(
                "%d %s in the last %d minutes of container logs"
                % (count, label, minutes)
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--droplet", default=DEFAULT_DROPLET)
    parser.add_argument("--app-dir", default=DEFAULT_APP_DIR)
    parser.add_argument("--logs", action="store_true",
                        help="also scan the container logs over ssh")
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    problems = check_pages(args.base)
    if args.logs:
        try:
            problems += check_logs(args.droplet, args.app_dir, args.minutes)
        except Exception as exc:
            problems.append("could not read container logs: %s" % exc)

    if args.json:
        print(json.dumps({"base": args.base, "problems": problems,
                          "ok": not problems}, indent=2))
    else:
        if problems:
            print("PRODUCTION IS NOT HEALTHY:")
            for line in problems:
                print("  " + line)
            print()
            print("This is anonymous coverage only -- authenticated pages are")
            print("covered by tests/smoke/test_no_error_banners.py.")
        else:
            print("production OK: %d public surfaces served, none reporting an error"
                  % len(PUBLIC_PATHS))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

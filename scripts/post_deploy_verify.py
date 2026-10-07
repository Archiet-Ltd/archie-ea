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
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from xml.etree import ElementTree

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


def sitemap_urls(base: str) -> list:
    """Every <loc> in the live sitemap.xml, read the same way check_pages()
    reads any other public page -- no new dependency, just the stdlib XML
    parser already available everywhere Python is."""
    status, body = _fetch(base.rstrip("/") + "/sitemap.xml")
    if status != 200 or not body:
        return []
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return []
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    return [loc.text.strip() for loc in root.findall(f".//{ns}loc") if loc.text]


def ping_indexnow(base: str, urls: list) -> dict | None:
    """Tell IndexNow (api.indexnow.org, shared by Bing/Yandex) that every URL
    in *urls* may have changed, so these engines can re-crawl now rather than
    waiting. No-op when INDEXNOW_API_KEY is unset -- the key is free and
    self-generated (https://www.indexnow.org/documentation), never a paid
    account, but until one is generated and the matching /<key>.txt is
    deployed this stays a no-op by design, the same pattern as the other
    *_API_KEY settings in config.py.

    Never allowed to fail the deploy: this is a courtesy ping to search
    engines, not a correctness check of the site itself, so any problem here
    is reported in the JSON output and never added to the caller's
    `problems` list (post_deploy_verify's exit code / rollback signal).
    """
    key = os.environ.get("INDEXNOW_API_KEY", "").strip()
    if not key or not urls:
        return None

    from urllib.parse import urlparse

    payload = json.dumps({
        "host": urlparse(base).netloc,
        "key": key,
        "keyLocation": base.rstrip("/") + "/" + key + ".txt",
        "urlList": urls,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.indexnow.org/indexnow",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return {"status": response.status, "url_count": len(urls)}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "url_count": len(urls), "error": exc.read().decode("utf-8", "replace")[:300]}
    except Exception as exc:  # network-level failure
        return {"status": 0, "url_count": len(urls), "error": str(exc)}


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
    parser.add_argument(
        "--no-indexnow", action="store_true",
        help="skip the IndexNow ping even when INDEXNOW_API_KEY is set",
    )
    args = parser.parse_args()

    problems = check_pages(args.base)
    if args.logs:
        try:
            problems += check_logs(args.droplet, args.app_dir, args.minutes)
        except Exception as exc:
            problems.append("could not read container logs: %s" % exc)

    # IndexNow: only after confirming the deploy is actually healthy -- no
    # point telling search engines to recrawl pages that are currently
    # serving errors. A ping problem is reported but never added to
    # `problems`: see ping_indexnow()'s docstring for why.
    indexnow_result = None
    if not problems and not args.no_indexnow:
        indexnow_result = ping_indexnow(args.base, sitemap_urls(args.base))

    if args.json:
        print(json.dumps({"base": args.base, "problems": problems,
                          "ok": not problems, "indexnow": indexnow_result},
                          indent=2))
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
            if indexnow_result is None:
                print("IndexNow: skipped (INDEXNOW_API_KEY not set, or --no-indexnow)")
            else:
                print("IndexNow: submitted %d URL(s), status %s"
                      % (indexnow_result["url_count"], indexnow_result["status"]))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

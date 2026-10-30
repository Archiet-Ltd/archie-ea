"""Wave 1 page list and personas for the response-time run (T-perf01).

One list of routes, shared by the locust scenarios in ``locustfile.py`` and by the tests that guard the
fixes. Each entry names the page id from the wave plan, the path (with ``{placeholders}`` filled from the
ids file), and who is expected to open it:

* ``public``  - anyone, signed in or not
* ``member``  - any signed-in person of the organisation
* ``admin``   - organisation administrators only

The run records what each persona actually received (status, final URL), so a page a persona cannot open is
reported as such and left out of that persona's timings instead of being timed as an error page.

Results hold routes and timings only. No names, no addresses.
"""
from __future__ import annotations

import json
import os
import re
import time

PERSONAS = ("anonymous", "cto", "viewer", "admin")

# (wave-plan id, path template, access)
W1_ROUTES = [
    # Sign-in and account
    ("PG-005", "/account/login", "public"),
    ("NP-01", "/account/register", "public"),
    ("NP-02", "/account/unconfirmed", "member"),
    ("NP-03", "/account/reset-password", "public"),
    ("NP-04", "/account/join/{join_token}", "public"),
    ("NP-10", "/account/mfa-enroll", "member"),
    ("PG-008", "/account/mfa-challenge", "member"),
    ("PG-006", "/account/manage", "member"),
    ("PG-007", "/account/manage/info", "member"),
    ("PG-003", "/account/manage/change-email", "member"),
    ("PG-004", "/account/manage/change-password", "member"),
    # Overview, health, settings, status
    ("PG-166", "/dashboard/overview", "member"),
    ("PG-165", "/dashboard/health", "member"),
    ("PG-315", "/settings", "member"),
    ("PG-358", "/status", "member"),
    # Applications
    ("PG-418", "/applications/", "member"),
    ("PG-419", "/applications/ownership-coverage", "member"),
    ("PG-417", "/applications/{app_id}/fact-sheet", "member"),
    ("PG-416", "/applications/{app_id}/edit", "member"),
    ("PG-059", "/dashboard/applications/create", "member"),
    ("PG-330", "/my-applications/", "member"),
    ("PG-326", "/modules/", "member"),
    # Import
    ("PG-134", "/batch-import/", "member"),
    ("PG-136", "/batch-import/new", "member"),
    ("PG-135", "/batch-import/jobs/{job_id}", "member"),
    ("PG-133", "/batch-import/batches/{batch_id}/review", "member"),
    # AI chat
    ("PG-412", "/ai-chat", "member"),
    ("PG-413", "/ai-chat/", "member"),
    ("PG-410", "/ai-chat/approvals/inbox", "member"),
    ("PG-415", "/ai-chat/run-records", "member"),
    ("PG-414", "/ai-chat/run-records/{run_record_id}", "member"),
    # Administration
    ("PG-030", "/admin/", "admin"),
    ("PG-038", "/admin/users", "admin"),
    ("PG-031", "/admin/invite-user", "admin"),
    ("PG-033", "/admin/new-user", "admin"),
    ("PG-018", "/admin/audit-log", "admin"),
    ("PG-137", "/admin/billing/", "admin"),
    ("PG-279", "/admin/connectors", "admin"),
    ("NP-11", "/admin/connectors/github", "admin"),
    ("NP-12", "/admin/connectors/github/review", "admin"),
    ("PG-047", "/admin/user/{other_user_id}", "admin"),
    ("PG-048", "/admin/user/{other_user_id}/info", "admin"),
    ("PG-019", "/admin/user/{other_user_id}/change-account-type", "admin"),
    ("PG-020", "/admin/user/{other_user_id}/change-email", "admin"),
    ("PG-024", "/admin/user/{other_user_id}/delete", "admin"),
    ("PG-042", "/admin/user/{other_user_id}/set-password", "admin"),
    ("PG-467", "/admin/user/{other_user_id}/role", "admin"),
    # Public site
    ("PG-303", "/", "public"),
    ("PG-288", "/demo/lantern-quay", "public"),
    ("PG-305", "/vs", "public"),
    ("PG-304", "/vs/{vs_slug}", "public"),
    ("PG-306", "/connectors/{connector_slug}", "public"),
    ("PG-307", "/how-archiet-runs-on-entelim", "public"),
    ("PG-308", "/{legal_slug}", "public"),
    ("PG-309", "/modules/{module_slug}", "member"),
    ("PG-310", "/roles/{role_slug}", "public"),
    ("PG-311", "/{site_slug}", "public"),
    ("PG-311b", "/pricing", "public"),
    ("PG-312", "/use-cases/{use_case_slug}", "public"),
    ("PG-313", "/use-cases", "public"),
    ("PG-314", "/vision", "public"),
]

# Pages the wave plan lists that have no GET path of their own on this branch (they are states of another
# page, a redirect target, or a payment-provider return). They are named in the report, not timed.
NO_OWN_ROUTE = {
    "NP-05": "reading progress and company profile confirm: a step inside the sign-up flow",
    "NP-06": "start the sample company: a POST action",
    "NP-07": "plan checkout and payment result: provider redirect",
    "NP-08": "cancel plan: provider portal",
    "NP-09": "shared error screens: rendered in place on any failing page",
}

DEFAULT_IDS = {
    "app_id": 1, "job_id": 1, "batch_id": 1, "run_record_id": 1, "other_user_id": 1,
    "join_token": "not-a-real-token", "legal_slug": "data-processing-agreement",
    "site_slug": "about", "vs_slug": "ardoq", "connector_slug": "ai-assistants",
    "module_slug": "applications", "role_slug": "cto-and-cio",
    "use_case_slug": "architecture-review-board",
}


def load_ids():
    """Ids for the placeholders: defaults, then the JSON file named by ARCHIE_W1_IDS."""
    ids = dict(DEFAULT_IDS)
    path = os.environ.get("ARCHIE_W1_IDS")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            ids.update(json.load(fh))
    return ids


def resolve(ids=None):
    """[(page_id, concrete_path, access, route_name)] with placeholders filled.

    ``route_name`` keeps the template, so every id of one kind groups into one line of statistics.
    """
    ids = ids or load_ids()
    return [(pid, template.format(**ids), access, template) for pid, template, access in W1_ROUTES]


def can_open(persona, access):
    if access == "public":
        return True
    if persona == "anonymous":
        return False
    if access == "admin":
        return persona == "admin"
    return True


def pick_slug(html, prefix, fallback):
    """First link under ``prefix`` found on an index page."""
    match = re.search(r'href="%s/([a-z0-9][a-z0-9-]*)"' % re.escape(prefix), html or "")
    return match.group(1) if match else fallback


def percentile(sorted_values, q):
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def summarise(samples):
    """samples: [(persona, page_id, route_name, status, final_path, ms)] -> list of result rows."""
    rows = {}
    for persona, page_id, route, status, final_path, ms in samples:
        rows.setdefault((persona, page_id, route), []).append((status, final_path, ms))
    out = []
    for (persona, page_id, route), items in sorted(rows.items()):
        opened = [ms for status, _final, ms in items if 200 <= status < 300]
        statuses = {}
        for status, _final, _ms in items:
            statuses[str(status)] = statuses.get(str(status), 0) + 1
        opened.sort()
        out.append({
            "persona": persona, "page": page_id, "route": route, "requests": len(items),
            "opened": len(opened), "statuses": statuses,
            "p50_ms": percentile(opened, 0.5), "p95_ms": percentile(opened, 0.95),
            "max_ms": opened[-1] if opened else None,
        })
    return out


def write_results(samples, path, meta=None):
    payload = {"recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "meta": meta or {},
               "rows": summarise(samples)}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    return payload

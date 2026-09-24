"""S-11 (discoverability wave, 18 Aug 2026) — a module is not "done" until it
can be found.

The register's finding: /modules/ (now the All-modules directory) listed 55
real, working module roots while the sidebar exposed roughly 30 — 25
substantial modules returned HTTP 200 with real content and were reachable
from nowhere a user or evaluator would look. A prior pass (commit 055cec4)
fixed part of this and explicitly logged that it had NOT audited the full
/modules/ estate against navigation; this file is that audit, made
self-enforcing.

The list of module roots below is derived DYNAMICALLY from
`app.url_map` every run — not hand-maintained — precisely so a future
module that ships without a sidebar link or a modules_directory entry fails
this test automatically instead of silently repeating S-11 a fourth time.
"Module root" here means: a GET route with no required URL arguments, not
under /api/, with exactly one path segment (e.g. /procurement/, not
/procurement/contracts/<id>/edit) — the same shape as every route named in
the QA register's list of 25.

Pure introspection against `app.url_map` and `app.utils.role_access` /
`app.modules.modules_directory.routes` — no requests, no fixtures that
register routes at test time (the `app` fixture is session-scoped; this
respects that by only ever reading its already-built url_map).
"""

from __future__ import annotations

import re

import pytest

# Infra/meta routes that are never a "module" a persona would look for in
# navigation, and redirect endpoints (endpoint name contains "redirect") that
# forward to an already-covered canonical page — both excluded by rule, not
# by name, so a new redirect or infra route doesn't need this file touched.
_INFRA_RULES = {
    "/favicon.ico",
    "/robots.txt",
    "/sitemap.xml",
    "/apidocs/",
    "/apispec.json",
    "/oauth2-redirect.html",
    "/health",
    "/version",
    "/login",
    "/settings",  # account settings, not a module — already reachable via user menu
    "/modules/",  # the directory page itself
}

_SINGLE_SEGMENT = re.compile(r"^/[^/]+/?$")


def _module_root_rules(app):
    """Every zero-argument GET route shaped like a module root."""
    roots = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or rule.arguments:
            continue
        if "GET" not in rule.methods:
            continue
        if rule.rule.startswith("/api/"):
            continue
        if rule.rule in _INFRA_RULES:
            continue
        if "redirect" in rule.endpoint.lower():
            continue
        if not _SINGLE_SEGMENT.match(rule.rule):
            continue
        roots.append(rule)
    return roots


def _all_linked_endpoints(app):
    from app.modules.modules_directory.routes import all_module_links

    with app.app_context():
        return {link["endpoint"] for link in all_module_links()}


# The 25 module roots the S-11 register named explicitly (ARCHIEQAUpdate6
# HiddenModules.md section 1), resolved to endpoints at test time via
# url_map rather than pinned as literal strings, so a route rename is
# caught here rather than silently going stale.
_S11_REGISTER_RULES = [
    "/adm-kanban/",
    "/architecture-journey/",
    "/procurement/",
    "/procurement/compliance",
    "/procurement/contracts",
    "/procurement/licenses",
    "/procurement/renewals",
    "/procurement/spend",
    "/architecture/investment-priorities",
    "/my-applications/",
    "/my-applications/list",
    "/my-applications/health",
    "/my-applications/roadmap",
    "/solutions/programmes",
    "/applications/rationalization",
    "/capability-roadmap",
    "/architecture/traceability",
    "/value-streams/",
    "/stakeholders/map",
    "/strategic/capability-health",
    "/strategic/impact-analysis",
    "/capability-maturity/frameworks",
    "/batch-import/",
    "/consolidation-list/",
    "/duplicate-detection/simple",
]


def test_s11_register_routes_still_exist(app):
    """Regression guard: none of the 25 routes named in the register have
    been removed or renamed out from under this audit."""
    rules_by_path = {r.rule: r for r in app.url_map.iter_rules()}
    missing = [p for p in _S11_REGISTER_RULES if p not in rules_by_path]
    assert not missing, f"S-11 register routes no longer exist: {missing}"


def test_s11_register_routes_are_discoverable(app):
    """Every route the register named is genuinely reachable from at least
    one role's rendered sidebar or the /modules All-modules directory's
    rendered rows -- not merely known to `all_module_links()`, which also
    carries every `_DARK` entry so `test_no_orphan_module_root` below does
    not report each one a brand-new orphan. That looser set is the wrong
    measure here: it would let a route go quietly unreachable (a real S-11
    regression) as long as `_MORE_TOOLS` still names it.

    A path can carry more than one rule (e.g. GET list + POST create on the
    same URL) — reachable if ANY GET-serving endpoint on that exact path is
    genuinely linked, not just whichever rule url_map happens to expose
    first. A route whose only matching rules are all in `_DARK` is not
    counted as unreachable either: that is a written, deliberate product
    decision to stop advertising it, not an accidental navigation gap.
    """
    from app.modules.modules_directory.routes import _DARK

    rules_by_path: dict[str, list] = {}
    for r in app.url_map.iter_rules():
        if "GET" in r.methods:
            rules_by_path.setdefault(r.rule, []).append(r)
    linked = _all_linked_endpoints(app) - set(_DARK)

    unreachable = []
    for path in _S11_REGISTER_RULES:
        rules = rules_by_path.get(path)
        if not rules:
            continue  # covered, and failed, by the existence test above
        endpoints = {rule.endpoint for rule in rules}
        if endpoints & linked:
            continue
        if endpoints & set(_DARK):
            continue  # a deliberately dark page, not an S-11-style regression
        unreachable.append((path, sorted(endpoints)))

    assert not unreachable, (
        "S-11 register routes with no sidebar link and no /modules directory "
        f"entry: {unreachable}"
    )


def test_no_orphan_module_root(app):
    """The general form of the S-11 defect: ANY zero-argument, single-segment
    GET route — not just the 25 named in the register — must be reachable
    from a persona's sidebar or the All-modules directory. This is what
    makes the property self-enforcing: a new module shipped without a nav
    entry or a modules_directory row fails here without anyone updating this
    file first.
    """
    linked = _all_linked_endpoints(app)
    orphans = [
        (rule.rule, rule.endpoint)
        for rule in _module_root_rules(app)
        if rule.endpoint not in linked
    ]
    assert not orphans, (
        "Module roots with no sidebar link and no /modules directory entry "
        f"(S-11): {orphans}. Add each either to a persona's SIDEBAR_ZONES in "
        "app/utils/role_access.py, or to _MORE_TOOLS in "
        "app/modules/modules_directory/routes.py if it has no natural "
        "persona owner yet."
    )


def test_all_module_links_are_guarded_endpoints(app):
    """all_module_links() must only ever list endpoints that really exist —
    a typo'd endpoint string here would BuildError every render of
    admin_sidebar.html or the /modules page, since neither guards with
    anything stronger than membership in view_functions."""
    from app.modules.modules_directory.routes import all_module_links

    with app.app_context():
        links = all_module_links()
    assert links, "all_module_links() returned nothing — nav bootstrap likely broken"
    unknown = [
        link["endpoint"] for link in links if link["endpoint"] not in app.view_functions
    ]
    assert not unknown, f"all_module_links() references unregistered endpoints: {unknown}"

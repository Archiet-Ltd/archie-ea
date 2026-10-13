"""WCAG 2.2 AA audit with axe-core, on the pages each archetype actually uses.

This engagement already closed 939 unlabelled form controls, which was the single
largest defect class in the product. That work was necessary and it is not
sufficient: "every input has a name" is one success criterion out of dozens, and
an EN 301 549 auditor tests the rest too - contrast, landmarks, heading order,
ARIA validity, focus order, link purpose.

axe-core is what those auditors run. It detects roughly a third of WCAG issues
automatically, so a clean result is a floor rather than a certificate - it means
nothing mechanical is wrong, not that the product is usable with a screen reader.
Manual testing is still owed, and this audit does not replace it.

The level is WCAG 2.2 AA as a build-and-test standard, not a conformance claim.
2.2 adds to 2.1 rather than replacing it, so the 2.1 tags stay. Of what 2.2 adds,
axe-core automates exactly one thing: the `target-size` rule (2.5.8, interactive
targets of at least 24 by 24 CSS pixels), carried by the `wcag22aa` tag. Focus Not
Obscured, Dragging Movements, Consistent Help, Redundant Entry and Accessible
Authentication have no axe rule and remain manual.

That is why a tag set that says 2.2 is not enough on its own. Naming `wcag22aa`
against an axe-core that predates the rule selects nothing and reports "no 2.2
violations" for a check that never ran. So this module reads the bundled axe-core
version at run time and prints it, and fails if `target-size` is not in the
loaded rule list. Once a browser is available it likewise FAILS, rather than
skips, when axe-playwright-python is not installed (with no browser the whole
tier skips, as tests/smoke/conftest.py arranges): a skipped test reports as
passed, which would let a CI run say "no accessibility violations" when no
accessibility check ran at all.

A clean `target-size` result is narrower than it sounds. The rule also passes a
target smaller than 24 by 24 pixels when enough clear space surrounds it, and
every audited page carries such targets (small links measuring 32 by 16, for
instance), so "clean" does not mean every target is 24 by 24. The audited tenant
also holds only a handful of records, so long lists and busy states are not
exercised. Both are left to manual review.

Gated as a ratchet against an accepted baseline, like the bandit and dependency
gates, for the same reason: a check that fails on day one gets disabled on day
two. Serious violations fail the build immediately; everything else may only
decrease.
"""

import ast
import datetime
import importlib
import importlib.metadata
import json
import os
import re
import uuid

import pytest

from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .intelligence_graph import mark_derived_stale, seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# The library is imported lazily, inside the `axe_module` fixture below, and a
# missing package FAILS there. It used to be `pytest.importorskip(...)` at import
# time, which reports as a pass: Playwright present, browser present, server
# booted, and the audit silently absent. There is deliberately no opt-out
# environment variable - an escape hatch is one more way to reproduce that defect
# under a new name, and `pip install -r requirements.txt` is the whole fix.
AXE_PACKAGE = "axe-playwright-python"
AXE_MODULE = "axe_playwright_python.sync_playwright"

BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "a11y_baseline.json")

SMOKE_DIR = os.path.dirname(os.path.abspath(__file__))
THIS_MODULE = os.path.splitext(os.path.basename(__file__))[0]

# WCAG 2.2 Level AA, with Level A - the level the product's design targets, as a
# build-and-test standard rather than a conformance claim. 2.2 includes 2.1, so
# the four 2.1 tags stay and `wcag22aa` is added. EN 301 549 v4.1.1 adopts 2.2 but
# is not yet the legal reference (v3.2.1 remains so until v4.1.1 is cited in the
# Official Journal of the European Union): that is context, not the reason for
# the move.
#
# Three files run axe with this tag set: this module, the ARB governance journey
# (which keeps its own copy of the list) and the transformation room journeys
# (which import TAGS). The last two assert on serious and critical findings
# directly, with no baseline, so changing this list changes what they enforce as
# well. test_every_axe_run_in_tests_smoke_uses_the_audit_tag_set keeps every axe
# run under tests/smoke on exactly this list.
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]

# What the `wcag22aa` tag exists to add: rule id -> the tag it must carry. A rule
# set without these makes a clean 2.2 result meaningless, so the audit refuses to
# draw any conclusion from one.
REQUIRED_RULES = {"target-size": "wcag22aa"}

# The four places axe reports a rule it ran; a rule in none of them did not run.
RESULT_KINDS = ("violations", "passes", "incomplete", "inapplicable")

# Every accepted baseline entry must carry a dated one-line reason. The date must
# be a real date and not in the future; the reason must be at least ten
# characters and contain letters, which rejects placeholders such as "...". The
# wording of the reason is not judged beyond that.
NOTE_FORMAT = re.compile(r"^(\d{4}-\d{2}-\d{2}): (\S.{9,})$")
BASELINE_COMMENT = (
    "Accepted axe-core violations per page under the WCAG 2.2 AA tag set "
    "(wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa). An entry here is a real, "
    "unfixed failure against that tag set, not a statement that the page is "
    "accessible. Each entry must be explained by a dated one-line reason under "
    "accepted_notes (page, then rule: 'YYYY-MM-DD: reason'); the test suite "
    "rejects an entry without one. Regenerate with SMOKE_A11Y_UPDATE_BASELINE=1.")

# One representative page per archetype, signed in as that archetype.
#
# NOTE FOR THE FIRST RUN AFTER /ai-chat WAS ADDED: that page had no entry in
# a11y_baseline.json, because it was in no smoke test at all until now. The
# first run will therefore FAIL with its real violations — that failure is the
# point, and is what proves the gate now sees the page. Record them with:
#
#     SMOKE_A11Y_UPDATE_BASELINE=1 pytest tests/smoke/test_accessibility_audit.py
#
# Then read the diff. Every accepted rule under "/ai-chat" is a known defect
# scheduled for removal in the client rebuild (see
# docs/superpowers/specs/2026-08-07-ai-chat-rebuild-design.md §9), NOT an
# accepted state. Expect at minimum three `select-name` criticals: the domain,
# model and template selects carry no label.
AUDIT = [
    ("procurement", "/procurement/contracts"),
    ("procurement", "/procurement/contracts/new"),
    ("procurement", "/procurement/compliance"),
    ("application_manager", "/my-applications/"),
    ("portfolio_manager", "/applications/"),
    ("business_architect", "/capability-map/"),
    ("cto", "/dashboard/overview"),
    ("enterprise_architect", "/ai-chat"),
    # Ask and Twin map join the list at zero accepted violations. They are never
    # baselined: a violation on either is fixed, not recorded. Their states with data
    # and with the provenance drawer open are audited separately, below.
    ("solution_architect", "/intelligence/ask"),
    ("enterprise_architect", "/intelligence/twin-map"),
]

# The states of the two new surfaces that a plain page load cannot reach: the answer
# on Ask, the map with its table on Twin map, and the provenance drawer open (with
# "Full detail" expanded, because that is where the technical detail lives) over each.
INTELLIGENCE_STATES = [
    ("solution_architect", "Ask, with an answer", "ask"),
    ("solution_architect", "Ask, provenance drawer open", "ask-drawer"),
    ("enterprise_architect", "Twin map, with the map and its table", "map"),
    ("enterprise_architect", "Twin map, provenance drawer open", "map-drawer"),
    # The same again for a model whose worked-out connection has gone out of date: the
    # notice and its button, the marked rows and badge, and the drawer over a stale row.
    ("solution_architect", "Ask, out-of-date model", "ask-stale"),
    ("solution_architect", "Ask, out-of-date model, provenance drawer open", "ask-stale-drawer"),
    ("enterprise_architect", "Twin map, out-of-date model", "map-stale"),
    ("enterprise_architect", "Twin map, out-of-date model, provenance drawer open", "map-stale-drawer"),
]

# An impact level at which a violation is not negotiable: these block a user
# rather than inconvenience them.
BLOCKING = {"critical", "serious"}


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _load_baseline_file():
    if not os.path.exists(BASELINE):
        return {}
    with open(BASELINE, encoding="utf-8") as fh:
        return json.load(fh)


def _load_baseline():
    return _load_baseline_file().get("accepted", {})


def _violation_evidence(violation):
    """Keep enough axe evidence to make a CI-only violation repairable."""
    nodes = violation.get("nodes") or []
    targets = []
    for node in nodes[:8]:
        target = node.get("target") or []
        targets.append(" > ".join(str(part) for part in target))
    return {
        "impact": violation.get("impact") or "unknown",
        "count": len(nodes),
        "targets": targets,
    }


class _AuditResults(dict):
    """path -> {rule id: evidence}, plus what the run can prove about itself.

    A plain dict to every test that only reads violations. `evaluated` maps each
    path to the ids of every rule axe reported on it (violations, passes,
    incomplete or inapplicable), `activity` says, for each required rule, how many
    elements it put in each of those four buckets, and `engine_versions` holds the
    axe-core version each run reported - so "clean" can be told apart from "did
    not check".
    """

    def __init__(self):
        super().__init__()
        self.evaluated = {}
        self.activity = {}
        self.engine_versions = set()


@pytest.fixture(scope="module")
def axe_module(browser):
    """The axe-playwright-python module, or a hard failure - never a skip.

    Depends on `browser` on purpose: the tier's own skip (Playwright or a browser
    unavailable, see conftest.py) must keep winning on a developer machine. This
    fixture only runs once a real browser exists, which is exactly the situation
    where a missing audit library is a defect rather than an environment gap.
    """
    try:
        return importlib.import_module(AXE_MODULE)
    except ImportError as exc:
        pytest.fail(
            "The accessibility audit could not run: the %s package is not "
            "installed (%s).\n"
            "This fails rather than skips because a skipped test reports as "
            "passed, and a CI run could then say 'no accessibility violations' "
            "when no accessibility check ran at all.\n"
            "Install it with:  pip install -r requirements.txt"
            % (AXE_PACKAGE, exc),
            pytrace=False)


@pytest.fixture(scope="module")
def axe_engine(axe_module, browser, live_server):
    """What the installed library actually carries, read at run time and printed.

    Injects the bundled script into a real page and asks it, rather than trusting
    a version string in a requirements file: the pin says what was asked for, this
    says what is running.
    """
    ctx = browser.new_context()
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.evaluate(axe_module.Axe().axe_script)
        version = page.evaluate("axe.version")
        rules = page.evaluate("axe.getRules().map(r => ({id: r.ruleId, tags: r.tags}))")
    finally:
        ctx.close()
    try:
        package_version = importlib.metadata.version(AXE_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        package_version = "unknown"
    engine = {
        "axe_core": version,
        "package": package_version,
        "rules": {r["id"]: list(r["tags"]) for r in rules},
    }
    print("\n[a11y] %s %s bundles axe-core %s; %d rules loaded; tags audited: %s"
          % (AXE_PACKAGE, package_version, version, len(engine["rules"]), ", ".join(TAGS)))
    for rule in sorted(REQUIRED_RULES):
        loaded = engine["rules"].get(rule)
        print("[a11y] required rule %s: %s"
              % (rule, "loaded, tags %s" % ",".join(loaded) if loaded else "ABSENT"))
    return engine


def _rule_set_problems(engine):
    """Why this axe-core cannot check WCAG 2.2, or [] if it can."""
    problems = []
    for rule, tag in sorted(REQUIRED_RULES.items()):
        tags = engine["rules"].get(rule)
        if tags is None:
            problems.append(
                "the bundled axe-core %s predates the %r rule: it is absent from the "
                "loaded rule list, so the %s tag would select nothing and a clean "
                "result would say nothing about WCAG 2.2 (a false green). Raise the "
                "%s pin in requirements.txt to a release that bundles a newer "
                "axe-core." % (engine["axe_core"], rule, tag, AXE_PACKAGE))
        elif tag not in tags:
            problems.append(
                "the %r rule is loaded (axe-core %s) but is not tagged %s (its tags "
                "are %s), so the audit's tag set would not select it."
                % (rule, engine["axe_core"], tag, ", ".join(tags)))
    return problems


def _require_rule_set(engine):
    problems = _rule_set_problems(engine)
    if problems:
        pytest.fail("The WCAG 2.2 rule set is not loaded:\n  %s" % "\n  ".join(problems),
                    pytrace=False)


@pytest.fixture(scope="module")
def audited(axe_module, axe_engine, browser, live_server, seeded):
    """Run axe once per page and retain counts plus actionable node selectors.

    The rule-set assertion runs first, here, so that no test below can read a
    clean result out of an audit whose rule set could not have produced a 2.2
    finding.
    """
    _require_rule_set(axe_engine)
    Axe = axe_module.Axe
    axe = Axe()
    results = _AuditResults()
    for archetype, path in AUDIT:
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.set_default_timeout(PAGE_TIMEOUT)
        ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
        page = ctx.new_page()
        try:
            _login(page, live_server, seeded["emails"][archetype])
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(2000)
            try:
                # The first-run overlay covers the viewport and would be audited
                # instead of the page underneath it.
                page.eval_on_selector_all(
                    "[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
            except Exception:
                pass
            report = axe.run(page, options={"runOnly": {"type": "tag", "values": TAGS}})
            data = report.response if hasattr(report, "response") else report
            results[path] = {
                v["id"]: _violation_evidence(v)
                for v in data.get("violations", [])
            }
            results.evaluated[path] = {
                r["id"]
                for kind in RESULT_KINDS
                for r in data.get(kind, [])
            }
            results.activity[path] = {
                rule: {kind: sum(len(r.get("nodes") or []) for r in data.get(kind, [])
                                 if r["id"] == rule)
                       for kind in RESULT_KINDS}
                for rule in REQUIRED_RULES
            }
            results.engine_versions.add((data.get("testEngine") or {}).get("version"))
        finally:
            ctx.close()
    return results


def _wait_for_component(page, factory):
    page.wait_for_function(
        "(f) => { const el = document.querySelector('[x-data=\"' + f + '()\"]');"
        " return !!(el && el._x_dataStack); }",
        arg=factory,
    )


def _open_question(page):
    page.click("#ask-question-impact")
    expect(page.locator("#ask-picker-input")).to_be_focused()


def _reach_intelligence_state(page, base, graph, kind):
    if kind.startswith("ask"):
        page.goto(base + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_for_component(page, "askSurface")
        _open_question(page)
        page.press("#ask-picker-input", "Control+a")
        page.locator("#ask-picker-input").press_sequentially(graph["noun"], delay=15)
        page.wait_for_selector("#ask-picker-listbox [role=option]")
        page.locator("#ask-picker-listbox [role=option]",
                     has_text=graph["names"]["service"]).click()
        page.wait_for_selector("[data-ask-row]")
        opener = page.locator("[data-ask-row][data-kind=derived]").get_by_role("button", name="Why?")
    else:
        page.goto(base + "/intelligence/twin-map?element=%s" % graph["service"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_for_component(page, "twinMapSurface")
        page.wait_for_selector("svg .intel-edge", state="attached")
        page.wait_for_selector("[data-graph-nodes] button", state="visible")
        opener = page.locator("[data-map-row][data-kind=derived]").get_by_role("button", name="Why?")
    if kind.endswith("-drawer"):
        opener.click()
        dialog = page.locator("#drawer-provenance [role=dialog]")
        dialog.wait_for(state="visible")
        dialog.locator("[data-full-detail-toggle]").click()
        dialog.locator("[data-full-detail-region]").wait_for(state="visible")
    # Let transitions settle so contrast is read from the final colours.
    page.wait_for_timeout(600)


@pytest.fixture(scope="module")
def audited_intelligence_states(axe_module, axe_engine, browser, live_server, seeded):
    """Run axe on the states of Ask and Twin map that need data or a click to reach."""
    _require_rule_set(axe_engine)
    graph = seed_impact_graph(seeded["ids"]["org"], "Auditpay")
    stale_graph = seed_impact_graph(seeded["ids"]["org"], "Auditstale")
    assert mark_derived_stale(stale_graph)["stale"] is True
    axe = axe_module.Axe()
    results = _AuditResults()
    for archetype, label, kind in INTELLIGENCE_STATES:
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.set_default_timeout(PAGE_TIMEOUT)
        ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
        page = ctx.new_page()
        try:
            _login(page, live_server, seeded["emails"][archetype])
            _reach_intelligence_state(
                page, live_server, stale_graph if "stale" in kind else graph, kind)
            report = axe.run(page, options={"runOnly": {"type": "tag", "values": TAGS}})
            data = report.response if hasattr(report, "response") else report
            results[label] = {
                v["id"]: _violation_evidence(v) for v in data.get("violations", [])
            }
            results.evaluated[label] = {
                r["id"] for kind_name in RESULT_KINDS for r in data.get(kind_name, [])
            }
        finally:
            ctx.close()
    return results


# The two canvas pages, each at 1280px and 360px, for an empty tenant (no
# record has been filled in yet, so every box renders its empty hint rather
# than content). Baselined like every other page in AUDIT above (a ratchet,
# not a hard zero) rather than joining Ask/Twin map's never-baselined pair,
# because these pages' existing macros (page_header, card, the meta panel)
# are shared with pages already carrying accepted findings and a first run
# here may find the same ones, not a new defect this change introduced.
CANVAS_PAGES = [
    ("business_architect", "Business Model Canvas, 1280px", "bmc", 1280),
    ("business_architect", "Business Model Canvas, 360px", "bmc", 360),
    ("business_architect", "Business case, 1280px", "case", 1280),
    ("business_architect", "Business case, 360px", "case", 360),
]


@pytest.fixture(scope="module")
def canvas_records(seeded):
    """One empty BusinessModelCanvas and one empty BusinessCase for the
    seeded org — the pages under audit 404 without a real row."""
    from app import create_app, db
    from app.models.business_case import BusinessCase
    from app.models.business_model import BusinessModelCanvas

    app = create_app("testing")
    with app.app_context():
        canvas = BusinessModelCanvas(name="A11y Audit Canvas", organization_id=seeded["ids"]["org"])
        case = BusinessCase(title="A11y Audit Case", organization_id=seeded["ids"]["org"])
        db.session.add_all([canvas, case])
        db.session.commit()
        return {"bmc": canvas.id, "case": case.id}


@pytest.fixture(scope="module")
def audited_canvas_pages(axe_module, axe_engine, browser, live_server, seeded, canvas_records):
    """Run axe on both canvas pages at 1280px and 360px."""
    _require_rule_set(axe_engine)
    axe = axe_module.Axe()
    results = _AuditResults()
    paths = {
        "bmc": "/business-model/%d" % canvas_records["bmc"],
        "case": "/business-case/%d" % canvas_records["case"],
    }
    for archetype, label, kind, width in CANVAS_PAGES:
        ctx = browser.new_context(viewport={"width": width, "height": 900})
        ctx.set_default_timeout(PAGE_TIMEOUT)
        ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
        page = ctx.new_page()
        try:
            _login(page, live_server, seeded["emails"][archetype])
            page.goto(live_server + paths[kind], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(1000)
            report = axe.run(page, options={"runOnly": {"type": "tag", "values": TAGS}})
            data = report.response if hasattr(report, "response") else report
            results[label] = {
                v["id"]: _violation_evidence(v) for v in data.get("violations", [])
            }
            results.evaluated[label] = {
                r["id"] for kind_name in RESULT_KINDS for r in data.get(kind_name, [])
            }
        finally:
            ctx.close()
    return results


def test_canvas_pages_are_audited_at_both_widths(audited_canvas_pages):
    assert set(audited_canvas_pages) == {label for _a, label, _k, _w in CANVAS_PAGES}


def test_canvas_pages_have_no_new_serious_or_critical_violations(audited_canvas_pages):
    """Same ratchet as test_no_new_serious_or_critical_violations, scoped to
    the two pages and widths this task adds."""
    baseline = _load_baseline()
    regressions = []
    for label, violations in sorted(audited_canvas_pages.items()):
        known = baseline.get(label, {})
        for rule, evidence in sorted(violations.items()):
            if evidence["impact"] not in BLOCKING:
                continue
            was = known.get(rule)
            if was is None:
                regressions.append("%s: NEW %s (%s, %d element%s; targets: %s)"
                                   % (label, rule, evidence["impact"], evidence["count"],
                                      "" if evidence["count"] == 1 else "s",
                                      ", ".join(evidence["targets"]) or "unavailable"))
            elif evidence["count"] > was:
                regressions.append("%s: %s worsened %d -> %d elements"
                                   % (label, rule, was, evidence["count"]))
    assert not regressions, (
        "%d new or worsened serious/critical WCAG 2.2 AA violation(s) on the canvas "
        "pages:\n  %s" % (len(regressions), "\n  ".join(regressions)))


def test_canvas_pages_all_violations_do_not_increase(audited_canvas_pages):
    baseline = _load_baseline()
    regressions = []
    for label, violations in sorted(audited_canvas_pages.items()):
        known = baseline.get(label, {})
        for rule, evidence in sorted(violations.items()):
            was = known.get(rule)
            if was is None:
                regressions.append("%s: NEW %s (%s, %d)"
                                   % (label, rule, evidence["impact"], evidence["count"]))
            elif evidence["count"] > was:
                regressions.append("%s: %s worsened %d -> %d" % (label, rule, was, evidence["count"]))
    assert not regressions, (
        "%d accessibility regression(s) on the canvas pages:\n  %s"
        % (len(regressions), "\n  ".join(regressions)))


def test_canvas_pages_target_size_rule_was_evaluated(audited_canvas_pages):
    """A clean result only means something if the 2.2 rule actually ran."""
    for label in audited_canvas_pages:
        assert "target-size" in audited_canvas_pages.evaluated[label], (
            "the target-size rule did not run on %r" % label)


def _dict_value(node, key):
    """The value expression stored under the string constant `key` in a dict literal."""
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and k.value == key:
            return v
    return None


def _imports_the_audit_module(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == THIS_MODULE:
                return True
            if any(a.name == THIS_MODULE for a in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(a.name.split(".")[-1] == THIS_MODULE for a in node.names):
                return True
    return False


def _candidate_tag_lists(expr, tree):
    """The literal tag lists an axe `values` expression can hold, or None if unreadable.

    Reads a list literal, this module's own TAGS (imported, or reached through the
    imported module), or a plain variable that is assigned list literals in the
    same file. Anything else is unreadable, and the caller treats that as a failure.
    """
    if isinstance(expr, (ast.List, ast.Tuple)):
        if all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in expr.elts):
            return [[e.value for e in expr.elts]]
        return None
    if isinstance(expr, ast.Attribute):
        return [TAGS] if expr.attr == "TAGS" and _imports_the_audit_module(tree) else None
    if not isinstance(expr, ast.Name):
        return None
    for node in ast.walk(tree):
        if (isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[-1] == THIS_MODULE
                and any(a.name == "TAGS" and (a.asname or a.name) == expr.id
                        for a in node.names)):
            return [TAGS]
    lists = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == expr.id for t in node.targets)):
            found = (_candidate_tag_lists(node.value, tree)
                     if isinstance(node.value, (ast.List, ast.Tuple)) else None)
            if found is None:
                return None
            lists.extend(found)
    return lists or None


def _is_axe_constructor(node):
    """True for `Axe(...)` and `something.Axe(...)`."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return ((isinstance(func, ast.Name) and func.id == "Axe")
            or (isinstance(func, ast.Attribute) and func.attr == "Axe"))


def _run_only_dicts(node):
    """Every dict literal at or under `node` that carries a "runOnly" key."""
    return [d for d in ast.walk(node)
            if isinstance(d, ast.Dict) and _dict_value(d, "runOnly") is not None]


def _names_a_run_only_dict(arg, tree):
    """True when `arg` is a variable assigned a dict literal carrying "runOnly"."""
    return isinstance(arg, ast.Name) and any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == arg.id for t in n.targets)
        and isinstance(n.value, ast.Dict)
        and _dict_value(n.value, "runOnly") is not None
        for n in ast.walk(tree))


def _axe_runs_in_source(source):
    """[(line, tag lists or None)] for every axe run in `source` that names tags.

    None means the tag list could not be read: a run with no runOnly option, or a
    tag list that is not a list literal, TAGS or a variable holding list literals.
    The caller treats that as a failure, because a list this check cannot see is
    one it cannot vouch for.
    """
    tree = ast.parse(source)
    runs = []
    for options in _run_only_dicts(tree):
        run_only = _dict_value(options, "runOnly")
        values = _dict_value(run_only, "values") if isinstance(run_only, ast.Dict) else None
        runs.append((options.lineno,
                     None if values is None else _candidate_tag_lists(values, tree)))
    axe_names = {t.id for node in ast.walk(tree)
                 if isinstance(node, ast.Assign) and _is_axe_constructor(node.value)
                 for t in node.targets if isinstance(t, ast.Name)}
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "run"):
            continue
        receiver = call.func.value
        if not (_is_axe_constructor(receiver)
                or (isinstance(receiver, ast.Name) and receiver.id in axe_names)):
            continue
        arguments = list(call.args) + [kw.value for kw in call.keywords]
        if _run_only_dicts(call) or any(_names_a_run_only_dict(a, tree) for a in arguments):
            continue                          # its tag list was read above
        runs.append((call.lineno, None))
    return runs


def _axe_runs_in_tests_smoke():
    """{file relative to tests/smoke: axe runs found there}, for every .py beneath it."""
    runs = {}
    for root, dirs, files in os.walk(SMOKE_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                found = _axe_runs_in_source(fh.read())
            if found:
                runs[os.path.relpath(path, SMOKE_DIR).replace(os.sep, "/")] = found
    return runs


def test_audit_tag_set_is_wcag22_aa_and_keeps_wcag21():
    """2.2 adds to 2.1: the tag set may gain, never lose, a 2.1 tag."""
    assert TAGS == ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"], (
        "the audit's tag set must be the four WCAG 2.1 tags plus wcag22aa; "
        "narrowing it to avoid a finding is not an accepted way to go green")


def test_every_axe_run_in_tests_smoke_uses_the_audit_tag_set():
    """Every axe run under tests/smoke audits exactly TAGS.

    Three files run axe today: this module, the ARB governance journey (which
    keeps its own copy of the list) and the transformation room journeys (which
    import TAGS). The last two assert on serious and critical findings directly,
    with no baseline, so a stale list in either audits a different standard
    without anything going red. Needs no browser: it reads the test sources.
    """
    runs = _axe_runs_in_tests_smoke()
    assert THIS_MODULE + ".py" in runs, (
        "the walk did not find this module's own axe runs, so it is not reading "
        "tests/smoke - which must not read as a pass")
    problems = []
    for name, entries in sorted(runs.items()):
        for line, candidates in entries:
            if candidates is None:
                problems.append(
                    "%s:%d: an axe run whose tag list this check cannot read (pass a "
                    "runOnly tag list written as a list literal, TAGS, or a variable "
                    "assigned a list literal)" % (name, line))
                continue
            for candidate in candidates:
                if candidate != TAGS:
                    problems.append("%s:%d: audits %s, not the audit's %s"
                                    % (name, line, candidate, TAGS))
    assert not problems, (
        "%d axe run(s) under tests/smoke do not use the audit's tag set:\n  %s"
        % (len(problems), "\n  ".join(problems)))


def test_the_axe_walker_reads_a_stale_list_literal_in_a_run():
    source = ('Axe().run(page, options={"runOnly": '
              '{"type": "tag", "values": ["wcag2a", "wcag21aa"]}})')
    assert _axe_runs_in_source(source) == [(1, [["wcag2a", "wcag21aa"]])]


def test_the_axe_walker_reads_variables_and_the_imported_tag_list():
    source = ("from .test_accessibility_audit import TAGS\n"
              "old = ['wcag2a']\n"
              "axe = axe_module.Axe()\n"
              "axe.run(page, options={'runOnly': {'type': 'tag', 'values': old}})\n"
              "axe.run(page, options={'runOnly': {'type': 'tag', 'values': TAGS}})\n")
    assert sorted(_axe_runs_in_source(source)) == [(4, [["wcag2a"]]), (5, [TAGS])]


@pytest.mark.parametrize("source", [
    "Axe().run(page)",
    "axe = Axe()\naxe.run(page, options=build_options())",
    'Axe().run(page, options={"runOnly": {"type": "tag", "values": build_tags()}})',
    'Axe().run(page, options={"runOnly": {"type": "tag"}})',
])
def test_the_axe_walker_flags_runs_whose_tag_list_it_cannot_read(source):
    assert any(candidates is None for _line, candidates in _axe_runs_in_source(source))


def test_the_axe_walker_ignores_runs_that_are_not_axe():
    assert _axe_runs_in_source('subprocess.run(["git", "status"])\nserver.run(port=1)') == []


def _note_problem(note):
    """Why `note` is not a dated one-line reason, or None if it is."""
    if not isinstance(note, str):
        return "the note is missing or is not text"
    match = NOTE_FORMAT.match(note)
    if not match:
        return ("not of the form 'YYYY-MM-DD: reason' with a reason of at least ten "
                "characters")
    try:
        accepted_on = datetime.date.fromisoformat(match.group(1))
    except ValueError:
        return "%s is not a real date" % match.group(1)
    if accepted_on > datetime.date.today():
        return "the date %s is in the future" % match.group(1)
    if not re.search(r"[^\W\d_]", match.group(2)):
        return "the reason contains no letters"
    return None


def _baseline_note_problems(data):
    """Every problem with the baseline's per-entry notes, as readable strings."""
    accepted = data.get("accepted", {})
    notes = data.get("accepted_notes", {})
    if not isinstance(accepted, dict):
        return ["'accepted' is not an object of page -> {rule: count}"]
    if not isinstance(notes, dict):
        return ["'accepted_notes' is not an object of page -> {rule: note}"]
    problems = ["accepted_notes[%r] is not an object of rule -> note" % path
                for path, page_notes in sorted(notes.items())
                if not isinstance(page_notes, dict)]
    for path, rules in sorted(accepted.items()):
        if not isinstance(rules, dict):
            problems.append("%s: its accepted entry is not an object of rule -> count" % path)
            continue
        page_notes = notes.get(path, {})
        if not isinstance(page_notes, dict):
            continue                          # reported above
        for rule in sorted(rules):
            problem = _note_problem(page_notes.get(rule))
            if problem:
                problems.append("%s: %s - %s" % (path, rule, problem))
    return problems


def test_every_accepted_baseline_entry_carries_a_date_and_a_reason():
    """An accepted violation with no dated reason is not acceptable.

    Needs no browser: it reads the committed baseline. `accepted_notes` maps
    page -> rule -> "YYYY-MM-DD: one-line reason" and is preserved across
    regeneration, so a rerun of the baseline writer cannot quietly drop it.
    """
    problems = _baseline_note_problems(_load_baseline_file())
    assert not problems, (
        "%d problem(s) with the baseline's accepted entries. Each entry needs "
        "accepted_notes[page][rule] = 'YYYY-MM-DD: what it is and why it is "
        "accepted rather than fixed now':\n  %s"
        % (len(problems), "\n  ".join(problems)))


@pytest.mark.parametrize("note", [
    None,
    42,
    "",
    "no date at all, just a reason",
    "2026-09-19: short",
    "2026-09-19: ..........",
    "2026-09-19: 1234567890 ---",
    "2026-02-30: an otherwise reasonable explanation",
    (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    + ": an otherwise reasonable explanation",
])
def test_a_baseline_note_without_a_real_dated_reason_is_rejected(note):
    assert _note_problem(note) is not None


def test_a_dated_reason_is_accepted():
    today = datetime.date.today().isoformat()
    assert _note_problem(today + ": small icon buttons; fix scheduled with the toolbar work") is None


@pytest.mark.parametrize("data", [
    {"accepted": {"/p": {"r": 1}}, "accepted_notes": {"/p": "not an object"}},
    {"accepted": {"/p": {"r": 1}}, "accepted_notes": {"/p": {"r": ["not text"]}}},
    {"accepted": {"/p": {"r": 1}}, "accepted_notes": []},
    {"accepted": {"/p": []}},
    {"accepted": []},
    {"accepted": {}, "accepted_notes": {"/p": 3}},
])
def test_malformed_baseline_notes_are_reported_rather_than_raised(data):
    assert _baseline_note_problems(data)


def test_the_rule_set_carries_the_wcag22_rules(axe_engine):
    """The tag is only worth naming if the rule behind it is actually loaded.

    Prints the bundled axe-core version (see the axe_engine fixture) so a reader
    can tell which rule set produced a given result. Run with -s to see it.
    """
    _require_rule_set(axe_engine)


def _engine_with(rules):
    return {"axe_core": "0.0.0-test", "package": "test", "rules": rules}


def test_the_required_rules_name_target_size_under_the_wcag22_tag():
    assert REQUIRED_RULES.get("target-size") == "wcag22aa"


def test_a_rule_list_without_target_size_is_a_problem():
    problems = _rule_set_problems(_engine_with({"color-contrast": ["wcag2aa"]}))
    assert len(problems) == 1
    assert "predates" in problems[0] and "target-size" in problems[0]


def test_a_target_size_rule_without_the_wcag22_tag_is_a_problem():
    problems = _rule_set_problems(_engine_with({"target-size": ["cat.sensory-and-visual-cues"]}))
    assert len(problems) == 1
    assert "not tagged wcag22aa" in problems[0]


def test_a_rule_list_with_target_size_under_the_wcag22_tag_is_fine():
    assert _rule_set_problems(_engine_with({"target-size": ["wcag22aa", "wcag258"]})) == []


def test_a_rule_set_problem_fails_the_run_and_a_clean_rule_set_does_not():
    with pytest.raises(pytest.fail.Exception):
        _require_rule_set(_engine_with({}))
    _require_rule_set(_engine_with({"target-size": ["wcag22aa"]}))


def test_the_tag_set_flags_a_seeded_small_target(axe_module, browser):
    """A positive control: the audit must be able to go red on a real 2.2 failure.

    Two adjacent 10 by 10 CSS pixel buttons, each named, on a page that serves
    nothing else. A single small target on its own passes target-size through the
    spacing exception, so the pair is what makes it a genuine failure. If this
    stops reporting `target-size`, a clean result on the real pages is
    meaningless whatever the rule list says.
    """
    page_html = (
        "<!doctype html><html lang='en'><head><title>Seeded small targets</title>"
        "</head><body><main>"
        "<button type='button' aria-label='First' "
        "style='width:10px;height:10px;padding:0;margin:0 2px 0 0'></button>"
        "<button type='button' aria-label='Second' "
        "style='width:10px;height:10px;padding:0;margin:0'></button>"
        "</main></body></html>")
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        page.set_content(page_html)
        report = axe_module.Axe().run(
            page, options={"runOnly": {"type": "tag", "values": TAGS}})
        data = report.response if hasattr(report, "response") else report
    finally:
        ctx.close()
    found = {v["id"]: len(v.get("nodes") or []) for v in data.get("violations", [])}
    print("[a11y] seeded small targets, axe-core %s: %s"
          % ((data.get("testEngine") or {}).get("version"), found))
    assert found.get("target-size") == 2, (
        "two adjacent 10x10px buttons should be reported by target-size (2 nodes) "
        "under the audit's tag set, got %r - the tag set cannot catch a WCAG 2.2 "
        "target-size failure" % found)


def test_no_new_serious_or_critical_violations(audited):
    """Serious and critical violations may not increase.

    A ratchet rather than an absolute gate, for the same reason every other gate
    here is one: the product carries known contrast failures today, and a check
    that can never go green is a check somebody deletes. New violations fail
    immediately; the existing count may only fall.

    What is in the baseline is real, unfixed WCAG failure - an accessibility
    audit of these pages against WCAG 2.2 AA will report every entry. Shrinking
    it is the point.
    """
    baseline = _load_baseline()
    regressions = []
    for path, violations in sorted(audited.items()):
        known = baseline.get(path, {})
        for rule, evidence in sorted(violations.items()):
            impact = evidence["impact"]
            count = evidence["count"]
            targets = evidence["targets"]
            if impact not in BLOCKING:
                continue
            was = known.get(rule)
            if was is None:
                regressions.append("%s: NEW %s (%s, %d element%s; targets: %s)"
                                   % (path, rule, impact, count, "" if count == 1 else "s",
                                      ", ".join(targets) or "unavailable"))
            elif count > was:
                regressions.append("%s: %s worsened %d -> %d elements"
                                   % (path, rule, was, count))
    assert not regressions, (
        "%d new or worsened serious/critical WCAG 2.2 AA violation(s):\n  %s\n\n"
        "These block a user rather than inconvenience them, and are the first "
        "thing an accessibility audit reports."
        % (len(regressions), "\n  ".join(regressions)))


def test_all_violations_do_not_increase(audited):
    """The same ratchet across every impact level."""
    baseline = _load_baseline()
    regressions = []
    for path, violations in sorted(audited.items()):
        known = baseline.get(path, {})
        for rule, evidence in sorted(violations.items()):
            impact = evidence["impact"]
            count = evidence["count"]
            targets = evidence["targets"]
            was = known.get(rule)
            if was is None:
                regressions.append("%s: NEW %s (%s, %d; targets: %s)"
                                   % (path, rule, impact, count,
                                      ", ".join(targets) or "unavailable"))
            elif count > was:
                regressions.append("%s: %s worsened %d -> %d" % (path, rule, was, count))
    assert not regressions, (
        "%d accessibility regression(s):\n  %s\n\n"
        "Fix them, or accept deliberately by regenerating "
        "tests/smoke/a11y_baseline.json and saying why."
        % (len(regressions), "\n  ".join(regressions)))


def test_the_audit_actually_ran(audited, axe_engine):
    """A crashed audit reports zero violations, which looks like success.

    Also the proof that a clean result means something: the rule set carries the
    WCAG 2.2 rule, and that rule was actually evaluated on every audited page.
    """
    assert audited, "no pages were audited"
    assert len(audited) == len({p for _a, p in AUDIT}), (
        "expected %d pages, audited %d - a page failed to load and its result is "
        "missing, which would read as a clean pass"
        % (len({p for _a, p in AUDIT}), len(audited)))
    _require_rule_set(axe_engine)
    print("[a11y] audited %d pages under axe-core %s"
          % (len(audited), ", ".join(sorted(str(v) for v in audited.engine_versions))))
    assert audited.engine_versions == {axe_engine["axe_core"]}, (
        "the pages were audited by axe-core %s but the loaded rule list came from "
        "%s - the rule check is not describing the run that produced the result"
        % (sorted(str(v) for v in audited.engine_versions), axe_engine["axe_core"]))
    for path, per_rule in sorted(audited.activity.items()):
        for rule, kinds in sorted(per_rule.items()):
            print("[a11y] %s  %s elements: %s"
                  % (path, rule, ", ".join("%s=%d" % (k, kinds[k]) for k in RESULT_KINDS)))
    for rule in sorted(REQUIRED_RULES):
        unevaluated = sorted(p for p, ids in audited.evaluated.items() if rule not in ids)
        assert not unevaluated, (
            "the %r rule is in the loaded rule list but axe reported nothing for it "
            "on %s - the tag set did not select it, so a clean result there says "
            "nothing about it" % (rule, ", ".join(unevaluated)))


def test_ask_and_twin_map_are_audited_and_carry_no_accepted_violations(audited):
    """Both pages are in the audited list, were audited, and hold nothing in the
    baseline: they start at zero and stay there."""
    accepted = _load_baseline()
    for path in ("/intelligence/ask", "/intelligence/twin-map"):
        assert path in {p for _a, p in AUDIT}, "%s is missing from the audited list" % path
        assert path in audited, "%s was not audited" % path
        assert path not in accepted, "%s must never be baselined" % path
        assert audited[path] == {}, "%s has violations: %r" % (path, audited[path])


def test_the_states_of_ask_and_twin_map_have_no_violations(audited_intelligence_states):
    """The answer on Ask, the map with its table, and the provenance drawer open with
    "Full detail" expanded over each: zero violations under the audit's tag set, and
    the 2.2 target-size rule was evaluated on every one. Never baselined."""
    states = audited_intelligence_states
    assert set(states) == {label for _a, label, _k in INTELLIGENCE_STATES}
    accepted = _load_baseline()
    for label, violations in sorted(states.items()):
        assert label not in accepted
        assert violations == {}, "%s has violations: %r" % (label, violations)
        assert "target-size" in states.evaluated[label], (
            "the target-size rule did not run on %r, so a clean result says nothing" % label)


def test_write_baseline_when_asked(audited, audited_canvas_pages):
    """Regenerate the accepted set:  SMOKE_A11Y_UPDATE_BASELINE=1 pytest ...

    Deliberately a test rather than a script: the audit needs a live server, a
    seeded database and a browser, all of which the fixtures already stand up.

    Notes for entries that survive regeneration are carried over; a NEW entry is
    written with no note, so test_every_accepted_baseline_entry_carries_a_date_
    and_a_reason fails until someone says when it was accepted and why.

    Includes the canvas pages (audited_canvas_pages) — they are baselined
    the same way as every page in AUDIT, unlike Ask/Twin map's
    never-baselined pair.
    """
    if os.environ.get("SMOKE_A11Y_UPDATE_BASELINE") != "1":
        # This is a maintenance utility, not a release assertion. Count it as
        # a clean no-op instead of making every qualification run carry a skip.
        return
    combined = dict(audited)
    combined.update(audited_canvas_pages)
    accepted = {
        p: {r: evidence["count"] for r, evidence in v.items()}
        for p, v in combined.items()
    }
    previous_notes = _load_baseline_file().get("accepted_notes", {})
    notes = {}
    for path, rules in accepted.items():
        kept = {r: previous_notes[path][r] for r in rules
                if r in previous_notes.get(path, {})}
        if kept:
            notes[path] = kept
    payload = {"_comment": BASELINE_COMMENT, "accepted": accepted}
    if notes:
        payload["accepted_notes"] = notes
    with open(BASELINE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("a11y baseline written: %d page(s), %d rule instance(s)"
          % (len(accepted), sum(len(v) for v in accepted.values())))
    unexplained = ["%s: %s" % (p, r) for p, rules in sorted(accepted.items())
                   for r in sorted(rules) if r not in notes.get(p, {})]
    if unexplained:
        print("a11y baseline entries still needing a dated one-line reason in "
              "accepted_notes:\n  " + "\n  ".join(unexplained))


# --------------------------------------------------------------------- #
# R1-08: the programme-structure and workstream-objective screens        #
# (US-16.4). New screens; audited at zero accepted violations, the same #
# rule Ask and Twin map joined the list under -- a violation here is    #
# fixed, not baselined. Each needs a typed journey/workstream a plain   #
# page load cannot reach, so it gets its own fixture rather than a      #
# static AUDIT entry.                                                   #
# --------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def r1_08_pages(seeded):
    from app import create_app, db
    from app.models.architecture_journey import ArchitectureJourney
    from app.models.strategic import StrategicInitiative
    from app.models.transformation_programme import ProgrammeWorkstream
    from app.models.user import User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org_id = seeded["ids"]["org"]
        owner = User.query.filter_by(email=seeded["emails"]["enterprise_architect"]).one()
        journey = ArchitectureJourney(
            owner_id=owner.id, organization_id=org_id, title="A11y switch %s" % suffix,
            intent="portfolio_change", selected_layers=["motivation"], programme_type="s4hana",
            journey_state={},
        )
        db.session.add(journey)
        programme = StrategicInitiative(
            organization_id=org_id, name="A11y programme %s" % suffix, description="A11y fixture.",
            record_kind="transformation_programme", status="draft", owner_id=owner.id, revision=1,
        )
        db.session.add(programme)
        db.session.flush()
        workstream = ProgrammeWorkstream(
            organization_id=org_id, programme_id=programme.id, workstream_type="process",
            objective="A11y fixture workstream.", scope_expression={}, lifecycle_stage="objective",
            lead_id=owner.id, revision=1,
        )
        db.session.add(workstream)
        db.session.commit()
        paths = {
            "programme_structure": "/architecture-journey/work/%s/programme-structure" % journey.id,
            "workstream_objective": "/solutions/programmes/%s/workstreams/%s/objective" % (
                programme.id, workstream.id),
        }
    return paths


def test_r1_08_screens_carry_no_accepted_violations(axe_module, axe_engine, browser, live_server, seeded, r1_08_pages):
    _require_rule_set(axe_engine)
    axe = axe_module.Axe()
    offenders = {}
    for label, path in r1_08_pages.items():
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.set_default_timeout(PAGE_TIMEOUT)
        ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
        page = ctx.new_page()
        try:
            _login(page, live_server, seeded["emails"]["enterprise_architect"])
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(1000)
            report = axe.run(page, options={"runOnly": {"type": "tag", "values": TAGS}})
            data = report.response if hasattr(report, "response") else report
            blocking = [v for v in data.get("violations", []) if v.get("impact") in BLOCKING]
            if blocking:
                offenders[label] = [_violation_evidence(v) for v in blocking]
        finally:
            ctx.close()
    assert offenders == {}, offenders

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
loaded rule list. It likewise FAILS, rather than skips, when axe-playwright-python
is not installed: a skipped test reports as passed, which would let a CI run say
"no accessibility violations" when no accessibility check ran at all.

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

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

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

# The audit's second copy of the tag list lives in another file, inside the ARB
# governance journey. test_arb_journey_tag_set_mirrors_the_audit_tag_set reads it
# from source so the two cannot drift apart silently.
ARB_JOURNEY = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "test_typed_arb_governance_journey.py")
ARB_JOURNEY_TEST = "test_journey_g_axe_has_no_serious_or_critical_findings"

# WCAG 2.2 Level AA, with Level A - the level the approved design commits to, as
# a build-and-test standard rather than a conformance claim. 2.2 includes 2.1, so
# the four 2.1 tags stay and `wcag22aa` is added. EN 301 549 v4.1.1 adopts 2.2 but
# is not yet the legal reference (v3.2.1 remains so until v4.1.1 is cited in the
# Official Journal of the European Union): that is context, not the reason for
# the move.
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]

# What the `wcag22aa` tag exists to add: rule id -> the tag it must carry. A rule
# set without these makes a clean 2.2 result meaningless, so the audit refuses to
# draw any conclusion from one.
REQUIRED_RULES = {"target-size": "wcag22aa"}

# The four places axe reports a rule it ran; a rule in none of them did not run.
RESULT_KINDS = ("violations", "passes", "incomplete", "inapplicable")

# Every accepted baseline entry must say when it was accepted and why. The date
# is checked as a real date; the reason must be a sentence, not a placeholder.
NOTE_FORMAT = re.compile(r"^(\d{4}-\d{2}-\d{2}): (\S.{9,})$")
BASELINE_COMMENT = (
    "Accepted axe-core violations per page under the WCAG 2.2 AA tag set "
    "(wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa). Every entry is a real, "
    "unfixed failure against that tag set, not a statement that the page is "
    "accessible, and each carries its acceptance date and a one-line reason in "
    "accepted_notes. Regenerate with SMOKE_A11Y_UPDATE_BASELINE=1.")

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


def _tags_declared_in_arb_journey():
    """The literal `tags = [...]` inside the ARB governance journey's axe test."""
    with open(ARB_JOURNEY, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    found = []
    for func in ast.walk(tree):
        if isinstance(func, ast.FunctionDef) and func.name == ARB_JOURNEY_TEST:
            for node in ast.walk(func):
                if (isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "tags"
                                for t in node.targets)):
                    found.append(ast.literal_eval(node.value))
    assert len(found) == 1, (
        "expected exactly one `tags = [...]` assignment in %s, found %d - the "
        "mirror check cannot run, which must not read as a pass"
        % (ARB_JOURNEY_TEST, len(found)))
    return found[0]


def test_audit_tag_set_is_wcag22_aa_and_keeps_wcag21():
    """2.2 adds to 2.1: the tag set may gain, never lose, a 2.1 tag."""
    assert TAGS == ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"], (
        "the audit's tag set must be the four WCAG 2.1 tags plus wcag22aa; "
        "narrowing it to avoid a finding is not an accepted way to go green")


def test_arb_journey_tag_set_mirrors_the_audit_tag_set():
    """The ARB journey carries its own copy of the tag list; keep it identical."""
    assert _tags_declared_in_arb_journey() == TAGS, (
        "%s audits a different tag set from this module - update it to match TAGS"
        % ARB_JOURNEY_TEST)


def test_every_accepted_baseline_entry_carries_a_date_and_a_reason():
    """An accepted violation with no date and no reason is not acceptable.

    Needs no browser: it reads the committed baseline. `accepted_notes` maps
    page -> rule -> "YYYY-MM-DD: one-line reason" and is preserved across
    regeneration, so a rerun of the baseline writer cannot quietly drop it.
    """
    data = _load_baseline_file()
    notes = data.get("accepted_notes", {})
    problems = []
    for path, rules in sorted(data.get("accepted", {}).items()):
        for rule in sorted(rules):
            note = notes.get(path, {}).get(rule, "")
            match = NOTE_FORMAT.match(note)
            try:
                if not match:
                    raise ValueError("no 'YYYY-MM-DD: reason' note")
                datetime.date.fromisoformat(match.group(1))
            except ValueError:
                problems.append("%s: %s (note: %r)" % (path, rule, note))
    assert not problems, (
        "%d accepted baseline entr%s no acceptance date and reason. Add "
        "accepted_notes[page][rule] = 'YYYY-MM-DD: what it is and why it is "
        "accepted rather than fixed now':\n  %s"
        % (len(problems), "y has" if len(problems) == 1 else "ies have",
           "\n  ".join(problems)))


def test_the_rule_set_carries_the_wcag22_rules(axe_engine):
    """The tag is only worth naming if the rule behind it is actually loaded.

    Prints the bundled axe-core version (see the axe_engine fixture) so a reader
    can tell which rule set produced a given result. Run with -s to see it.
    """
    _require_rule_set(axe_engine)


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


def test_write_baseline_when_asked(audited):
    """Regenerate the accepted set:  SMOKE_A11Y_UPDATE_BASELINE=1 pytest ...

    Deliberately a test rather than a script: the audit needs a live server, a
    seeded database and a browser, all of which the fixtures already stand up.

    Notes for entries that survive regeneration are carried over; a NEW entry is
    written with no note, so test_every_accepted_baseline_entry_carries_a_date_
    and_a_reason fails until someone says when it was accepted and why.
    """
    if os.environ.get("SMOKE_A11Y_UPDATE_BASELINE") != "1":
        # This is a maintenance utility, not a release assertion. Count it as
        # a clean no-op instead of making every qualification run carry a skip.
        return
    accepted = {
        p: {r: evidence["count"] for r, evidence in v.items()}
        for p, v in audited.items()
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

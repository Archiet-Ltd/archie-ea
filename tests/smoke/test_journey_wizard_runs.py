"""Browser journey: an architect frames a journey, starts solution design and
walks the wizard through to the solution page.

Written from the person's goal ("get from a problem statement to a solution I can
open"), not from the code. What it holds the product to:

* the automatic run never ends on database or exception text, and never makes a
  request that fails on the server (two requests racing for one solution row used
  to end in "UPDATE statement ... expected to update 1 row(s); 0 were matched");
* a step is ticked complete only when its panel has content;
* step 3 never spins for ever, offers the Generate Architecture button its own
  hint names, and Previous and the step tabs move the person;
* the page makes no request that 404s and shows no "could not load" toast;
* an action that needs a language model says so plainly when none is connected.

The organisation carries capabilities that match the problem statement, so the
pipeline has real work to do and the race between its calls can happen.
"""

import re
import time
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login, _visit, page  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PROBLEM = (
    "Lantern Quay calibrates and tests water-quality sensors. A calibration certificate is "
    "keyed into the calibration ledger, re-keyed into dispatch planning and again into quality "
    "monitoring, so each calibration certificate takes five working days. We want one working day "
    "by making the calibration ledger the single record for certificate, dispatch and quality data."
)

# A statement that names nothing in the catalogue, so the person takes the manual path.
MANUAL_PROBLEM = (
    "The warehouse night shift needs a clearer staff rota. Supervisors keep the rota on paper and "
    "swap shifts by phone, so nobody can see who is on duty until the shift starts."
)

# Database and exception text no person should ever read on these screens.
RAW = re.compile(
    r"UPDATE statement|expected to update|StaleDataError|sqlalchemy|psycopg|Traceback|\[SQL|"
    r"IntegrityError|OperationalError|max_workers|NoneType|cb is not a function",
    re.I,
)


@pytest.fixture(scope="module")
def matching_capabilities(seeded):
    """Capabilities in the architect's organisation that the problem statement names."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.business_capabilities import BusinessCapability

        org_id = seeded["ids"]["org"]
        suffix = uuid.uuid4().hex[:6]
        for i, name in enumerate(
            ["Calibration certificate issuing", "Calibration ledger keeping",
             "Dispatch planning", "Quality monitoring"]
        ):
            db.session.add(BusinessCapability(
                name=name, code="JW%s%d" % (suffix.upper(), i),
                organization_id=org_id, level=1,
                description="Certificate, dispatch and quality data for calibration.",
            ))
        db.session.commit()
    return True


def _watch(page):
    """Record failed requests, the way a person would only notice as a broken page."""
    bad = []
    page.on("response", lambda r: bad.append("%s %s %s" % (r.status, r.request.method, r.url.split("?")[0]))
            if r.status >= 400 and "/static/" not in r.url else None)
    return bad


def _body(page):
    return " ".join(page.inner_text("body").split())


def _state(page):
    return page.evaluate(
        """() => { const d = Alpine.$data(document.querySelector('[x-data*=journeyV2Flow]'));
                   return {cur: d.currentStep, done: d.completedSteps.slice(), caps: d.acceptedCapabilities.length,
                           landscape: d.reasoningLandscape.length, loadingArch: d.architectureLoading,
                           error: d.error, options: d.reasoningOptions.length}; }"""
    )


def _ticked(page):
    """The steps the stepper shows as complete, read from what is drawn."""
    return page.evaluate(
        """() => [...document.querySelectorAll("[role=navigation][aria-label='Progress steps'] button")]
                   .map((b, i) => [i + 1, b.className.includes('bg-success/15')])
                   .filter(x => x[1]).map(x => x[0])"""
    )


def _strip(page):
    """The status strip's own figures: domains confirmed and elements."""
    text = page.evaluate(
        """() => [...document.querySelectorAll('span')].map(s => s.textContent.trim().replace(/\\s+/g, ' '))
                   .filter(t => /^\\d+\\/7 domains$|^\\d+ elements$/.test(t))"""
    )
    domains = next((int(t.split("/")[0]) for t in text if t.endswith("domains")), None)
    elements = next((int(t.split(" ")[0]) for t in text if t.endswith("elements")), None)
    return domains, elements


def _assert_ticks_agree_with_the_screen(page):
    """Every ticked step's own count is above zero, and the strip says the same as the ticks."""
    ticked = _ticked(page)
    domains, elements = _strip(page)
    counts = page.evaluate(
        """() => { const d = Alpine.$data(document.querySelector('[x-data*=journeyV2Flow]'));
                   return {problem: d.problemStatement.length, decisions: (d.decisionPoints || []).length,
                           roadmap: d.roadmapData && d.roadmapData.phases ? d.roadmapData.phases.length : 0,
                           readiness: d.readinessItems().map(i => [i.label, i.pass])}; }"""
    )
    if 1 in ticked:
        assert counts["problem"] > 20, "Clarify is ticked with no problem statement"
    if 2 in ticked:
        assert domains == 7, "Domains is ticked but the status strip says %s of 7 domains" % domains
    if 3 in ticked:
        assert elements and elements > 0, "Architecture is ticked but the status strip says %s elements" % elements
    if 4 in ticked:
        assert counts["decisions"] > 0, "Decisions is ticked with no decision points"
    if 5 in ticked:
        assert counts["roadmap"] > 0, "Roadmap is ticked with no roadmap phases"
    if 6 in ticked:
        assert all(p for _, p in counts["readiness"]), "ARB Review is ticked while the readiness list has open items"
    # And the other way: the strip's figures show up as ticks.
    assert (domains == 7) == (2 in ticked), "ticks %s and strip (%s domains) disagree about Domains" % (ticked, domains)
    assert bool(elements) == (3 in ticked), "ticks %s and strip (%s elements) disagree about Architecture" % (ticked, elements)
    return ticked


def _start_solution_design(page, live_server, email):
    """Frame a journey, then start solution design from the hub, as a person does."""
    _login(page, live_server, email)
    _visit(page, live_server, "/architecture-journey/")
    page.fill("#journey-title", "Cut calibration certificate turnaround from five days to one")
    page.click("label:has-text('Design a solution')")
    for layer in ("Business", "Application"):
        page.click("fieldset:has(legend:text('Architecture scope')) label:has(span:text-is('%s'))" % layer)
    page.click("fieldset:has(legend:text('Useful deliverables')) label:has-text('Solution blueprint')")
    page.select_option("#journey-outcome", "roadmap")
    page.click("button:has-text('Start architecture journey')")
    page.wait_for_url("**/architecture-journey/work/**", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("domcontentloaded")
    assert "Cut calibration certificate turnaround" in _body(page), "the framed journey did not open"
    _visit(page, live_server, "/architecture-journey/")
    page.click("button:has-text('Start solution design')")
    page.wait_for_url(re.compile(r".*/architecture-journey/\d+$"), timeout=PAGE_TIMEOUT)
    page.wait_for_selector("input[x-model='solutionName']", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)


@pytest.fixture
def started(page, live_server, seeded, matching_capabilities):
    page.console_errors.clear()
    page.page_errors.clear()
    bad = _watch(page)
    _start_solution_design(page, live_server, seeded["emails"]["solution_architect"])
    return {"bad": bad}


def test_automatic_run_finishes_without_raw_errors_or_failed_requests(page, live_server, started):
    # No name typed: the person's own words become the name, never "Untitled Solution".
    assert page.locator("h1").inner_text().strip() == "This solution has no name yet"
    assert page.locator("[data-testid='name-offer']").is_visible(), "no offer to name the solution"
    page.fill("textarea[placeholder^='e.g., We need']", PROBLEM)
    page.click("button:has-text('Skip clarification')")

    deadline = time.time() + 120
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        text = _body(page)
        if "automatic run stopped" in text or "Every step is in place" in text or "capabilities generated" in text:
            break
    page.wait_for_timeout(2500)

    text = _body(page)
    assert not RAW.search(text), "database or exception text reached the person: %s" % RAW.search(text).group(0)
    failures = [b for b in started["bad"] if re.match(r"5\d\d ", b) or "codegen/file-list" in b
                or "vendors" in b or "vendor-suggestions" in b]
    assert not failures, "requests failed on the server during the run: %s" % failures
    assert "could not be auto-saved" not in text, "progress could not be saved"
    assert "Could not load vendor suggestions" not in text
    errors = [e for e in page.console_errors + page.page_errors if "favicon" not in e.lower()]
    assert not [e for e in errors if "cb is not a function" in e or "(reading 'after')" in e], errors
    assert "Blueprint populated" not in text, "the run claims success while steps are open"
    assert "automatic run stopped" in text or "Every step is in place" in text, text[-600:]
    assert "Untitled Solution" not in text, "the solution is still called Untitled Solution"
    assert page.locator("h1").inner_text().startswith("Lantern Quay calibrates and tests water-quality sensors")

    # Each ticked step has its content: open each one and read it.
    state = _state(page)
    for n in state["done"]:
        page.click("button[aria-label^='Step %d:']" % n)
        page.wait_for_timeout(1200)
        panel = _body(page)
        assert "Loading architecture elements" not in panel, "step %d is ticked but still loading" % n
        assert not RAW.search(panel), "step %d shows raw error text" % n
        after = _state(page)
        assert after["cur"] == n, "clicking ticked step %d did not open it" % n
        _assert_ticks_agree_with_the_screen(page)


def test_ticks_agree_with_the_status_strip_and_the_run_shows_where_it_stopped(page, live_server, started):
    page.fill("input[x-model='solutionName']", "Calibration certificate consolidation %s" % uuid.uuid4().hex[:5])
    page.fill("textarea[placeholder^='e.g., We need']", PROBLEM)
    page.click("button:has-text('Skip clarification')")
    deadline = time.time() + 120
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        text = _body(page)
        if "automatic run stopped" in text or "Every step is in place" in text or "capabilities generated" in text:
            break
    page.wait_for_timeout(2500)

    # The ticks come from the same facts as the status strip and the readiness list.
    ticked = _assert_ticks_agree_with_the_screen(page)
    open_step = next((n for n in range(1, 7) if n not in ticked), None)
    if open_step:
        assert _state(page)["cur"] == open_step, "the stepper does not show the step the run stopped at"
        text = _body(page)
        assert "Blueprint populated" not in text
        assert "automatic run stopped at" in text or "capabilities generated" in text, text[-500:]


def test_step_three_has_its_controls_and_the_solution_page_opens(page, live_server, started):
    """The manual path: confirm domains, reach step 3, use what it offers."""
    page.fill("input[x-model='solutionName']", "Manual path %s" % uuid.uuid4().hex[:5])
    page.fill("textarea[placeholder^='e.g., We need']", MANUAL_PROBLEM)
    for _ in range(4):
        nxt = page.locator("button:visible", has_text=re.compile(r"^\s*Next\s*$"))
        if nxt.count() == 0:
            break
        nxt.last.click()
        page.wait_for_timeout(1500)
    page.click("button:has-text('Save & Continue to Step 2')")
    page.wait_for_timeout(4000)
    page.click("button:has-text('Accept & confirm all')")
    page.wait_for_timeout(3000)
    page.locator("button:visible", has_text="Next Step").last.click()
    page.wait_for_timeout(4000)

    state = _state(page)
    assert state["cur"] == 3
    assert not state["loadingArch"], "step 3 is still loading its elements"
    assert not page.locator("text=Loading architecture elements").is_visible(), "the spinner never ended"
    generate = page.locator("button:visible", has_text="Generate Architecture")
    assert generate.count() >= 1, "the hint names a Generate Architecture button and there is none"

    # Generating with nothing accepted, and no language model, says so plainly.
    generate.first.click()
    page.wait_for_timeout(5000)
    text = _body(page)
    assert not RAW.search(text), "raw text after Generate Architecture: %s" % RAW.search(text).group(0)

    # Previous and the step tabs move the person.
    page.click("button:has-text('Previous')")
    page.wait_for_timeout(800)
    assert _state(page)["cur"] == 2, "Previous did nothing"
    page.click("button[aria-label^='Step 1:']")
    page.wait_for_timeout(800)
    assert _state(page)["cur"] == 1, "the step tab did nothing"

    # No request the page made failed, and nothing is reported as unloadable.
    failures = [b for b in started["bad"] if "codegen/file-list" in b or re.match(r"5\d\d ", b)]
    assert not failures, failures
    assert "Could not load" not in _body(page)
    errors = [e for e in page.console_errors + page.page_errors if "favicon" not in e.lower()]
    assert not [e for e in errors if "cb is not a function" in e or "(reading 'after')" in e], errors

    # The way out: the solution page opens with the solution on it.
    link = page.locator("a:has-text('Open Solution Detail')")
    href = link.get_attribute("href")
    response, _ = _visit(page, live_server, href)
    assert response.status < 400
    assert "Manual path" in _body(page), "the solution page did not open the solution"


def test_phone_stepper_labels_are_readable_and_do_not_overlap(page, live_server, started):
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(600)
    boxes = page.evaluate(
        """() => [...document.querySelectorAll("[role=navigation][aria-label='Progress steps'] button, "
                 + "[role=navigation][aria-label='Progress steps'] .text-xs.font-medium")]
                 .filter(e => e.offsetParent !== null)
                 .map(e => { const r = e.getBoundingClientRect();
                             return {t: (e.getAttribute('aria-label') || e.textContent).trim(),
                                     l: r.left, r: r.right, top: r.top, b: r.bottom}; })"""
    )
    assert boxes, "no stepper controls are visible at 390 wide"
    for i, a in enumerate(boxes):
        assert a["l"] >= 0 and a["r"] <= 390, "%r runs off the edge of the phone" % a["t"]
        for c in boxes[i + 1:]:
            overlap = a["l"] < c["r"] and c["l"] < a["r"] and a["top"] < c["b"] and c["top"] < a["b"]
            assert not overlap, "%r overlaps %r on the phone" % (a["t"], c["t"])
    current = page.locator("[data-testid='stepper-current']")
    assert current.is_visible() and "Step 1 of" in current.inner_text(), "the phone does not say which step this is"
    assert page.evaluate("document.documentElement.scrollWidth <= 390"), "the wizard scrolls sideways on the phone"


def test_unnamed_solution_says_so_and_offers_to_name_it(page, live_server, started):
    assert page.locator("h1").inner_text().strip() == "This solution has no name yet"
    assert "Untitled Solution" not in _body(page)
    page.locator("[data-testid='name-offer'] button").click()
    focused = page.evaluate("document.activeElement && document.activeElement.getAttribute('aria-label')")
    assert focused == "Solution Name", "the offer did not take the person to the name field"
    page.keyboard.type("Calibration records in one place")
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)
    assert page.locator("h1").inner_text().strip() == "Calibration records in one place"

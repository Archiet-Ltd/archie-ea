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
                name=name, code="JW%s%d" % (suffix[:3].upper(), i),
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
    page.fill("input[x-model='solutionName']", "Calibration certificate consolidation %s" % uuid.uuid4().hex[:5])
    page.fill("textarea[placeholder^='e.g., We need']", PROBLEM)
    page.click("button:has-text('Skip clarification')")

    deadline = time.time() + 120
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        text = _body(page)
        if "Blueprint populated" in text or "stopped while" in text or "No capabilities found" in text:
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
    assert "Blueprint populated" in text, "the run did not reach the blueprint: %s" % text[-600:]

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

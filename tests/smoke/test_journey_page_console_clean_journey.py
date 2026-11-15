"""A solution architect opens a solution's journey page. The page shows the solution and its steps, the browser
console stays free of errors, and nothing the page asks for is missing."""
import uuid

import os

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000


@pytest.fixture()
def journey_solution(app, seeded):
    from app import db
    from app.models.solution_domain_spec import SolutionDomainSpec
    from app.models.solution_models import Solution
    from app.models.user import User

    name = "Field Calibration Platform %s" % uuid.uuid4().hex[:6]
    with app.app_context():
        owner = User.query.filter_by(email=seeded["emails"]["solution_architect"]).one()
        solution = Solution(name=name, description="Calibration of field instruments.",
                            organization_id=owner.organization_id, created_by_id=owner.id,
                            governance_status="draft", has_acm_domains=True,
                            journey_state={"current_step": 3, "domains_populated": True})
        db.session.add(solution)
        db.session.flush()
        db.session.add(SolutionDomainSpec(solution_id=solution.id, organization_id=owner.organization_id,
                                          domain_code="COM", status="confirmed"))
        db.session.commit()
        payload = {"solution_id": solution.id, "name": name}
    yield payload
    with app.app_context():
        SolutionDomainSpec.query.filter_by(solution_id=payload["solution_id"]).delete()
        row = db.session.get(Solution, payload["solution_id"])
        if row is not None:
            db.session.delete(row)
        db.session.commit()


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_journey_page_opens_with_a_clean_console(browser, live_server, seeded, journey_solution, viewport):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    page = context.new_page()
    problems = []
    page.on("console", lambda m: problems.append("console: " + m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append("page error: " + str(e)))
    page.on("response", lambda r: problems.append("%s %s" % (r.status, r.url)) if r.status >= 400 else None)
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", seeded["emails"]["solution_architect"])
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
        problems.clear()
        page.goto(live_server + "/architecture-journey/%d" % journey_solution["solution_id"],
                  wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=journey_solution["name"])).to_be_visible(timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(3000)
        # A generated architecture arriving changes what the diagram panel watches; that must not raise.
        changed = page.evaluate("""() => {
            const el = [...document.querySelectorAll('[x-data]')].find(e => (e.getAttribute('x-data') || '').includes('diagramReady'));
            if (!el) return 'no diagram panel';
            const host = Alpine.$data(el.parentElement);
            host.architectureResult = {
                elements_by_layer: { application: [{ name: 'Calibration Service', type: 'ApplicationComponent' },
                                                   { name: 'Instrument Registry', type: 'ApplicationComponent' }] },
                relationships: [{ source_name: 'Calibration Service', target_name: 'Instrument Registry', type: 'Serving' }] };
            return 'changed';
        }""")
        assert changed == "changed", changed
        page.wait_for_timeout(1500)
        drawn = page.locator("#journey-arch-diagram")
        expect(drawn).to_contain_text("Calibration Service", timeout=PAGE_TIMEOUT)
        _frames = os.environ.get("SMALL_FIX_FRAMES_DIR")
        if _frames:
            os.makedirs(_frames, exist_ok=True)
            page.screenshot(path="%s/%s-%d.png" % (_frames, "journey-page", page.viewport_size["width"]), full_page=True)
        assert not problems, problems
    finally:
        context.close()

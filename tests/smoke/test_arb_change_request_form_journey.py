"""A solution architect raises an ARB change request from the form: it is created, opens
on its own page, counts as Open in the list and stays inside their organisation; a viewer
is refused in plain words and a missing title is explained next to the title field."""

import os
import re
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("ARB_CR_FORM_WALK_DIR", "")
FORM = "/arb/change-requests/new"
LIST = "/arb/change-requests"


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _context(browser, width, dark=False):
    context = browser.new_context(
        viewport={"width": width, "height": 900},
        color_scheme="dark" if dark else "light",
    )
    init = "localStorage.setItem('archie_onboarding_ts', '1');"
    if dark:
        init += " localStorage.setItem('theme', 'dark'); document.documentElement.classList.add('dark');"
    context.add_init_script(init)
    return context


def _shot(page, name, width, theme):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, "%s-%s-%d.png" % (name, theme, width)), full_page=True)


def _open_count(page):
    """The figure on the list's Open card: the text beside the label that reads exactly Open."""
    text = page.evaluate("""() => {
        const label = [...document.querySelectorAll('div, p, span, h3')].find(
            el => el.children.length === 0 && el.textContent.trim() === 'Open' && el.tagName !== 'OPTION');
        return label ? label.parentElement.innerText : null;
    }""")
    assert text, "the list shows no Open card"
    return int(re.sub(r"\D", "", text))


@pytest.fixture(scope="module")
def world(seeded):
    """Linked data in the seeded organisation, a viewer in it, and a second organisation."""
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.solution_models import Solution
    from app.models.unified_capability import UnifiedCapability
    from app.models.user import Role, User

    app = create_app("testing")
    tag = uuid.uuid4().hex[:8]
    out = {"tag": tag, "viewer": "smoke.cr.viewer.%s@example.com" % tag,
           "outsider": "smoke.cr.outsider.%s@example.com" % tag}
    org_id = seeded["ids"]["org"]
    with app.app_context():
        architect = User.query.filter_by(email=seeded["emails"]["solution_architect"]).one()
        from app.models.architecture_decision import ArchitectureDecision, DecisionCapabilityLink
        from app.models.business_capabilities import BusinessCapability

        # The capability the request is about, projected from the business capability an accepted
        # decision governs.
        source = BusinessCapability(name="Claims handling %s" % tag, organization_id=org_id, level=1)
        db.session.add(source)
        db.session.flush()
        decision = ArchitectureDecision(title="Keep claims data in the shared store %s" % tag,
                                        organization_id=org_id, enterprise_level=False, status="accepted")
        db.session.add(decision)
        db.session.flush()
        db.session.add(DecisionCapabilityLink(decision_id=decision.id, capability_id=source.id))
        # Saving a business capability projects it into the unified store.
        db.session.flush()
        capability = UnifiedCapability.query.filter_by(
            source_table="business_capability", source_id=str(source.id)).one()
        solution = Solution(name="Claims platform %s" % tag, description="Linked solution",
                            organization_id=org_id, created_by_id=architect.id,
                            governance_status="draft")
        other = Organization(name="Other Org %s" % tag, slug="other-%s" % tag)
        db.session.add_all([solution, other])
        db.session.flush()
        other_capability = UnifiedCapability(name="Other capability %s" % tag, organization_id=other.id,
                                             scope="tenant", level=1)
        other_solution = Solution(name="Other solution %s" % tag, description="Other organisation",
                                  organization_id=other.id, created_by_id=architect.id,
                                  governance_status="draft")
        db.session.add_all([other_capability, other_solution])
        db.session.flush()
        for email, role, org in ((out["viewer"], "Viewer", org_id), (out["outsider"], "Architect", other.id)):
            user = User(email=email, first_name="Smoke", last_name="CR", organization_id=org,
                        enterprise_role="solution_architect", confirmed=True,
                        role=Role.query.filter_by(name=role).one())
            user.password = PASSWORD
            db.session.add(user)
        db.session.commit()
        out.update(capability_id=capability.id, capability_name=capability.name,
                   solution_id=solution.id, solution_name=solution.name, other_org=other.id,
                   decision_id=decision.id, decision_title=decision.title, source_id=source.id,
                   other_capability_id=other_capability.id, other_solution_id=other_solution.id)
    yield out
    from app.models.architecture_decision import ArchitectureChangeRequest

    with app.app_context():
        for cr in ArchitectureChangeRequest.query.filter(
                ArchitectureChangeRequest.title.like("%% %s" % tag)).all():
            db.session.delete(cr)
        db.session.commit()
        db.session.remove()
        for email in (out["viewer"], out["outsider"]):
            user = User.query.filter_by(email=email).one()
            db.session.execute(db.text("DELETE FROM soc2_audit_log WHERE user_id = :uid"), {"uid": user.id})
            db.session.delete(user)
        db.session.commit()
        db.session.delete(db.session.get(Solution, out["solution_id"]))
        db.session.delete(db.session.get(Solution, out["other_solution_id"]))
        db.session.delete(db.session.get(UnifiedCapability, out["other_capability_id"]))
        db.session.delete(db.session.get(UnifiedCapability, out["capability_id"]))
        from app.models.architecture_decision import ArchitectureDecision, DecisionCapabilityLink
        from app.models.business_capabilities import BusinessCapability

        DecisionCapabilityLink.query.filter_by(decision_id=out["decision_id"]).delete()
        db.session.delete(db.session.get(ArchitectureDecision, out["decision_id"]))
        db.session.delete(db.session.get(BusinessCapability, out["source_id"]))
        db.session.execute(db.text("DELETE FROM soc2_audit_log WHERE organization_id = :oid"), {"oid": out["other_org"]})
        db.session.delete(db.session.get(Organization, out["other_org"]))
        db.session.commit()


def _fill_form(page, world, title):
    page.get_by_label("Title", exact=False).first.fill(title)
    page.locator("#cr-description").fill("The claims platform must move to the shared data store.")
    page.locator("#cr-trigger-type").select_option("regulation")
    page.locator("#cr-capability-id").fill(str(world["capability_id"]))
    page.locator("#cr-solution-id").fill(str(world["solution_id"]))


@pytest.mark.parametrize("width,dark", [(1440, False), (390, True)])
def test_architect_raises_a_change_request_and_sees_it_open(browser, live_server, seeded, world, width, dark):
    theme = "dark" if dark else "light"
    title = "Move claims to shared store %d %s" % (width, world["tag"])
    context = _context(browser, width, dark)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + LIST, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        before = _open_count(page)
        page.get_by_role("link", name="New Change Request").first.click()
        page.wait_for_url("**" + FORM, timeout=PAGE_TIMEOUT)
        _fill_form(page, world, title)
        _shot(page, "form-filled", width, theme)
        page.get_by_role("button", name="Submit Change Request").click()

        page.wait_for_url(re.compile(r".*/arb/change-requests/\d+$"), timeout=PAGE_TIMEOUT)
        body = page.locator("main, body").first.inner_text()
        assert title in body
        assert "The claims platform must move to the shared data store." in body
        assert "Regulation" in body
        assert "Open" in body
        # Raised By names the person who submitted, never a Python object.
        raised_by = page.locator("dt", has_text="Raised By").locator("xpath=following-sibling::dd[1]").inner_text().strip()
        assert raised_by == "Smoke solution_arc", raised_by
        assert "bound method" not in body and "<User" not in body
        _shot(page, "detail", width, theme)
        detail_url = page.url

        # Both links the request carries land on a page that names the thing clicked.
        page.get_by_role("link", name=world["solution_name"]).click()
        page.wait_for_load_state("domcontentloaded")
        assert world["solution_name"] in page.locator("body").inner_text()
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("dl").get_by_role("link", name=world["capability_name"]).click()
        page.wait_for_load_state("domcontentloaded")
        assert world["capability_name"] in page.locator("body").inner_text()

        page.goto(live_server + LIST, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert _open_count(page) == before + 1
        row = page.locator("tr").filter(has_text=title)
        assert row.count() == 1
        assert "Open" in row.inner_text()
        _shot(page, "list", width, theme)
        row.get_by_role("link", name="View").click()
        page.wait_for_url(detail_url, timeout=PAGE_TIMEOUT)
        assert title in page.locator("body").inner_text()

        # Assess Impact lists the accepted decision that governs the linked capability, and every
        # name in the table leads to a page that names the thing clicked.
        assert page.locator("[data-testid=impact-row]").count() == 0
        with page.expect_response(re.compile(r".*/assess-impact$")) as assessed:
            page.get_by_role("button", name="Assess Impact").click()
        assert assessed.value.status == 200, assessed.value.text()
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=impact-row]').length > 0", timeout=PAGE_TIMEOUT)
        rows = page.locator("[data-testid=impact-row]")
        assert rows.count() == 1
        row_text = rows.first.inner_text()
        assert world["decision_title"] in row_text and world["capability_name"] in row_text
        assert "Medium" in row_text
        _shot(page, "detail-impacts", width, theme)
        rows.first.get_by_role("link", name=world["capability_name"]).click()
        page.wait_for_load_state("domcontentloaded")
        assert world["capability_name"] in page.locator("body").inner_text()
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator("[data-testid=impact-row]").count() == 1
        assert world["decision_title"] in page.locator("body").inner_text()
    finally:
        context.close()


@pytest.mark.parametrize("width,dark", [(1440, False), (390, True)])
def test_missing_title_is_explained_next_to_the_title_field(browser, live_server, seeded, world, width, dark):
    context = _context(browser, width, dark)
    page = context.new_page()
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + FORM, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("#cr-description").fill("A description without a title.")
        page.locator("#cr-trigger-type").select_option("market")
        page.get_by_role("button", name="Submit Change Request").click()
        error = page.locator("#cr-title-error")
        error.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert error.inner_text().strip() == "Enter a title for the change request."
        # It sits in the same block as the title field, directly under it.
        assert page.locator("#cr-title").evaluate(
            "el => el.parentElement.nextElementSibling && el.parentElement.nextElementSibling.id") == "cr-title-error"
        assert page.locator("#cr-title").get_attribute("aria-invalid") == "true"
        assert page.url.endswith(FORM)
        assert not [u for u in posts if "change-requests" in u and "/new" not in u]
        _shot(page, "title-error", width, "dark" if dark else "light")
    finally:
        context.close()


def test_a_wrong_linked_number_is_explained_next_to_its_field(browser, live_server, seeded, world):
    context = _context(browser, 1440)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + FORM, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _fill_form(page, world, "Wrong link %s" % world["tag"])
        page.locator("#cr-solution-id").fill("999999999")
        page.get_by_role("button", name="Submit Change Request").click()
        error = page.locator("#cr-solution-id-error")
        error.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert "No solution with number 999999999 exists in your organisation." in error.inner_text()
        assert page.url.endswith(FORM)
    finally:
        context.close()


@pytest.mark.parametrize("field,key,label", [
    ("#cr-capability-id", "other_capability_id", "capability"),
    ("#cr-solution-id", "other_solution_id", "solution"),
])
def test_another_organisations_number_is_refused_next_to_its_field_and_nothing_is_created(
        browser, live_server, seeded, world, field, key, label):
    title = "Foreign link %s %s" % (label, world["tag"])
    foreign = world[key]
    context = _context(browser, 1440)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + FORM, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _fill_form(page, world, title)
        page.locator(field).fill(str(foreign))
        page.get_by_role("button", name="Submit Change Request").click()
        error = page.locator(field + "-error")
        error.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert ("No %s with number %d exists in your organisation." % (label, foreign)) in error.inner_text()
        assert page.url.endswith(FORM)
        _shot(page, "foreign-" + label, 1440, "light")
    finally:
        context.close()
    from app import create_app
    from app.models.architecture_decision import ArchitectureChangeRequest

    with create_app("testing").app_context():
        assert ArchitectureChangeRequest.query.filter_by(title=title).count() == 0


@pytest.mark.parametrize("width,dark", [(1440, False), (390, True)])
def test_a_viewer_cannot_submit_and_is_told_why(browser, live_server, seeded, world, width, dark):
    title = "Viewer attempt %d %s" % (width, world["tag"])
    context = _context(browser, width, dark)
    page = context.new_page()
    try:
        _login(page, live_server, world["viewer"])
        page.goto(live_server + FORM, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _fill_form(page, world, title)
        page.get_by_role("button", name="Submit Change Request").click()
        alert = page.get_by_role("alert").filter(has_text="does not have write access")
        alert.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert page.url.endswith(FORM)
        _shot(page, "viewer-refused", width, "dark" if dark else "light")
    finally:
        context.close()
    from app import create_app
    from app.models.architecture_decision import ArchitectureChangeRequest

    with create_app("testing").app_context():
        assert ArchitectureChangeRequest.query.filter_by(title=title).count() == 0


def test_a_second_organisation_cannot_see_the_request(browser, live_server, seeded, world):
    title = "Private request %s" % world["tag"]
    context = _context(browser, 1440)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + FORM, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _fill_form(page, world, title)
        page.get_by_role("button", name="Submit Change Request").click()
        page.wait_for_url(re.compile(r".*/arb/change-requests/\d+$"), timeout=PAGE_TIMEOUT)
        own_url = page.url
    finally:
        context.close()

    context = _context(browser, 1440)
    page = context.new_page()
    try:
        _login(page, live_server, world["outsider"])
        page.goto(live_server + LIST, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        text = page.locator("body").inner_text()
        assert title not in text
        assert "No change requests yet" in text
        assert "Raise a new change request to start the Phase H process." in text
        response = page.goto(own_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 404
        assert title not in page.locator("body").inner_text()
    finally:
        context.close()


@pytest.mark.parametrize("width,dark", [(1440, False), (390, True)])
def test_the_raiser_cannot_decide_their_request_but_the_board_can(browser, live_server, seeded, world, width, dark):
    theme = "dark" if dark else "light"
    title = "Board decides %d %s" % (width, world["tag"])
    context = _context(browser, width, dark)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + FORM, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _fill_form(page, world, title)
        page.get_by_role("button", name="Submit Change Request").click()
        page.wait_for_url(re.compile(r".*/arb/change-requests/\d+$"), timeout=PAGE_TIMEOUT)
        detail_url = page.url
        body = page.locator("main, body").first.inner_text()
        # The raiser may assess impact; deciding and closing are not offered to them, and the page says who does.
        assert page.get_by_role("button", name="Assess Impact").count() == 1
        assert page.get_by_role("button", name="Set Disposition").count() == 0
        assert page.get_by_role("button", name="Issue Notice").count() == 0
        assert "The architecture board sets the disposition" in body
        assert "Not decided yet" in body
        _shot(page, "raiser-view", width, theme)
        csrf = page.locator('meta[name="csrf-token"]').get_attribute("content") or ""
        cr_id = detail_url.rsplit("/", 1)[1]
        for path, payload in (("set-disposition", '{"disposition": "accept"}'), ("issue-notice", '{"notice_text": "Done"}')):
            refused = page.request.post(
                live_server + "/arb/api/change-requests/%s/%s" % (cr_id, path),
                headers={"X-CSRFToken": csrf, "Content-Type": "application/json"}, data=payload)
            assert refused.status == 403, (path, refused.status)
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "Not decided yet" in page.locator("body").inner_text()
    finally:
        context.close()

    context = _context(browser, width, dark)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_role("button", name="Set Disposition").count() == 1
        assert page.get_by_role("button", name="Issue Notice").count() == 1
        page.locator("#select-disposition").select_option("incremental_update")
        _shot(page, "board-view", width, theme)
        with page.expect_response(re.compile(r".*/set-disposition$")) as decided:
            page.get_by_role("button", name="Set Disposition").click()
        assert decided.value.status == 200, decided.value.text()
        page.wait_for_timeout(2000)
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        disposition = page.locator("dt", has_text="Disposition").locator("xpath=following-sibling::dd[1]").inner_text()
        assert disposition.strip() == "Incremental Update", disposition
    finally:
        context.close()

"""Connect GitHub to a cited answer, in a real browser (Job 0 of the product).

The person is the administrator of a brand-new organisation. They open Connect GitHub from the
connectors page, paste a read-only token, watch the read finish, review what it found, press
Approve all, find the applications in the inventory with their parts and owner, ask what breaks if
Payments Service fails, and see each answer name the repository and file it rests on. Another
organisation sees none of it, a Viewer is refused the connect page, and the token is in no page
and no response.

GitHub is never called. The recorded organisation of the service tests
(tests/test_github_builds_the_model.py, fixture tests/fixtures/github_acme_org.json) is served to
the server under test by the same github fixture and extend_with_job0 those tests use: a
sitecustomize on the server's import path builds them in the server process the first time it is
asked for a GitHub address. The token below is a plain placeholder.
"""

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TOKEN = "test-token-not-real"
SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
OWNER_EMAIL = "owner.one@example.test"
SHOTS = os.environ.get("JOB0_JOURNEY_SHOTS")

# Runs inside the server process, before the application: every request to api.github.com is
# answered from the recorded organisation by the fixtures the service tests use; anything else
# goes where it would have gone.
_SITECUSTOMIZE = '''
def _install():
    import requests

    original = requests.Session.request
    state = {}

    def dispatch(self, method=None, url=None, *args, **kwargs):
        if isinstance(url, str) and url.startswith("https://api.github.com/"):
            if "fake" not in state:
                from types import SimpleNamespace

                import pytest

                from tests import test_github_builds_the_model as job0
                from tests import test_github_connector as connector

                recorded = connector.github._get_wrapped_function()(SimpleNamespace(config={}), pytest.MonkeyPatch())
                job0.extend_with_job0(recorded.bundle)
                state["fake"] = requests.Session.request
                requests.Session.request = dispatch
            return state["fake"](self, method, url, *args, **kwargs)
        return original(self, method, url, *args, **kwargs)

    requests.Session.request = dispatch


_install()
'''


@pytest.fixture(scope="module")
def github_server(request, app, tmp_path_factory):
    """A second server whose GitHub is the recorded organisation."""
    folder = tmp_path_factory.mktemp("github_peer")
    (folder / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    path = os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)
    return boot_live_server(
        request, None, app, extra_env={"PYTHONPATH": path, "ALLOW_EXTERNAL_API_CALLS": "true"}
    )


@pytest.fixture(scope="module")
def world(github_server, app):
    """Two fresh organisations. In the first: its administrator, a Viewer, and the person the
    code-owner file of Payments Service names."""
    from app import db
    from app.models.user import Role, User

    one = create_fresh_org("solution_architect", org_admin=True, extra_roles=("non_technical_owner",))
    two = create_fresh_org("solution_architect", org_admin=True)
    with app.app_context():
        # The code-owner file names one address, and an address belongs to one person on the whole
        # server: a holder left by an earlier run gives it up.
        for old in User.query.filter_by(email=OWNER_EMAIL).all():
            old.email = "owner.one.%s@example.test" % uuid.uuid4().hex[:8]
        db.session.flush()
        # The Community plan admits three people: the administrator, the Viewer and Payments Service's owner.
        db.session.add(User(first_name="Olive", last_name="One", email=OWNER_EMAIL, organization_id=one["org_id"], confirmed=True))
        viewer = User.query.filter_by(email=one["emails"]["non_technical_owner"]).one()
        viewer.role = Role.query.filter_by(name="Viewer").one()
        db.session.commit()
        db.session.remove()
    seen = {"html": [], "bodies": [], "answer_url": None}
    return {"base": str(github_server), "one": one, "two": two, "seen": seen}


@pytest.fixture(scope="module")
def admin(browser, world):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()

    def keep(response):
        try:
            if response.url.startswith(world["base"]):
                world["seen"]["bodies"].append((response.url, response.body().decode("utf-8", "replace")))
        except Exception:
            pass

    page.on("response", keep)
    sign_in(page, world["base"], world["one"]["emails"]["solution_architect"])
    yield page
    context.close()


def shoot(page, name, reload=True):
    """The screen at the two widths people use, kept for the person who reviews it."""
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height in ((1440, 900), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        if reload:  # the page as it opens at this width, not as it was resized
            page.reload(wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
        page.screenshot(path=os.path.join(SHOTS, "%s-%d.png" % (name, width)), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})


def remember(world, page):
    world["seen"]["html"].append((page.url, page.content()))


# --------------------------------------------------------------------------------------------- #
# The journey, in order                                                                          #
# --------------------------------------------------------------------------------------------- #


def test_1_connect_github_is_a_link_on_the_connectors_page_and_lands_on_content(admin, world):
    admin.goto(world["base"] + "/admin/connectors", wait_until="domcontentloaded")
    link = admin.get_by_test_id("github-connect-link").get_by_role("link", name="Connect GitHub")
    expect(link).to_be_visible()
    link.click()
    admin.wait_for_url("**/admin/connectors/github")
    expect(admin.get_by_role("heading", name="Connect GitHub")).to_be_visible()
    form = admin.get_by_test_id("github-connect-form")
    expect(form.get_by_label("GitHub organisation")).to_be_visible()
    expect(form.get_by_label("Read-only token")).to_be_visible()
    expect(form.get_by_role("button", name="Connect and read my repositories")).to_be_visible()
    expect(admin.get_by_text("Paste a read-only token.", exact=False)).to_be_visible()
    remember(world, admin)
    shoot(admin, "1-connect")


def test_2_the_administrator_pastes_a_token_and_submits(admin, world):
    form = admin.get_by_test_id("github-connect-form")
    form.get_by_label("GitHub organisation").fill("acme")
    form.get_by_label("Read-only token").fill(TOKEN)
    form.get_by_role("button", name="Connect and read my repositories").click()
    expect(admin.locator("#main-content").get_by_text("Connected. Reading your repositories now", exact=False)).to_be_visible()
    expect(admin.get_by_test_id("github-progress")).to_be_visible()
    remember(world, admin)


def test_3_the_status_moves_to_done_with_counts(admin, world):
    line = admin.locator("#github-progress-line")
    expect(line).to_have_text(re.compile(r"Read 16 repositories\. 22 proposals are ready for you to review\."), timeout=PAGE_TIMEOUT)
    # Nothing is in the model until it is approved.
    expect(admin.locator("#github-count-applications")).to_have_text("0")
    remember(world, admin)
    shoot(admin, "3-status")


def test_4_the_review_groups_the_proposals_each_with_a_reason_and_the_owners(admin, world):
    admin.get_by_role("link", name="Review what was found").click()
    admin.wait_for_url("**/admin/connectors/github/review")
    expect(admin.get_by_role("heading", name="Review what GitHub showed")).to_be_visible()
    expect(admin.get_by_test_id("github-review-actions")).to_contain_text(
        "8 applications, 3 parts, 5 links and 6 repositories that stay out of the model."
    )

    apps = admin.locator("section[aria-labelledby^='group-'] tbody tr")
    expect(apps).to_have_count(8)
    parts = admin.locator("section[aria-labelledby='parts-heading'] tbody tr")
    expect(parts).to_have_count(3)
    others = admin.locator("section[aria-labelledby='others-heading'] tbody tr")
    expect(others).to_have_count(6)
    # the "Why" column: second cell of an application, third of a part or an excluded repository
    for rows, column in ((apps, 1), (parts, 2), (others, 2)):
        for i in range(rows.count()):
            reason = rows.nth(i).locator("td").nth(column).inner_text().strip()
            assert reason, "a proposal with no reason: %s" % rows.nth(i).inner_text()

    expect(apps.filter(has_text="Payments Service")).to_contain_text("owner.one@example.test")
    expect(apps.filter(has_text="Customer Portal")).to_contain_text("acme/web-team")
    expect(admin.get_by_role("button", name="Approve all (22)")).to_be_visible()
    remember(world, admin)
    shoot(admin, "4-review")


def test_5_approve_all_adds_everything_to_the_model(admin, world):
    admin.get_by_role("button", name="Approve all (22)").click()
    main = admin.locator("#main-content")
    expect(main.get_by_text("Added 22 items to your model.")).to_be_visible()
    expect(admin.locator("#main-content").get_by_text("Nothing is waiting for review.", exact=False)).to_be_visible()
    remember(world, admin)


def test_6_the_applications_are_listed_and_payments_service_shows_its_parts_and_owner(admin, world):
    admin.goto(world["base"] + "/applications/", wait_until="domcontentloaded")
    table = admin.get_by_role("region", name="Applications table")
    for name in ("Payments Service", "Customer Portal", "Billing Api", "Inventory Service", "Orders Worker", "Mobile App", "Auth", "Gateway"):
        expect(table.get_by_role("row", name=re.compile(r"^Select %s " % name))).to_have_count(1)
    shoot(admin, "6-applications")
    table.get_by_role("row", name=re.compile(r"^Select Payments Service ")).get_by_role("link", name=re.compile("Fact sheet")).click()
    admin.wait_for_load_state("domcontentloaded")
    expect(admin.get_by_role("heading", name="Payments Service").first).to_be_visible()
    expect(admin.get_by_text("Payments Worker").first).to_be_visible()
    expect(admin.get_by_text("Olive One").first).to_be_visible()
    remember(world, admin)
    shoot(admin, "6-application")


def ask_what_breaks_if_payments_service_fails(page, base):
    """On the Ask page, the way a person does: choose the question, type the name, pick it."""
    page.goto(base + "/intelligence/ask", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const el = document.querySelector('[x-data=\"askSurface()\"]'); return !!(el && el._x_dataStack); }"
    )
    page.locator("#ask-question-impact").click()
    box = page.locator("#ask-picker-input")
    expect(box).to_be_focused()
    box.press_sequentially("Payments Service", delay=15)
    page.locator("#ask-picker-listbox [role=option]", has_text="Payments Service").first.click()
    rows = page.locator("[data-ask-row]")
    expect(rows.first).to_be_visible()
    return rows


def test_7_asking_what_breaks_names_the_dependents(admin, world):
    rows = ask_what_breaks_if_payments_service_fails(admin, world["base"])
    for name in ("Billing Api", "Customer Portal", "Payments Worker"):
        expect(rows.filter(has_text=name).first).to_be_visible()
    remember(world, admin)
    shoot(admin, "7-answer", reload=False)


def test_7a_the_saved_answer_names_them_at_its_own_address(admin, world):
    admin.goto(world["base"] + "/intelligence/answers", wait_until="domcontentloaded")
    admin.wait_for_function(
        "() => { const el = document.querySelector('[x-data=\"answersSurface()\"]'); return !!(el && el._x_dataStack); }"
    )
    admin.locator("#answers-picker-input").press_sequentially("Payments Service", delay=15)
    admin.locator("#answers-picker-listbox [role=option]", has_text="Payments Service").first.click()
    admin.locator("#answers-ask-button").click()
    admin.wait_for_url("**/intelligence/answers/*")
    world["seen"]["answer_url"] = admin.url
    rows = admin.locator("[data-answer-rows]")
    for name in ("Billing Api", "Customer Portal", "Payments Worker"):
        expect(rows).to_contain_text(name)
    remember(world, admin)


@pytest.mark.xfail(strict=True, reason="The answer names what breaks but not the repository and file each link was read from")
def test_7b_each_dependent_is_cited_with_its_repository_and_file(admin, world):
    rows = ask_what_breaks_if_payments_service_fails(admin, world["base"])
    billing = rows.filter(has_text="Billing Api").first.inner_text() + admin.locator("#ask-results").inner_text()
    assert "acme/billing-api" in billing and "pom.xml" in billing
    portal = admin.locator("#ask-results").inner_text()
    assert "acme/customer-portal" in portal and "package.json" in portal


@pytest.mark.xfail(strict=True, reason="The answer offers no link from a citation to the repository file it came from")
def test_7c_a_citation_link_opens_on_content(admin, world):
    ask_what_breaks_if_payments_service_fails(admin, world["base"])
    link = admin.locator("#ask-results a[href*='github.com'], #ask-results [data-citation] a").first
    expect(link).to_be_visible(timeout=3000)
    link.click()
    assert admin.locator("body").inner_text().strip()


def test_8_another_organisation_sees_none_of_it(browser, world):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, world["base"], world["two"]["emails"]["solution_architect"])
        page.goto(world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
        expect(page.get_by_role("button", name="Connect and read my repositories")).to_be_visible()
        expect(page.get_by_test_id("github-progress")).to_have_count(0)
        page.goto(world["base"] + "/admin/connectors/github/review", wait_until="domcontentloaded")
        expect(page.locator("#main-content").get_by_text("Nothing is waiting for review.", exact=False)).to_be_visible()
        page.goto(world["base"] + "/applications/", wait_until="domcontentloaded")
        for name in ("Payments Service", "Customer Portal", "Billing Api"):
            expect(page.get_by_text(name)).to_have_count(0)
        page.goto(world["base"] + "/intelligence/answers", wait_until="domcontentloaded")
        expect(page.locator("[data-answer-item]")).to_have_count(0)
        response = page.goto(world["seen"]["answer_url"], wait_until="domcontentloaded")
        assert response.status == 404
        assert "Payments Service" not in page.content()
    finally:
        context.close()


def test_9_the_token_is_in_no_page_and_no_response(world):
    seen = world["seen"]
    assert seen["html"] and seen["bodies"], "nothing was collected"
    for url, html in seen["html"]:
        assert TOKEN not in html, "the token is in the page at %s" % url
    for url, body in seen["bodies"]:
        assert TOKEN not in body, "the token is in the response from %s" % url


def test_10_a_viewer_is_refused_the_connect_page(browser, world):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, world["base"], world["one"]["emails"]["non_technical_owner"])
        for path in ("/admin/connectors/github", "/admin/connectors/github/review"):
            response = page.goto(world["base"] + path, wait_until="domcontentloaded")
            assert response.status == 403, "%s answered %s" % (path, response.status)
            assert page.get_by_label("Read-only token").count() == 0
            assert "Payments Service" not in page.content()
    finally:
        context.close()

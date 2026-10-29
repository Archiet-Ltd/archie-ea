"""An administrator connects a personal GitHub account and reviews the proposals.

The person is the administrator of a brand-new organisation. They open Connect GitHub from the
connectors page, paste a read-only token for a personal account, watch the read finish, review
what it found, and see both repositories as proposals. Another organisation sees none of it.

GitHub is never called. The recorded personal account (User type) with two repositories is served
to the server under test by the same github fixture pattern the service tests use.
"""

import json
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.test_connect_github_to_cited_answer_journey import remove_organisations

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TOKEN = "test-token-not-real"
SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
SHOTS = os.environ.get("JOB0_JOURNEY_SHOTS")

# Runs inside the server process, before the application: every request to api.github.com is
# answered from the recorded personal account (User type with two repositories); anything else
# goes where it would have gone.
_SITECUSTOMIZE = '''
def _install():
    import requests
    import json

    original = requests.Session.request
    API = "https://api.github.com/"
    state = {}

    # Personal account fixture bundle (User type with two repositories)
    BUNDLE = {
        "users/octocat": {"type": "User", "login": "octocat"},
        "users/octocat/repos": [
            {
                "archived": False,
                "created_at": "2022-01-04T09:00:00Z",
                "default_branch": "main",
                "description": "Personal payments service",
                "disabled": False,
                "fork": False,
                "full_name": "octocat/payments-service",
                "html_url": "https://github.com/octocat/payments-service",
                "id": 8001,
                "is_template": False,
                "language": "Python",
                "name": "payments-service",
                "node_id": "R_8001",
                "open_issues_count": 3,
                "owner": {"id": 2, "login": "octocat", "type": "User"},
                "private": True,
                "pushed_at": "2026-10-10T09:15:00Z",
                "size": 1200,
                "topics": [],
                "updated_at": "2026-10-10T09:15:00Z",
                "visibility": "private",
            },
            {
                "archived": False,
                "created_at": "2022-01-04T09:00:00Z",
                "default_branch": "main",
                "description": "Personal customer portal",
                "disabled": False,
                "fork": False,
                "full_name": "octocat/customer-portal",
                "html_url": "https://github.com/octocat/customer-portal",
                "id": 8002,
                "is_template": False,
                "language": "TypeScript",
                "name": "customer-portal",
                "node_id": "R_8002",
                "open_issues_count": 3,
                "owner": {"id": 2, "login": "octocat", "type": "User"},
                "private": True,
                "pushed_at": "2026-10-12T14:02:00Z",
                "size": 1200,
                "topics": [],
                "updated_at": "2026-10-12T14:02:00Z",
                "visibility": "private",
            },
        ],
        "repos/octocat/payments-service/contents": [
            {"name": "Dockerfile", "path": "Dockerfile", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "file"},
            {"name": "pyproject.toml", "path": "pyproject.toml", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "file"},
            {"name": "src", "path": "src", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "dir"},
            {"name": "README.md", "path": "README.md", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "file"},
        ],
        "repos/octocat/customer-portal/contents": [
            {"name": "Dockerfile", "path": "Dockerfile", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "file"},
            {"name": "package.json", "path": "package.json", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "file"},
            {"name": "src", "path": "src", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "dir"},
            {"name": "README.md", "path": "README.md", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "type": "file"},
        ],
        "users/octocat/teams": [],
    }

    class _Resp:
        def __init__(self, status, body, headers=None):
            self.status_code = status
            self._body = body
            self.headers = {"X-RateLimit-Remaining": "4990", **(headers or {})}
            self.reason = {200: "OK", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 500: "Server Error"}.get(status, "Status")
            self.text = json.dumps(body)
            self.is_redirect = 300 <= status < 400
        def json(self):
            return self._body

    def dispatch(self, method=None, url=None, params=None, headers=None, **kw):
        if isinstance(url, str) and url.startswith(API):
            path = url[len(API):].strip("/")
            params = dict(params or {})
            page = int(params.get("page", 1))
            if path in BUNDLE:
                body = BUNDLE[path]
                if path.endswith("/repos") or path.endswith("/teams"):
                    per_page = int(params.get("per_page", 30))
                    body = body[(page - 1) * per_page : page * per_page]
                return _Resp(200, body)
            return _Resp(404, {"message": "Not Found"})
        return original(self, method, url, params, headers, **kw)

    requests.Session.request = dispatch


_install()
'''


@pytest.fixture(scope="module")
def github_personal_server(request, app, tmp_path_factory):
    """A server whose GitHub is the recorded personal account."""
    folder = tmp_path_factory.mktemp("github_personal")
    (folder / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    path = os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)
    return boot_live_server(
        request, None, app, extra_env={"PYTHONPATH": path, "ALLOW_EXTERNAL_API_CALLS": "true"}
    )


@pytest.fixture(scope="module")
def world(github_personal_server, app):
    """A fresh organisation with its administrator."""
    from app import db
    from app.models.user import Role, User

    one = create_fresh_org("solution_architect", org_admin=True)
    with app.app_context():
        db.session.remove()
    seen = {"html": [], "bodies": [], "answer_url": None}
    yield {"base": str(github_personal_server), "one": one, "seen": seen}


def shoot(page, name, reload=True):
    """The screen at the two widths people use, kept for the person who reviews it."""
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height in ((1440, 900), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        if reload:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
        page.screenshot(path=os.path.join(SHOTS, "%s-%d.png" % (name, width)), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})


def remember(world, page):
    world["seen"]["html"].append((page.url, page.content()))


# --------------------------------------------------------------------------------------------- #
# The journey, in order                                                                          #
# --------------------------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def admin(browser, world):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
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


def test_1_connect_github_page_shows_organisation_or_account_label(admin, world):
    admin.goto(world["base"] + "/admin/connectors", wait_until="domcontentloaded")
    link = admin.get_by_test_id("github-connect-link").get_by_role("link", name="Connect GitHub")
    expect(link).to_be_visible()
    link.click()
    admin.wait_for_url("**/admin/connectors/github")
    expect(admin.get_by_role("heading", name="Connect GitHub", exact=True)).to_be_visible()
    form = admin.get_by_test_id("github-connect-form")
    expect(form.get_by_label("GitHub organisation or account")).to_be_visible()
    expect(form.get_by_label("Read-only token")).to_be_visible()
    expect(form.get_by_role("button", name="Connect and read my repositories")).to_be_visible()
    expect(admin.get_by_text("Paste a read-only token.", exact=False)).to_be_visible()
    # The help text mentions organisation or personal account
    expect(admin.get_by_text("organisation or personal account", exact=False)).to_be_visible()
    remember(world, admin)
    shoot(admin, "1-connect")


def test_2_the_administrator_pastes_a_personal_account_name_and_token(admin, world):
    form = admin.get_by_test_id("github-connect-form")
    form.get_by_label("GitHub organisation or account").fill("octocat")
    form.get_by_label("Read-only token").fill(TOKEN)
    form.get_by_role("button", name="Connect and read my repositories").click()
    expect(admin.locator("#main-content").get_by_text("Connected. Reading your repositories now", exact=False)).to_be_visible()
    expect(admin.get_by_test_id("github-progress")).to_be_visible()
    remember(world, admin)


def test_3_the_status_moves_to_done_with_counts(admin, world):
    line = admin.locator("#github-progress-line")
    expect(line).to_have_text(re.compile(r"Read 2 repositories\. 2 proposals are ready for you to review\."), timeout=PAGE_TIMEOUT)
    # Nothing is in the model until it is approved.
    expect(admin.locator("#github-count-applications")).to_have_text("0")
    remember(world, admin)
    shoot(admin, "3-status")


def test_4_the_review_shows_both_repositories_as_proposals(admin, world):
    admin.get_by_role("link", name="Review what was found").click()
    admin.wait_for_url("**/admin/connectors/github/review")
    expect(admin.get_by_role("heading", name="Review what GitHub showed")).to_be_visible()
    expect(admin.get_by_test_id("github-review-actions")).to_contain_text(
        "2 applications, 0 parts, 0 links and 0 repositories that stay out of the model."
    )

    apps = admin.locator("section[aria-labelledby^='group-'] tbody tr")
    expect(apps).to_have_count(2)
    # The "Why" column: second cell of an application
    for i in range(apps.count()):
        reason = apps.nth(i).locator("td").nth(1).inner_text().strip()
        assert reason, "a proposal with no reason: %s" % apps.nth(i).inner_text()

    expect(apps.filter(has_text="Payments Service")).to_be_visible()
    expect(apps.filter(has_text="Customer Portal")).to_be_visible()
    expect(admin.get_by_role("button", name="Approve all (2)")).to_be_visible()
    remember(world, admin)
    shoot(admin, "4-review")

    # On a phone every proposal still shows its name, reason, owner and source, with no sideways scroll.
    admin.set_viewport_size({"width": 390, "height": 844})
    try:
        admin.reload(wait_until="domcontentloaded")
        overflow = admin.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, "the review page scrolls sideways by %spx at 390 wide" % overflow
        rows = admin.locator("section[aria-labelledby^='group-'] tbody tr")
        for i in range(rows.count()):
            cells = rows.nth(i).locator("td")
            for index, label in ((0, "name"), (1, "reason"), (2, "owner"), (3, "source")):
                box = cells.nth(index).bounding_box()
                assert box and cells.nth(index).is_visible() and box["width"] > 200, "the %s of row %s is clipped at 390 wide" % (label, i)
                assert cells.nth(index).inner_text().strip(), "the %s of row %s is empty at 390 wide" % (label, i)
    finally:
        admin.set_viewport_size({"width": 1440, "height": 900})


def test_5_approve_all_adds_everything_to_the_model(admin, world):
    admin.get_by_role("button", name="Approve all (2)").click()
    main = admin.locator("#main-content")
    expect(main.get_by_text("Added 2 items to your model.")).to_be_visible()
    expect(admin.locator("#main-content").get_by_text("Nothing is waiting for review.", exact=False)).to_be_visible()
    remember(world, admin)


def test_6_the_applications_are_listed(admin, world):
    admin.goto(world["base"] + "/applications/", wait_until="domcontentloaded")
    table = admin.get_by_role("region", name="Applications table")
    for name in ("Payments Service", "Customer Portal"):
        expect(table.get_by_role("row", name=re.compile(r"^Select %s " % name))).to_have_count(1)
    rows = table.locator("tbody tr")
    assert rows.count() == 2
    remember(world, admin)
    shoot(admin, "6-applications")


def test_7_another_organisation_sees_none_of_it(browser, world, app):
    from tests.smoke.fresh_org import create_fresh_org, sign_in
    from app import db
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()
        org = Organization(name="Other Org %s" % suffix, slug="other-org-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        org_id = org.id
        email = "other.%s@example.com" % suffix
        user = User(
            email=email, first_name="Other", last_name="User",
            organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
        )
        user.role = architect_role
        user.is_org_admin = True
        user.password = PASSWORD
        db.session.add(user)
        db.session.flush()
        OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
        db.session.commit()
        db.session.remove()

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, world["base"], email)
        page.goto(world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
        expect(page.get_by_role("button", name="Connect and read my repositories")).to_be_visible()
        expect(page.get_by_test_id("github-progress")).to_have_count(0)
        page.goto(world["base"] + "/admin/connectors/github/review", wait_until="domcontentloaded")
        expect(page.locator("#main-content").get_by_text("Nothing is waiting for review.", exact=False)).to_be_visible()
        page.goto(world["base"] + "/applications/", wait_until="domcontentloaded")
        for name in ("Payments Service", "Customer Portal"):
            expect(page.get_by_text(name)).to_have_count(0)
    finally:
        context.close()
        # Clean up the other organisation properly
        remove_organisations(app, [org_id])


def test_8_the_token_is_in_no_page_and_no_response(world):
    seen = world["seen"]
    assert seen["html"] and seen["bodies"], "nothing was collected"
    for url, html in seen["html"]:
        assert TOKEN not in html, "the token is in the page at %s" % url
    for url, body in seen["bodies"]:
        assert TOKEN not in body, "the token is in the response from %s" % url
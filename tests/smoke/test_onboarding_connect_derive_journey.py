"""Connect GitHub, watch the model being built, and reach a cited fact — in a real browser.

A startup CTO in a brand-new organisation connects GitHub from the connectors page, sees a
progress line that names what was read ("16 repositories, 8 services found"), reviews what the
read proposed, approves it, asks what breaks if one service fails, and opens a fact that lands on
a page showing the repository file it was read from and the date it was read. The two other ways
to start — import from a spreadsheet, try with sample data — are on the connect page from the
first moment, before any failure, and each lands on a page with content. A bad token, a token
that can see no repositories, and an organisation whose repositories have no links between them
each show a plain line and a next action, never a blank or an unexplained zero. A second
organisation's read is its own; neither sees the other's.

GitHub is never called. A sitecustomize on the server's import path answers every request to
api.github.com from the recorded organisations the connector's own tests use (the acme
organisation of tests/test_github_builds_the_model.py, plus a second organisation with
repositories and no links, and a third whose token is refused). The token below is a placeholder.
"""

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TOKEN = "connect-derive-placeholder-token"
BAD_TOKEN = "refused-placeholder-token"
SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
# The address the Payments Service code-owner file of the recorded organisation names; the
# review can show a person as the owner only when this address belongs to one user of the org.
OWNER_EMAIL = "owner.one@example.test"
SHOTS = os.environ.get("ONB01_JOURNEY_SHOTS")

# Runs inside the server process, before the application. Every request to api.github.com is
# answered from a recorded organisation: acme (the connector suite's fixture, extended with the
# job0 section that names the links), a second organisation "beacon" whose repositories hold code
# but no dependency manifest, docker-compose or deploy workflow — so services are found and no
# links are — and a third organisation "refused" whose listing answers 401. Anything else goes
# where it would have gone.
_SITECUSTOMIZE = '''
import base64
import json


def _beacon_bundle():
    """A second organisation: repositories with code and no links between them."""
    def repo(idx, name, language):
        stamp = "2026-02-%02dT00:00:00Z" % (idx + 1)
        return {
            "id": 90000 + idx,
            "name": name,
            "full_name": "beacon/" + name,
            "html_url": "https://github.com/beacon/" + name,
            "node_id": "R_beacon_%d" % idx,
            "description": name.replace("-", " "),
            "language": language,
            "pushed_at": stamp,
            "updated_at": stamp,
            "archived": False,
            "fork": False,
            "is_template": False,
            "topics": [],
            "size": 1200,
            "default_branch": "main",
            "permissions": {},
        }

    names = [("beacon-ledger", "Python"), ("beacon-portal", "TypeScript"),
             ("beacon-scheduler", "Go"), ("beacon-reports", "Python")]
    repos = [repo(i, n, lang) for i, (n, lang) in enumerate(names)]
    bundle = {"orgs/beacon/repos": repos, "orgs/beacon/teams": []}
    for r in repos:
        full = r["full_name"]
        # A plain source tree: a src folder and a readme, nothing that names another repository.
        bundle["repos/%s/contents" % full] = [
            {"name": "src", "path": "src", "type": "dir", "sha": "a" * 40},
            {"name": "README.md", "path": "README.md", "type": "file", "sha": "a" * 40},
        ]
        bundle["repos/%s/contents/src" % full] = [
            {"name": "main." + ("py" if r["language"] == "Python" else "txt"),
             "path": "src/main", "type": "file", "sha": "a" * 40},
        ]
        bundle["repos/%s/commits/main" % full] = {"sha": ("%08x" % r["id"]) + "c0de" * 8}
    return bundle


def _install():
    import sys

    import requests

    original = requests.Session.request
    state = {}

    def ensure_bundle():
        if "bundle" not in state:
            from types import SimpleNamespace

            import pytest

            from tests import test_github_builds_the_model as job0
            from tests import test_github_connector as connector

            # Building the recorded responses runs the connector suite's own ``github``
            # fixture, which monkeypatches requests.Session.request to its acme-only
            # server. Capture that bundle, then put this module's dispatch (which also
            # serves beacon and refused) back in place — otherwise the fixture's patch
            # would shadow it and every beacon path would answer 404.
            recorded = connector.github._get_wrapped_function()(SimpleNamespace(config={}), pytest.MonkeyPatch())
            job0.extend_with_job0(recorded.bundle)
            bundle = dict(recorded.bundle)
            bundle.update(_beacon_bundle())
            state["bundle"] = bundle
            requests.Session.request = dispatch
        return state["bundle"]

    class Resp:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body
            self.headers = {"X-RateLimit-Remaining": "4990"}
            self.reason = {200: "OK", 401: "Unauthorized", 404: "Not Found"}.get(status, "Status")
            self.text = json.dumps(body)
            self.is_redirect = False

        def json(self):
            return self._body

    API = "https://api.github.com/"

    def dispatch(self, method=None, url=None, params=None, headers=None, **kwargs):
        if not (isinstance(url, str) and url.startswith(API)):
            return original(self, method, url, params=params, headers=headers, **kwargs)
        path = url[len(API):].strip("/")
        # Build the recorded world on first contact, for every organisation, so which
        # organisation is read first never changes what the others can see.
        bundle = ensure_bundle()
        if path.startswith("orgs/refused") or path.startswith("repos/refused"):
            return Resp(401, {"message": "Bad credentials"})
        params = dict(params or {})
        page = int(params.get("page", 1))
        if path not in bundle:
            print("GITHUB-FAKE-MISS:", path, file=sys.stderr, flush=True)
            return Resp(404, {"message": "Not Found"})
        body = bundle[path]
        if path.endswith("/repos") or path.endswith("/teams"):
            per_page = int(params.get("per_page", 30))
            body = body[(page - 1) * per_page : page * per_page]
        return Resp(200, body)

    requests.Session.request = dispatch


_install()
'''


@pytest.fixture(scope="module")
def github_server(request, app, tmp_path_factory):
    folder = tmp_path_factory.mktemp("onb01_github")
    (folder / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    path = os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)
    return boot_live_server(
        request, None, app, extra_env={"PYTHONPATH": path, "ALLOW_EXTERNAL_API_CALLS": "true"}
    )


@pytest.fixture(scope="module")
def world(github_server, app):
    """Two fresh organisations, each with its own administrator. The first also holds the person
    the Payments Service code-owner file names, so the review can show a real owner."""
    from app import db
    from app.models.user import User

    one = create_fresh_org("cto", org_admin=True)
    two = create_fresh_org("cto", org_admin=True)
    with app.app_context():
        for old in User.query.filter_by(email=OWNER_EMAIL).all():
            old.email = "derive.owner.%s@example.test" % uuid.uuid4().hex[:8]
        db.session.flush()
        db.session.add(User(first_name="Robin", last_name="Reed", email=OWNER_EMAIL,
                            organization_id=one["org_id"], confirmed=True))
        db.session.commit()
        # The sample-data exit points at the Lantern Quay demonstration organisation,
        # which the product ships seeded; seed it here so the exit lands on content
        # rather than the not-seeded placeholder. Idempotent.
        from app.commands.seed_demo_company import seed_demo_company

        seed_demo_company()
        db.session.commit()
        db.session.remove()
    from tests.smoke.test_connect_github_to_cited_answer_journey import remove_organisations

    seen = {"html": [], "bodies": []}
    yield {"base": str(github_server), "one": one, "two": two, "seen": seen}
    remove_organisations(app, [one["org_id"], two["org_id"]])


def _context(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    return context


@pytest.fixture(scope="module")
def admin(browser, world):
    context = _context(browser)
    page = context.new_page()

    def keep(response):
        try:
            if response.url.startswith(world["base"]):
                world["seen"]["bodies"].append((response.url, response.body().decode("utf-8", "replace")))
        except Exception:
            pass

    page.on("response", keep)
    sign_in(page, world["base"], world["one"]["emails"]["cto"])
    yield page
    context.close()


def shoot(page, name, reload=True):
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


def _connect(page, base, organization, token):
    page.goto(base + "/admin/connectors/github", wait_until="domcontentloaded")
    form = page.get_by_test_id("github-connect-form")
    form.get_by_label("GitHub organisation").fill(organization)
    form.get_by_label("Read-only token").fill(token)
    form.get_by_role("button", name=re.compile("Connect and read my repositories|Save and read again")).click()


# --------------------------------------------------------------------------------------------- #
# The journey, in order                                                                          #
# --------------------------------------------------------------------------------------------- #


def test_1_the_connect_page_offers_the_two_other_ways_to_start_from_the_first_moment(admin, world):
    admin.goto(world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
    expect(admin.get_by_role("heading", name="Connect GitHub")).to_be_visible()
    form = admin.get_by_test_id("github-connect-form")
    expect(form.get_by_label("GitHub organisation")).to_be_visible()
    expect(form.get_by_label("Read-only token")).to_be_visible()
    # Both exits are present before anything has been connected or failed.
    import_exit = admin.get_by_test_id("github-exit-import")
    sample_exit = admin.get_by_test_id("github-exit-sample")
    expect(import_exit).to_be_visible()
    expect(sample_exit).to_be_visible()
    expect(admin.get_by_test_id("github-progress")).to_have_count(0)
    remember(world, admin)
    shoot(admin, "1-connect")


def test_2_the_spreadsheet_import_exit_lands_on_a_page_with_content(admin, world):
    admin.goto(world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
    admin.get_by_test_id("github-exit-import").click()
    admin.wait_for_load_state("domcontentloaded")
    body = admin.locator("#main-content").inner_text().strip()
    assert body, "the import exit landed on an empty page"
    expect(admin.get_by_role("heading", name=re.compile("Application", re.I)).first).to_be_visible()


def test_3_the_sample_data_exit_lands_on_a_page_with_content(admin, world):
    admin.goto(world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
    href = admin.get_by_test_id("github-exit-sample").get_attribute("href")
    response = admin.goto(world["base"] + href, wait_until="domcontentloaded")
    assert response.status == 200, "the sample-data exit answered %s" % response.status
    body = admin.locator("#main-content, body").first.inner_text().strip()
    assert body and len(body) > 200, "the sample-data exit landed on an empty page"
    # The sample company describes a real estate, not a placeholder.
    assert "Lantern Quay Systems" in admin.content()


def test_4_a_bad_token_shows_the_plain_line_and_how_to_fix_it(admin, world):
    _connect(admin, world["base"], "refused", BAD_TOKEN)
    line = admin.locator("#github-progress-line")
    expect(line).to_have_text(
        re.compile(r"GitHub did not accept that token\. It needs read access to repositories\."),
        timeout=PAGE_TIMEOUT,
    )
    # A next action: how to create a token that works.
    expect(line).to_contain_text("fine-grained token")
    # No bare dash or unexplained zero stands in for the failure.
    assert "—" not in line.inner_text()
    remember(world, admin)
    shoot(admin, "4-bad-token")


def test_5_connecting_names_the_services_found_and_reaches_the_model(admin, world):
    _connect(admin, world["base"], "acme", TOKEN)
    expect(admin.locator("#main-content").get_by_text("Connected. Reading your repositories now", exact=False)).to_be_visible()
    expect(admin.get_by_test_id("github-progress")).to_be_visible()
    line = admin.locator("#github-progress-line")
    expect(line).to_have_text(
        re.compile(r"Read 16 repositories, 8 services found\. 22 proposals are ready for you to review\."),
        timeout=PAGE_TIMEOUT,
    )
    # The named counts are real, and nothing is in the model until it is approved.
    expect(admin.locator("#github-count-repositories")).to_have_text("16")
    expect(admin.locator("#github-count-services")).to_have_text("8")
    expect(admin.locator("#github-count-links")).to_have_text("5")
    expect(admin.locator("#github-count-applications")).to_have_text("0")
    remember(world, admin)
    shoot(admin, "5-progress")


def test_6_the_review_shows_a_reason_and_an_owner_then_approve_all_builds_the_model(admin, world):
    admin.get_by_role("link", name="Review what was found").click()
    admin.wait_for_url("**/admin/connectors/github/review")
    apps = admin.locator("section[aria-labelledby^='group-'] tbody tr")
    expect(apps).to_have_count(8)
    expect(apps.filter(has_text="Payments Service")).to_contain_text(OWNER_EMAIL)
    admin.get_by_role("button", name="Approve all (22)").click()
    expect(admin.locator("#main-content").get_by_text("Added 22 items to your model.")).to_be_visible()
    remember(world, admin)
    shoot(admin, "6-review")


def _ask_impact(page, base, name):
    page.goto(base + "/intelligence/ask", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const el = document.querySelector('[x-data=\"askSurface()\"]'); return !!(el && el._x_dataStack); }"
    )
    page.locator("#ask-question-impact").click()
    box = page.locator("#ask-picker-input")
    expect(box).to_be_focused()
    box.press_sequentially(name, delay=15)
    page.locator("#ask-picker-listbox [role=option]", has_text=name).first.click()
    rows = page.locator("[data-ask-row]")
    expect(rows.first).to_be_visible()
    return rows


def test_7_opening_a_fact_lands_on_its_repository_file_and_the_date_it_was_read(admin, world):
    rows = _ask_impact(admin, world["base"], "Payments Service")
    billing = rows.filter(has_text="Billing Api").first
    expect(billing.locator("[data-citation]").first).to_be_visible()
    text = billing.inner_text()
    # The fact names the repository and the file it was read from.
    assert "acme/billing-api" in text and "pom.xml" in text, text
    assert re.search(r"@ [0-9a-f]{7}(?![0-9a-f])", text), "no short commit in: " + text
    # The citation link is exactly the file at the commit — never a guessed address.
    link = billing.get_by_role("link", name="View file on GitHub").first
    href = link.get_attribute("href")
    assert re.fullmatch(r"https://github\.com/acme/billing-api/blob/[0-9a-f]{40}/pom\.xml", href), href
    # Opening the Why drawer lands on the record of where the fact was read: the repository
    # it came from and the date it was read, never a blank or "not recorded".
    billing.get_by_role("button", name="Why?").click()
    dialog = admin.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")
    section = dialog.locator("[data-provenance-record]")
    expect(section).to_be_visible()
    assert "acme/billing-api" in section.inner_text(), section.inner_text()
    date_line = section.locator("[data-provenance-date]").first
    expect(date_line).to_contain_text(re.compile(r"Recorded\s+\d"))
    assert "not recorded" not in section.inner_text().lower(), section.inner_text()
    remember(world, admin)
    shoot(admin, "7-fact", reload=False)


def test_8_a_second_organisation_with_no_links_is_told_so_plainly(browser, world):
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, world["base"], world["two"]["emails"]["cto"])
        _connect(page, world["base"], "beacon", TOKEN)
        line = page.locator("#github-progress-line")
        expect(line).to_have_text(
            re.compile(r"We found 4 repositories and no links between them yet\."),
            timeout=PAGE_TIMEOUT,
        )
        # The services it did find are named, and a next action is offered — never a bare zero.
        expect(page.locator("#github-count-repositories")).to_have_text("4")
        expect(page.locator("#github-count-services")).to_have_text("4")
        expect(page.locator("#github-count-links")).to_have_text("0")
        expect(line).to_contain_text("4 services are ready to review")
        # The first organisation's repositories are not visible here.
        page.goto(world["base"] + "/admin/connectors/github/review", wait_until="domcontentloaded")
        for name in ("Payments Service", "Billing Api", "Customer Portal"):
            expect(page.get_by_text(name)).to_have_count(0)
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            page.goto(world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
            page.screenshot(path=os.path.join(SHOTS, "8-no-links-1440.png"), full_page=True)
    finally:
        context.close()


def test_9_the_first_organisation_still_holds_only_its_own_model(admin, world):
    admin.goto(world["base"] + "/admin/connectors/github/review", wait_until="domcontentloaded")
    for name in ("beacon-ledger", "beacon-portal", "beacon-scheduler"):
        expect(admin.get_by_text(name)).to_have_count(0)


def test_10_the_token_is_in_no_page_and_no_response(world):
    seen = world["seen"]
    assert seen["html"] and seen["bodies"], "nothing was collected"
    for token in (TOKEN, BAD_TOKEN):
        for url, html in seen["html"]:
            assert token not in html, "a token is in the page at %s" % url
        for url, body in seen["bodies"]:
            assert token not in body, "a token is in the response from %s" % url

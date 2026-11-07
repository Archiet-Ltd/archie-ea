"""What each application runs on, read from GitHub, in a real browser.

A solution architect of a new organisation connects GitHub, reviews what was found (two applications
whose docker-compose files run Postgres, one of them Redis as well), approves it, opens the Postgres
element and sees both applications with the file that says so, then asks the chat what breaks if
Postgres fails and sees both applications and a citation that opens on the file at its commit. A second
organisation that connects the same repositories gets a Postgres element of its own and sees nothing
of the first one's.

GitHub is never called: the recorded organisation of the connector tests is served to the server under
test, with the two compose files of this journey laid over it. The token is a plain placeholder.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from scripts.capture.github_fixture import SITECUSTOMIZE as _BASE_SITECUSTOMIZE
from tests.smoke.chat_ask import ask
from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.test_connect_github_to_cited_answer_journey import remove_organisations

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TOKEN = "test-token-not-real"
SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
SHOTS = os.environ.get("TECHDEP_JOURNEY_SHOTS")

_SITECUSTOMIZE = _BASE_SITECUSTOMIZE.replace(
    "job0.extend_with_job0(recorded.bundle)",
    "job0.extend_with_job0(recorded.bundle)\n"
    "                from tests import test_github_technology_dependency as technology\n"
    "                technology._put_compose(recorded.bundle, 'customer-portal', technology.PORTAL_COMPOSE)\n"
    "                technology._put_compose(recorded.bundle, 'payments-service', technology.PAYMENTS_COMPOSE)",
)
assert "technology._put_compose" in _SITECUSTOMIZE


@pytest.fixture(scope="module")
def github_server(request, app, tmp_path_factory):
    folder = tmp_path_factory.mktemp("github_technology_peer")
    (folder / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    path = os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)
    return boot_live_server(request, None, app, extra_env={"PYTHONPATH": path, "ALLOW_EXTERNAL_API_CALLS": "true"})


@pytest.fixture(scope="module")
def world(github_server, app):
    one = create_fresh_org("solution_architect", org_admin=True)
    two = create_fresh_org("solution_architect", org_admin=True)
    yield {"base": str(github_server), "one": one, "two": two}
    remove_organisations(app, [one["org_id"], two["org_id"]])


def new_page(browser, world, who, width=1440, height=900, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, world["base"], world[who]["emails"]["solution_architect"])
    return context, page


@pytest.fixture(scope="module")
def architect(browser, world):
    context, page = new_page(browser, world, "one")
    yield page
    context.close()


def shoot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name), full_page=True)


def connect_and_read(page, world):
    page.goto(world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
    form = page.get_by_test_id("github-connect-form")
    form.get_by_label("GitHub organisation").fill("acme")
    form.get_by_label("Read-only token").fill(TOKEN)
    form.get_by_role("button", name="Connect and read my repositories").click()
    expect(page.locator("#github-progress-line")).to_have_text(re.compile(r"Read 16 repositories\."), timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name="Review what was found").click()
    page.wait_for_url("**/admin/connectors/github/review")


def approve_everything(page):
    page.get_by_role("button", name=re.compile(r"^Approve all \(\d+\)$")).click()
    expect(page.locator("#main-content").get_by_text("Nothing is waiting for review.", exact=False)).to_be_visible()


def test_1_the_review_lists_what_each_application_runs_on_with_the_line_that_says_so(architect, world):
    connect_and_read(architect, world)
    links = architect.locator("section[aria-labelledby='links-heading'] tbody tr")
    postgres = links.filter(has_text="Postgres")
    expect(postgres).to_have_count(2)
    expect(postgres.filter(has_text="Customer Portal")).to_contain_text("docker-compose.yml, line 5")
    expect(postgres.filter(has_text="Payments Service")).to_contain_text("docker-compose.yml, line 6")
    expect(postgres.filter(has_text="Customer Portal")).to_contain_text("postgres:16")
    expect(links.filter(has_text="Redis")).to_have_count(1)
    expect(links.filter(has_text="Redis")).to_contain_text("Customer Portal")
    shoot(architect, "review-1440.png")


def test_2_approving_puts_postgres_in_the_model(architect, world):
    approve_everything(architect)
    architect.goto(world["base"] + "/architecture/dashboard?layer=technology", wait_until="domcontentloaded")
    architect.wait_for_load_state("networkidle")
    shoot(architect, "technology-layer-1440.png")


def open_postgres(page, world):
    """From the technology layer of the architecture workspace, the way a person does."""
    page.goto(world["base"] + "/architecture/dashboard?layer=technology", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name="View full detail for Postgres").click()
    page.wait_for_load_state("domcontentloaded")


def test_3_the_postgres_page_shows_both_applications_with_their_files(architect, world):
    open_postgres(architect, world)
    expect(architect.get_by_role("heading", name="Postgres").first).to_be_visible()
    rows = architect.get_by_test_id("connection-row").filter(has=architect.locator("[data-testid='connection-origin']"))
    expect(rows).to_have_count(2)
    portal = rows.filter(has_text="Customer Portal")
    payments = rows.filter(has_text="Payments Service")
    expect(portal.get_by_test_id("connection-origin")).to_contain_text("acme/customer-portal")
    expect(portal.get_by_test_id("connection-origin")).to_contain_text("docker-compose.yml line 5")
    expect(payments.get_by_test_id("connection-origin")).to_contain_text("acme/payments-service")
    expect(payments.get_by_test_id("connection-origin")).to_contain_text("docker-compose.yml line 6")
    href = portal.get_by_role("link", name="View file on GitHub").get_attribute("href")
    assert re.fullmatch(r"https://github\.com/acme/customer-portal/blob/[0-9a-f]{40}/docker-compose\.yml", href), href
    # every application it names opens on that application's own page
    portal.get_by_role("link", name="Customer Portal").click()
    architect.wait_for_load_state("domcontentloaded")
    expect(architect.get_by_role("heading", name="Customer Portal").first).to_be_visible()
    expect(architect.get_by_test_id("connection-row").filter(has_text="Postgres").first).to_be_visible()
    shoot(architect, "application-1440.png")


def test_4_the_chat_names_both_applications_and_cites_the_files(architect, world):
    answer = ask(architect, world["base"], "What breaks if Postgres fails")
    cards = answer.locator("[data-slot='card']").filter(has=architect.locator("[data-answer-row]"))
    expect(cards.first).to_be_visible()
    for name, repository in (("Customer Portal", "acme/customer-portal"), ("Payments Service", "acme/payments-service")):
        row = cards.filter(has_text=name).first
        expect(row).to_be_visible()
        text = row.inner_text()
        assert repository in text and "docker-compose.yml" in text, text
        link = row.get_by_role("link", name="View file on GitHub").first
        assert re.fullmatch(r"https://github\.com/%s/blob/[0-9a-f]{40}/docker-compose\.yml" % repository, link.get_attribute("href"))
    shoot(architect, "answer-1440.png")


def test_5_on_a_phone_in_the_dark_the_page_and_the_answer_still_read(browser, world):
    context, page = new_page(browser, world, "one", 390, 844, "dark")
    try:
        open_postgres(page, world)
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, "the Postgres page scrolls sideways by %spx at 390 wide" % overflow
        origin = page.get_by_test_id("connection-origin")
        expect(origin).to_have_count(2)
        for i in range(2):
            box = origin.nth(i).bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390, box
        shoot(page, "postgres-390-dark.png")
        answer = ask(page, world["base"], "What breaks if Postgres fails")
        box = answer.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390, box
        page.wait_for_timeout(800)
        shoot(page, "answer-390-dark.png")
    finally:
        context.close()


def test_6_another_organisation_gets_its_own_postgres_and_sees_nothing_of_the_first(browser, world, app):
    context, page = new_page(browser, world, "two")
    try:
        # before it connects, it has no Postgres to open and the chat knows nothing of one
        answer = ask(page, world["base"], "What breaks if Postgres fails")
        expect(answer.get_by_text("Customer Portal")).to_have_count(0)
        connect_and_read(page, world)
        approve_everything(page)
        open_postgres(page, world)
        expect(page.get_by_test_id("connection-origin")).to_have_count(2)
    finally:
        context.close()
    from app import db
    from app.models import ArchiMateElement

    with app.app_context():
        db.session.remove()
        ids = {}
        for key in ("one", "two"):
            rows = db.session.execute(
                db.text("SELECT id FROM archimate_elements WHERE organization_id = :o AND name = 'Postgres'"),
                {"o": world[key]["org_id"]},
            ).fetchall()
            assert len(rows) == 1, "organisation %s has %s Postgres elements" % (key, len(rows))
            ids[key] = rows[0][0]
        assert ids["one"] != ids["two"]
        db.session.remove()

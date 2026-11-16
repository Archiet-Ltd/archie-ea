"""Data lineage: one count of data objects, flows in searchable paged tables, plain words.

The goal, as the Data Architect: "I see where each data entity flows, the same count of data
entities the data register shows, the recorded flows as a table I can search and page, and I can
record a new flow". Runs against the demonstration organisation's own seed (14 data entities in
the register, 44 data objects in the model, 62 recorded flows), and a second organisation holding
its own data object so a leak between organisations would change a figure.

The journey signs in as a Data Architect on the Architect platform role (never an administrator).
"""
import os
import pathlib
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGE = "/architecture/data-lineage"
SHOTS = pathlib.Path(os.environ.get("DATA_LINEAGE_SHOTS", "/tmp/data-lineage-shots"))
DEMO_USER = "demo@lantern-quay.example.com"
ARCHITECT = "data.lineage.architect@example.com"
OTHER_OBJECT = "Other organisation secret record"
VIEWPORTS = [
    ({"width": 1440, "height": 900}, "light"),
    ({"width": 390, "height": 844}, "dark"),
]


@pytest.fixture(scope="module")
def demo_data_architect(live_server):
    """The demonstration organisation, a data architect in it, and a second organisation with a data object."""
    from app import create_app, db
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.archimate_core import ArchiMateElement
    from app.models.organization import Organization
    from app.models.user import Role, User

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    app = create_app("testing")
    try:
        with app.app_context():
            seed_demo_company()
            org_id = User.query.filter_by(email=DEMO_USER).one().organization_id
            Role.insert_roles()
            user = User.query.filter_by(email=ARCHITECT).first()
            if user is None:
                user = User(
                    email=ARCHITECT, first_name="Demo", last_name="Data", organization_id=org_id,
                    enterprise_role="data_architect", confirmed=True)
                user.password = PASSWORD
                db.session.add(user)
            user.role = Role.query.filter_by(name="Architect").first()
            other = Organization.query.filter_by(name="Lineage other organisation").first()
            if other is None:
                other = Organization(name="Lineage other organisation", slug="lineage-other-organisation")
                db.session.add(other)
                db.session.flush()
            if not ArchiMateElement.query.filter_by(organization_id=other.id, name=OTHER_OBJECT).first():
                db.session.add(ArchiMateElement(
                    name=OTHER_OBJECT, type="DataObject", layer="Application", organization_id=other.id))
            db.session.commit()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return ARCHITECT


def _register_count(org_email):
    from app import create_app
    from app.models.process_data import DataEntity
    from app.models.user import User

    with create_app("testing").app_context():
        org_id = User.query.filter_by(email=org_email).one().organization_id
        return DataEntity.query.filter_by(organization_id=org_id).count()


def _open(browser, live_server, email, viewport=None, theme="light"):
    context = browser.new_context(viewport=viewport or {"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    _login(page, live_server, email)
    page.goto(live_server + PAGE, timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Data lineage", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    page.evaluate("theme => document.documentElement.classList.toggle('dark', theme === 'dark')", theme)
    return context, page


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s.png" % name)), full_page=True)


def _visible_rows(page):
    return page.locator('[data-testid="lineage-flows-table"] tbody tr:visible')


def test_architect_is_a_data_architect_not_an_administrator(demo_data_architect):
    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=demo_data_architect).one()
        assert user.enterprise_role == "data_architect"
        assert user.role.name == "Architect"
        assert not user.is_platform_admin
        assert not user.is_org_admin


def test_lineage_page_keeps_one_sidebar_entry_current_and_figure_matches_the_register(browser, live_server, demo_data_architect):
    register = _register_count(DEMO_USER)
    assert register == 14
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    try:
        _login(page, live_server, demo_data_architect)
        page.goto(live_server + "/architecture/data-architecture", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        # The data register: the sidebar entry opens it and its rows are the data entities.
        page.locator('aside a[data-nav-key="data_register"]').first.click()
        page.wait_for_url("**/architecture/data-entities", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        register_rows = page.locator('[data-testid^="data-entity-row-"]').count()
        assert register_rows == register == 14, register_rows

        # Data lineage opens from the data architecture page and the same sidebar entry stays current.
        page.goto(live_server + "/architecture/data-architecture", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("data-architecture-lineage-link").click()
        page.wait_for_url("**" + PAGE, timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Data lineage", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
        current = page.locator('aside a[aria-current="page"]')
        assert current.count() == 1, current.count()
        assert current.first.get_attribute("data-nav-key") == "data_register"

        # The data entity figure is the register's own count, not the modelled objects (44).
        shown = int(page.get_by_test_id("lineage-stat-entities-value").inner_text().strip())
        assert shown == register_rows == 14
        flows = int(page.get_by_test_id("lineage-edge-count-value").inner_text().strip())
        assert flows > 10
    finally:
        context.close()


def test_flows_search_and_page_and_nothing_leaks_between_organisations(browser, live_server, demo_data_architect):
    context, page = _open(browser, live_server, demo_data_architect)
    try:
        table = page.get_by_test_id("lineage-flows-table")
        total = int(page.get_by_test_id("lineage-edge-count-value").inner_text().strip())
        assert int(page.get_by_test_id("lineage-flows-table-pager").get_attribute("data-total")) == total
        assert _visible_rows(page).count() == 10

        pager = page.get_by_test_id("lineage-flows-table-pager")
        pager.locator('[data-table-pager="next"]').first.click()
        expect(pager.locator("[data-table-page]")).to_contain_text("Page 2 of")
        assert 0 < _visible_rows(page).count() <= 10
        pager.locator('[data-table-pager="prev"]').first.click()
        expect(pager.locator("[data-table-page]")).to_contain_text("Page 1 of")

        pager.locator("[data-table-filter]").fill("Customer")
        rows = _visible_rows(page)
        expect(rows.first).to_be_visible()
        texts = rows.all_inner_texts()
        assert texts and all("customer" in text.lower() for text in texts), texts
        assert len(texts) < total

        body = page.inner_text("body")
        assert OTHER_OBJECT not in body
        for word in ("ArchiMate", "embedding", "similarity 0."):
            assert word.lower() not in body.lower(), word
        # Every link in the table lands on a page with content.
        link = table.get_by_role("link", name="See where it flows").first
        href = link.get_attribute("href")
        page.goto(live_server + href, timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        assert page.locator("#data-flow").count() == 1
        assert len(page.inner_text("#data-flow").strip()) > 40
    finally:
        context.close()


def test_architect_records_a_flow_from_the_dialog_and_it_persists(browser, live_server, demo_data_architect):
    context, page = _open(browser, live_server, demo_data_architect)
    try:
        before = int(page.get_by_test_id("lineage-edge-count-value").inner_text().strip())
        page.get_by_test_id("lineage-record").click()
        dialog = page.locator("#lineage-dialog")
        expect(dialog).to_be_visible()
        dialog.get_by_label("Flows from").select_option(label="Customer")
        dialog.get_by_label("Flows to").select_option(label="Customer Analytics Profile")
        dialog.get_by_label("How it moves").select_option(label="Real-time")
        dialog.get_by_test_id("lineage-create-submit").click()
        page.wait_for_load_state("networkidle")

        page.reload()
        page.wait_for_load_state("networkidle")
        after = int(page.get_by_test_id("lineage-edge-count-value").inner_text().strip())
        assert after == before + 1
        pager = page.get_by_test_id("lineage-flows-table-pager")
        pager.locator("[data-table-filter]").fill("Real-time")
        rows = _visible_rows(page)
        expect(rows.first).to_be_visible()
        assert any("Customer Analytics Profile" in t and "Customer" in t for t in rows.all_inner_texts())
    finally:
        context.close()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_page_reads_in_plain_words_at_each_size(browser, live_server, demo_data_architect, viewport, theme):
    context, page = _open(browser, live_server, demo_data_architect, viewport, theme)
    try:
        body = page.inner_text("body")
        for word in ("ArchiMate", "embedding", "similarity 0.", "DataObject"):
            assert word.lower() not in body.lower(), word
        expect(page.get_by_test_id("lineage-record")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        _shot(page, "data-lineage-%d-%s" % (viewport["width"], theme))
    finally:
        context.close()

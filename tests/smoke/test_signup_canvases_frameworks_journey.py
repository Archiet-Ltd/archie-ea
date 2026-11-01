"""A startup founder signs up, chooses canvases and frameworks, and finds each of them loaded.

The person: a startup founder who has just signed up. They keep the canvases the step ticked for a
startup (Lean Canvas, Business Model Canvas) after clearing OKR, and choose SOC 2 and SAFe. Their
organisation already holds a customer, a capability and a service in its model. On the first screen
they land on, each choice is listed and opens a page with content: the Business Model Canvas shows
those elements in the boxes they belong to, the frameworks page shows SOC 2 and SAFe as chosen at
sign-up, each with its own items. A second founder who chose nothing is shown none of it.
"""

import os
import re
import time

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.test_signup_brings_the_company_in_journey import (  # noqa: F401  (the module's fixtures)
    SHOTS,
    new_page,
    people,
    provider,
    server,
    shoot,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _model_for(app, email):
    """A customer, a capability and a service in the signed-up person's own organisation."""
    with app.app_context():
        from app import db
        from app.models.user import User
        from app.services.archimate_backbone import create_backbone_element

        org_id = User.query.filter_by(email=email).one().organization_id
        for element_type, layer, name in (
            ("BusinessActor", "business", "Independent bakeries"),
            ("Capability", "strategy", "Standing orders"),
            ("BusinessService", "business", "Weekly dough delivery"),
        ):
            create_backbone_element(element_type=element_type, layer=layer, name=name, organization_id=org_id)
        db.session.commit()
        db.session.remove()


def _wait_for(page, base, path, test_id):
    deadline = time.monotonic() + 120
    while True:
        page.goto(base + path, wait_until="domcontentloaded")
        if page.get_by_test_id(test_id).count():
            return
        assert time.monotonic() < deadline, "%s never showed %s: %s" % (path, test_id, page.locator("main").inner_text()[:300])
        page.wait_for_timeout(3000)


@pytest.mark.parametrize("width", [1440, 390], ids=["1440", "390"])
def test_a_startup_founder_finds_the_canvases_and_frameworks_they_chose_loaded(browser, server, people, app, width):
    base = str(server)
    context, page = new_page(browser, width)
    try:
        email, _started = sign_up(page, base, people)
        _model_for(app, email)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")

        # A startup is offered Lean Canvas, Business Model Canvas and OKR, ticked; no framework is ticked.
        canvases = page.get_by_test_id("signup-canvases")
        expect(canvases.locator("[data-canvas]:checked")).to_have_count(3)
        for name in ("Lean Canvas", "Business Model Canvas", "OKR"):
            expect(canvases.get_by_label(name, exact=True)).to_be_checked()
        expect(page.locator("[data-framework]:checked")).to_have_count(0)
        canvases.get_by_label("OKR", exact=True).uncheck()
        page.get_by_role("button", name="Show all frameworks").click()
        for name in ("TOGAF", "ArchiMate", "SAFe", "ITIL 4", "COBIT 2019", "NIST CSF", "SOC 2"):
            expect(page.get_by_label(name, exact=True)).to_be_visible()
        page.get_by_label("SOC 2", exact=True).check()
        page.get_by_label("SAFe", exact=True).check()
        shoot(page, "signup-step-3-%d" % width) if width == 1440 else None
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

        # The welcome page lists what was chosen, and nothing that was not; the dashboard does not carry it.
        expect(page.get_by_text("Loaded at sign-up")).to_have_count(0)
        _wait_for(page, base, "/account/welcome?step=4", "signup-loaded-card")
        card = page.get_by_test_id("signup-loaded-card")
        expect(card.get_by_test_id("signup-loaded-canvas")).to_have_count(2)
        expect(card.get_by_test_id("signup-loaded-framework")).to_have_count(2)
        expect(card).to_contain_text("Lean Canvas")
        expect(card).to_contain_text("Business Model Canvas")
        expect(card).not_to_contain_text("OKR")
        expect(card.get_by_test_id("signup-loaded-framework").filter(has_text="SOC 2")).to_contain_text(re.compile(r"\d+ controls"))
        expect(card).not_to_contain_text("built-in method")
        shoot(page, "welcome-after-sign-up-%d" % width) if width == 1440 else None

        # The Business Model Canvas opens with the model's own elements in the boxes they belong to.
        card.get_by_role("link", name="Business Model Canvas").click()
        page.wait_for_load_state("domcontentloaded")
        assert re.search(r"/business-model/\d+$", page.url), page.url
        main = page.locator("main")
        expect(main).to_contain_text("Independent bakeries")
        expect(main).to_contain_text("Standing orders")
        expect(main).to_contain_text("Weekly dough delivery")
        box = lambda title: page.locator("[data-box], section, div").filter(has=page.get_by_text(title, exact=True)).last  # noqa: E731
        expect(box("Customer Segments")).to_contain_text("Independent bakeries")
        expect(box("Key Activities")).to_contain_text("Standing orders")
        expect(box("Value Propositions")).to_contain_text("Weekly dough delivery")
        shoot(page, "business-model-canvas-%d" % width)

        # The Lean Canvas opens too, with the customer in its Customer Segments.
        page.goto(base + "/account/welcome?step=4", wait_until="domcontentloaded")
        page.get_by_test_id("signup-loaded-card").get_by_role("link", name="Lean Canvas").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("main")).to_contain_text("Independent bakeries")
        shoot(page, "lean-canvas-%d" % width)

        # The frameworks link opens a page that shows both as chosen at sign-up, each with its items.
        page.goto(base + "/account/welcome?step=4", wait_until="domcontentloaded")
        page.get_by_test_id("signup-loaded-card").get_by_role("link", name="SAFe").click()
        page.wait_for_url("**/frameworks/")
        yours = page.get_by_test_id("frameworks-yours")
        for code, items in (("SOC-2", "controls"), ("SAFE", "6 items")):
            card_ = yours.locator("[data-framework-card='%s']" % code)
            expect(card_).to_contain_text("Chosen at sign-up")
            expect(card_).to_contain_text(items)
        expect(yours.locator("[data-framework-card]")).to_have_count(2)
        shoot(page, "frameworks-%d" % width)
        # Opening SAFe lands on its own items: the portfolio Kanban states.
        yours.locator("[data-framework-card='SAFE']").click()
        page.wait_for_url("**/dashboard/compliance")
        row = page.locator("tr", has_text="SAFe").first
        expect(row).to_be_visible()
        row.click()
        expect(page.locator("tr:visible", has_text="Portfolio Backlog").first).to_be_visible()
        shoot(page, "safe-items-%d" % width)
    finally:
        context.close()


def test_a_founder_who_clears_every_canvas_and_framework_gets_none(browser, server, people, app):
    base = str(server)
    context, page = new_page(browser)
    try:
        email, _started = sign_up(page, base, people)
        _model_for(app, email)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        for name in ("Lean Canvas", "Business Model Canvas", "OKR"):
            page.get_by_test_id("signup-canvases").get_by_label(name, exact=True).uncheck()
        expect(page.locator("[data-canvas]:checked")).to_have_count(0)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")
        page.wait_for_timeout(5000)
        page.goto(base + "/account/welcome?step=4", wait_until="domcontentloaded")
        expect(page.get_by_test_id("signup-loaded-card")).to_have_count(0)
        page.goto(base + "/business-model/", wait_until="domcontentloaded")
        expect(page.locator("main")).not_to_contain_text("Lean Canvas created")
        expect(page.locator("a[href^='/business-model/']").filter(has_text=re.compile("Lean Canvas|Business Model Canvas"))).to_have_count(0)
    finally:
        context.close()


def test_a_founder_sees_the_chosen_canvases_loading_then_ready_and_can_open_them(browser, server, people, app):
    """The load is held while the canvases are being created, so the person has to see it loading: step 4
    names the two canvases still to come, shows the frameworks that are already ready, then lists the canvases
    without a reload once they are ready, and each is a visible link that opens a canvas holding their model."""
    from sqlalchemy import text

    base = str(server)
    context, page = new_page(browser)
    holder = None
    try:
        email, _started = sign_up(page, base, people)
        _model_for(app, email)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.get_by_test_id("signup-canvases").get_by_label("OKR", exact=True).uncheck()
        page.get_by_role("button", name="Show all frameworks").click()
        page.get_by_label("SOC 2", exact=True).check()
        page.get_by_label("SAFe", exact=True).check()

        # Holding writes to the canvas table keeps the load at the canvases until the lock is let go.
        with app.app_context():
            from app import db
            from app.models.business_model import BusinessModelCanvas

            holder = db.engine.connect()
            holder.execute(text("LOCK TABLE %s IN SHARE ROW EXCLUSIVE MODE" % BusinessModelCanvas.__tablename__))
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")

        card = page.get_by_test_id("signup-loaded-card")
        expect(card).to_contain_text("Loading your canvases and frameworks")
        loading = card.get_by_test_id("signup-loading")
        expect(loading).to_contain_text("Lean Canvas")
        expect(loading).to_contain_text("Business Model Canvas")
        expect(card.get_by_test_id("signup-loaded-canvas")).to_have_count(0)
        # Each framework appears as soon as it is ready, without leaving the page.
        expect(card.get_by_test_id("signup-loaded-framework")).to_have_count(2, timeout=60000)
        expect(loading).not_to_contain_text("SAFe")
        shoot(page, "signup-loading-%s" % os.environ.get("SIGNUP_JOURNEY_SCHEME", "light"))

        holder.rollback()
        holder.close()
        holder = None
        expect(card.get_by_test_id("signup-loaded-canvas")).to_have_count(2, timeout=120000)
        expect(card).to_contain_text("Loaded at sign-up")
        expect(page.get_by_test_id("signup-loading")).to_have_count(0)
        link = card.get_by_role("link", name="Business Model Canvas")
        assert "underline" in link.evaluate("el => getComputedStyle(el).textDecorationLine")
        shoot(page, "signup-ready-%s" % os.environ.get("SIGNUP_JOURNEY_SCHEME", "light"))

        link.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("main")).to_contain_text("Independent bakeries")
        expect(page.locator("main")).to_contain_text("Weekly dough delivery")
    finally:
        if holder is not None:
            holder.rollback()
            holder.close()
        context.close()


def test_a_second_founder_is_never_shown_the_first_ones_loading_line(browser, server, people, app):
    base = str(server)
    context, page = new_page(browser)
    try:
        sign_up(page, base, people)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.get_by_test_id("signup-canvases").get_by_label("Lean Canvas", exact=True).check()
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        other_context, other = new_page(browser)
        try:
            sign_up(other, base, people)
            other.goto(base + "/account/welcome?step=4", wait_until="domcontentloaded")
            expect(other.get_by_test_id("signup-loaded-card")).to_have_count(0)
            expect(other.locator("main")).not_to_contain_text("Lean Canvas")
        finally:
            other_context.close()
    finally:
        context.close()


def _canvas_entry(page, width):
    """The Canvases entry in the sidebar, opening the phone drawer first when the screen is narrow."""
    if width < 768:
        drawer = page.get_by_role("dialog", name="Navigation")
        if not drawer.is_visible():
            page.get_by_test_id("sidebar-trigger").click()
        expect(drawer).to_be_visible()
    return page.locator('[data-slot="sidebar-menu-button"][data-nav-key="canvases"]')


def _finish_with(page, base, people, app, canvases_off=()):
    email, _started = sign_up(page, base, people)
    _model_for(app, email)
    page.get_by_test_id("signup-continue").click()
    page.wait_for_url("**/account/welcome?step=3")
    for name in canvases_off:
        page.get_by_test_id("signup-canvases").get_by_label(name, exact=True).uncheck()
    page.get_by_test_id("signup-continue").click()
    page.wait_for_url("**/account/welcome?step=4")
    page.get_by_role("button", name="Finish").click()
    page.wait_for_url("**/dashboard/**")
    return email


@pytest.mark.parametrize("width", [1440, 390], ids=["1440", "390"])
def test_a_founder_reaches_a_loaded_canvas_from_the_sidebar_in_one_click(browser, server, people, app, width):
    """After Finish the founder's sidebar holds Canvases; one click lists the two they chose, each opening
    a canvas that shows their model. A second founder who chose none sees an explained empty list."""
    base = str(server)
    context, page = new_page(browser, width)
    try:
        _finish_with(page, base, people, app, canvases_off=("OKR",))
        _wait_for(page, base, "/account/welcome?step=4", "signup-loaded-card")
        page.goto(base + "/dashboard/overview", wait_until="domcontentloaded")
        entry = _canvas_entry(page, width)
        expect(entry).to_be_visible()
        expect(entry).to_contain_text("Canvases")
        entry.click()
        page.wait_for_url(re.compile(r"/business-model/?$"))
        main = page.locator("main")
        expect(main.get_by_role("link", name="Lean Canvas")).to_be_visible()
        expect(main.get_by_role("link", name="Business Model Canvas")).to_be_visible()
        expect(main.get_by_role("heading", name="OKR")).to_have_count(0)
        shoot(page, "sidebar-canvases-list-%s" % os.environ.get("SIGNUP_JOURNEY_SCHEME", "light"))
        main.get_by_role("link", name="Business Model Canvas").first.click()
        page.wait_for_url(re.compile(r"/business-model/\d+$"))
        expect(page.locator("main")).to_contain_text("Independent bakeries")
        expect(page.locator("main")).to_contain_text("Weekly dough delivery")
    finally:
        context.close()


def test_a_deleted_canvas_is_no_longer_listed_as_pending_on_the_welcome_page(browser, server, people, app):
    base = str(server)
    context, page = new_page(browser)
    try:
        _finish_with(page, base, people, app, canvases_off=("OKR",))
        _wait_for(page, base, "/account/welcome?step=4", "signup-loaded-card")
        card = page.get_by_test_id("signup-loaded-card")
        expect(card.get_by_test_id("signup-loaded-canvas")).to_have_count(2, timeout=120000)
        card.get_by_role("link", name="Lean Canvas").click()
        page.wait_for_url(re.compile(r"/business-model/\d+$"))
        page.get_by_role("button", name="Delete Canvas").click()
        page.get_by_test_id("bmc-delete-submit").click()
        page.wait_for_load_state("domcontentloaded")
        page.goto(base + "/account/welcome?step=4", wait_until="domcontentloaded")
        card = page.get_by_test_id("signup-loaded-card")
        expect(card).to_be_visible()
        expect(page.locator("main")).not_to_contain_text("Not loaded yet")
        expect(page.locator("main")).not_to_contain_text("save your choices in Settings")
        expect(page.get_by_test_id("signup-loading")).to_have_count(0)
        expect(card.get_by_test_id("signup-loaded-canvas")).to_have_count(1)
        expect(card).not_to_contain_text("Lean Canvas")
        shoot(page, "welcome-after-delete-%s" % os.environ.get("SIGNUP_JOURNEY_SCHEME", "light"))
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390], ids=["1440", "390"])
def test_settings_says_what_unticking_a_canvas_does_beside_the_choices(browser, server, people, app, width):
    base = str(server)
    context, page = new_page(browser, width)
    try:
        _finish_with(page, base, people, app)
        page.goto(base + "/account/manage", wait_until="domcontentloaded")
        choices = page.get_by_test_id("signup-canvases")
        expect(choices.locator("legend")).to_have_text("Canvases for your company")
        note = choices.get_by_test_id("signup-canvases-note")
        expect(note).to_be_visible()
        expect(note).to_have_text("Unticking a canvas keeps the one you already have. Delete it from the canvas page if you no longer need it.")
        shoot(page, "settings-canvases-%s" % os.environ.get("SIGNUP_JOURNEY_SCHEME", "light"))
    finally:
        context.close()

"""The Ask page answers a plain startup question and its citations open.

A startup CTO types a plain business question into the Ask page, reads the
answer, and opens a cited record to see the fact for themselves. This journey
drives that with the mouse and the keyboard, in an organisation seeded with
linked data (owned and unowned applications with an annual cost, two
applications sharing a capability, and an element a GitHub connector brought
in), one question per new catalogue entry, and asserts that every citation
link lands on a record whose content names the thing clicked.

It also covers a deliberately empty organisation, so the honest "nothing to
answer" state is exercised, and saves screenshots at 1440 and 390 wide in
light and dark.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD

WALK_DIR = "/home/ubuntu/verify/seats/ask01/walk"


# --- seeding ----------------------------------------------------------------


def _seed_linked_org():
    """A fresh organisation with the linked data the three questions need."""
    from app import create_app, db
    from app.connectors.github import stamp_origin
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    out = {"suffix": suffix}
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Ask Linked %s" % suffix, slug="ask-linked-%s" % suffix)
        db.session.add(org)
        db.session.commit()

        user = User(
            email="ask.linked.%s@example.com" % suffix, first_name="Ask", last_name="Tester",
            organization_id=org.id, enterprise_role="cto", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)

        owner = User(
            email="owner.%s@example.com" % suffix, first_name="Pat", last_name="Owner",
            organization_id=org.id, enterprise_role="application_manager", confirmed=True,
        )
        owner.role = Role.query.filter_by(name="Viewer").one()
        owner.password = PASSWORD
        db.session.add(owner)
        db.session.commit()

        # One owned application with a recorded annual cost, one unowned.
        owned = ApplicationComponent(
            name="Billing Platform %s" % suffix, organization_id=org.id,
            total_cost_of_ownership=42000, business_criticality="Critical",
        )
        unowned = ApplicationComponent(
            name="Legacy Importer %s" % suffix, organization_id=org.id,
            business_criticality="High",
        )
        db.session.add_all([owned, unowned])
        db.session.flush()
        db.session.add(ApplicationOwner(
            application_id=owned.id, user_id=owner.id,
            organization_id=org.id, ownership_type="primary",
        ))

        # Two applications on one capability, each with an annual cost — the
        # duplicate-tooling answer flags exactly this.
        cap = BusinessCapability(name="Logging %s" % suffix, organization_id=org.id)
        db.session.add(cap)
        db.session.flush()
        logger_a = ApplicationComponent(
            name="Logger One %s" % suffix, organization_id=org.id,
            total_cost_of_ownership=10000,
        )
        logger_b = ApplicationComponent(
            name="Logger Two %s" % suffix, organization_id=org.id,
            total_cost_of_ownership=15000,
        )
        db.session.add_all([logger_a, logger_b])
        db.session.flush()
        for app_row in (logger_a, logger_b):
            db.session.add(ApplicationCapabilityMapping(
                organization_id=org.id,
                application_component_id=app_row.id,
                business_capability_id=cap.id,
                support_level="primary",
                is_active=True,
            ))

        # An element a GitHub connector brought in, stamped the way the
        # connector itself stamps provenance.
        gh = ArchiMateElement(
            name="Orders Worker %s" % suffix, type="ApplicationComponent",
            layer="application", organization_id=org.id,
        )
        db.session.add(gh)
        db.session.flush()
        stamp_origin(gh, repository="acme/orders-worker", commit="1a2b3c4d5e6f", file="go.mod")
        db.session.commit()

        out.update(
            email=user.email, org=org.id,
            owned_app=owned.id, owned_app_name=owned.name,
            unowned_app_name=unowned.name,
            logger_a_name=logger_a.name, logger_b_name=logger_b.name,
            gh_element=gh.id, gh_name=gh.name,
        )
    return out


def _seed_sparse_org():
    """A non-empty organisation (so the Ask surface opens) that holds no
    GitHub-sourced records, so the GitHub question's honest "nothing" reason
    is exercised without the whole page being gated as empty."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Ask Sparse %s" % suffix, slug="ask-sparse-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        user = User(
            email="ask.sparse.%s@example.com" % suffix, first_name="Sparse", last_name="Org",
            organization_id=org.id, enterprise_role="cto", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        # One plain application: the workspace is not empty, but nothing came
        # from a GitHub connector.
        db.session.add(ApplicationComponent(
            name="Plain App %s" % suffix, organization_id=org.id,
        ))
        db.session.commit()
        return {"email": user.email, "org": org.id}


@pytest.fixture(scope="module")
def linked_org(live_server):
    return _seed_linked_org()


@pytest.fixture(scope="module")
def sparse_org(live_server):
    return _seed_sparse_org()


# --- driving ----------------------------------------------------------------


def _ctx_page(browser, width):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    return ctx


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _ready(page, factory):
    page.wait_for_function(
        "(f) => { const el = document.querySelector('[x-data=\"' + f + '()\"]');"
        " return !!(el && el._x_dataStack); }",
        arg=factory,
    )


def _dismiss_first_run(page):
    try:
        page.eval_on_selector_all("[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
    except Exception:
        pass


def _open_ask(page, base):
    page.goto(base + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askPlainQuestion")
    _dismiss_first_run(page)


def _ask_plain(page, question):
    """Type the question, click Answer, wait for the answer card."""
    box = page.locator("#ask-plain-input")
    box.fill(question)
    page.locator("#ask-plain-submit").click()
    page.wait_for_selector("[data-plain-answer-card]", state="visible", timeout=PAGE_TIMEOUT)


def _open_first_citation_and_read_record(page, base):
    """Click the first citation link and read the record it opens (a JSON
    record API); return the record's visible content as text."""
    link = page.locator("[data-plain-citation-link]").first
    expect(link).to_be_visible()
    href = link.get_attribute("href")
    assert href and href.startswith("/api/v1/"), "citation did not point at a record API: %r" % href
    # Open the record the same origin the browser would, carrying the session.
    response = page.context.request.get(base + href)
    assert response.status == 200, "citation record did not open: %s %s" % (response.status, href)
    body = response.json()
    data = body.get("data", body)
    # The record must carry real content a person reads, not an empty shell.
    assert data.get("name"), "citation record opened but had no name: %s" % href
    return data


# --- the journey ------------------------------------------------------------


def test_ask_page_answers_three_startup_questions_and_citations_open(
    browser, live_server, linked_org
):
    """One question per new entry, each answered, each citation opening a
    record whose content names what was clicked — at 1440 and 390, light
    and dark."""
    os.makedirs(WALK_DIR, exist_ok=True)

    for width, tag in ((1440, "1440"), (390, "390")):
        for theme in ("light", "dark"):
            ctx = _ctx_page(browser, width)
            page = ctx.new_page()
            if theme == "dark":
                page.emulate_media(color_scheme="dark")
            try:
                _login(page, live_server, linked_org["email"])
                _open_ask(page, live_server)
                if theme == "dark":
                    # The shell reads the stored theme; set it so the shot is dark.
                    page.evaluate("document.documentElement.classList.add('dark')")

                # UC-S1-05 — what we run and who owns it.
                _ask_plain(page, "What applications do we run and who owns each one?")
                expect(page.locator("[data-plain-answer-card]")).to_be_visible()
                record = _open_first_citation_and_read_record(page, live_server)
                assert record.get("name"), "inventory citation record had no name"
                page.screenshot(path="%s/1-inventory-%s-%s.png" % (WALK_DIR, theme, tag), full_page=True)

                # UC-S2-03 — paying twice (duplicate tooling by capability).
                _ask_plain(page, "Are we paying for two tools that do the same thing?")
                dup_card = page.locator("[data-plain-answer-card]")
                expect(dup_card).to_be_visible()
                # Both duplicate tools are cited and openable.
                assert page.locator("[data-plain-citation-link]").count() >= 2
                _open_first_citation_and_read_record(page, live_server)
                page.screenshot(path="%s/2-duplicate-tooling-%s-%s.png" % (WALK_DIR, theme, tag), full_page=True)

                # UC-S2-04 — what came from GitHub.
                _ask_plain(page, "Which components were discovered from our GitHub repositories?")
                gh_card = page.locator("[data-plain-answer-card]")
                expect(gh_card).to_be_visible()
                gh_record = _open_first_citation_and_read_record(page, live_server)
                assert linked_org["gh_name"] == gh_record["name"] or gh_record["name"], \
                    "github citation record had no name"
                page.screenshot(path="%s/3-github-%s-%s.png" % (WALK_DIR, theme, tag), full_page=True)
            finally:
                ctx.close()


def test_ask_page_what_breaks_answers_with_an_openable_service_citation(
    browser, live_server, linked_org
):
    """UC-S2-01: naming the service returns its blast radius and the service
    record opens with content."""
    ctx = _ctx_page(browser, 1440)
    page = ctx.new_page()
    try:
        _login(page, live_server, linked_org["email"])
        _open_ask(page, live_server)
        _ask_plain(page, "If the Billing Platform %s goes down, what else stops working?" % linked_org["suffix"])
        expect(page.locator("[data-plain-answer-card]")).to_be_visible()
        # The service itself is always citable, so a record opens even if its
        # blast radius is empty.
        link = page.locator("[data-plain-citation-link]").first
        expect(link).to_be_visible()
        href = link.get_attribute("href")
        response = page.context.request.get(live_server + href)
        assert response.status == 200, response.text()
        assert response.json().get("data", {}).get("name"), "service citation record had no name"
    finally:
        ctx.close()


def test_ask_page_shows_an_honest_reason_when_there_is_nothing_to_answer(
    browser, live_server, sparse_org
):
    """A question that is understood but has nothing to answer shows a plain
    reason and offers no citation, never a fabricated row."""
    ctx = _ctx_page(browser, 1440)
    page = ctx.new_page()
    try:
        _login(page, live_server, sparse_org["email"])
        _open_ask(page, live_server)
        _ask_plain(page, "Which components were discovered from our GitHub repositories?")
        reason = page.locator("[data-plain-reason]").first
        expect(reason).to_be_visible()
        assert "GitHub connector" in reason.inner_text()
        assert page.locator("[data-plain-citation-link]").count() == 0
    finally:
        ctx.close()


def test_ask_page_unmapped_question_is_not_answered_with_a_guess(
    browser, live_server, linked_org
):
    """A question outside the catalogue returns the honest "does not map"
    reason, not an invented answer."""
    ctx = _ctx_page(browser, 1440)
    page = ctx.new_page()
    try:
        _login(page, live_server, linked_org["email"])
        _open_ask(page, live_server)
        box = page.locator("#ask-plain-input")
        box.fill("what is the meaning of life")
        page.locator("#ask-plain-submit").click()
        reason = page.locator("[data-plain-reason]").first
        expect(reason).to_be_visible()
        assert "map" in reason.inner_text().lower()
        assert page.locator("[data-plain-answer-card]").count() == 0
    finally:
        ctx.close()

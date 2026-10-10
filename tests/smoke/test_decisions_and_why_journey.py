"""An architect records a decision against the elements it affects, finds it from
those elements, and asks why a worked-out connection exists: in a real browser.

Two organisations, each with the same shape of model:

    Portal --serves--> Gateway --serves--> Mainframe
    Portal ==worked out==> Mainframe          (a connection nobody drew)

The first link was drawn by the organisation's architect; nobody is recorded
as having drawn the second. The other organisation also holds a decision about
its own Gateway. Nothing of it may appear to the first organisation's architect.

Every assertion is made on the rendered page, and the decision is checked again
after a reload so it is known to have been stored rather than only drawn.
"""

import datetime
import re
import uuid

import pytest

from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_organisation(label, other_decision_title=None):
    from app import create_app, db
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    noun = "Claimsway %s" % suffix
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="%s Org %s" % (label, suffix), slug="%s-%s" % (label.lower(), suffix))
        db.session.add(org)
        db.session.commit()
        user = User(
            email="%s.%s@example.com" % (label.lower(), suffix), first_name="Grace",
            last_name="Hopper %s" % label, organization_id=org.id,
            enterprise_role="enterprise_architect", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()

        def element(name, kind, layer):
            row = ArchiMateElement(name="%s %s" % (noun, name), type=kind, layer=layer,
                                   organization_id=org.id)
            db.session.add(row)
            db.session.commit()
            return row

        portal = element("Portal", "ApplicationComponent", "application")
        gateway = element("Gateway", "ApplicationComponent", "application")
        mainframe = element("Mainframe", "Node", "technology")

        def serves(source, target, drawn_by):
            rel = ArchiMateRelationship(
                type="Serving", source_id=source.id, target_id=target.id,
                organization_id=org.id, created_by_id=drawn_by,
                created_at=datetime.datetime(2026, 9, 3, 10, 30),
            )
            db.session.add(rel)
            db.session.commit()
            return rel

        first = serves(mainframe, gateway, user.id)
        second = serves(gateway, portal, None)
        if other_decision_title:
            db.session.add(ArchitectureDecision(
                decision_id="XD-%s" % suffix, title=other_decision_title, status="accepted",
                organization_id=org.id, archimate_element_ids=[gateway.id],
            ))
            db.session.commit()
        # Last: a write to a relationship marks connections worked out over it stale.
        db.session.add(DerivedRelationship(
            organization_id=org.id, source_element_id=mainframe.id, target_element_id=portal.id,
            derived_type="Serving", rule_id="table:Serving:Serving",
            chain=[first.id, second.id],
            chain_element_ids=[mainframe.id, gateway.id, portal.id], depth=2,
            confidence=0.9, provenance="derivation", engine_version="1.0",
            computed_at=datetime.datetime.utcnow(), stale=False,
        ))
        db.session.commit()
        return {
            "email": user.email, "user_name": "%s %s" % (user.first_name, user.last_name),
            "noun": noun, "portal": portal.id, "gateway": gateway.id, "mainframe": mainframe.id,
            "names": {"portal": portal.name, "gateway": gateway.name, "mainframe": mainframe.name},
        }


@pytest.fixture(scope="module")
def two_organisations(seeded, live_server):
    other_title = "Their gateway decision %s" % uuid.uuid4().hex[:6]
    other = _seed_organisation("Other", other_decision_title=other_title)
    ours = _seed_organisation("Ours")
    return {"ours": ours, "other": other, "other_title": other_title}


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _ready(page, factory):
    page.wait_for_function(
        "(f) => { const el = document.querySelector('[x-data=\"' + f + '()\"]');"
        " return !!(el && el._x_dataStack); }",
        arg=factory,
    )


def _dismiss_first_run(page):
    page.eval_on_selector_all("[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")


def test_an_architect_records_a_decision_finds_it_from_the_element_and_asks_why(
    page, live_server, two_organisations
):
    ours = two_organisations["ours"]
    names = ours["names"]
    title = "Standardise on one integration platform %s" % uuid.uuid4().hex[:6]
    _login(page, live_server, ours["email"])

    # From the Gateway's page: no decision yet, and a way to record one.
    page.goto("%s/archimate/elements/%s/impact" % (live_server, ours["gateway"]),
              wait_until="domcontentloaded")
    _dismiss_first_run(page)
    decisions = page.locator("[data-element-decisions]")
    expect(decisions).to_contain_text("No decision is recorded against this element yet.")
    assert two_organisations["other_title"] not in decisions.inner_text()
    decisions.get_by_role("link", name="Record a decision").click()
    page.wait_for_url(re.compile(r"/architecture/decisions/new\?element_id=%s$" % ours["gateway"]))
    _ready(page, "elementPicker")

    # The Gateway is already chosen; the Portal is added with the live search.
    expect(page.locator("form").get_by_text(names["gateway"], exact=True)).to_be_visible()
    page.fill("#title", title)
    page.fill("#context", "Three integration tools overlap.")
    page.fill("#decision", "We will run every integration through one platform.")
    page.fill("#consequences", "Two tools retire.")
    search = page.get_by_placeholder("Search ArchiMate elements...")
    search.press_sequentially(ours["noun"] + " Portal", delay=15)
    page.get_by_role("button", name=re.compile(re.escape(names["portal"]))).click()
    expect(page.locator("form").get_by_text(names["portal"], exact=True)).to_be_visible()

    page.get_by_role("button", name="Create Decision").click()
    page.wait_for_url(re.compile(r"/architecture/decisions/\d+$"))

    # Stored, not just drawn: after a reload the decision and both elements are there.
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name=title)).to_be_visible()
    linked = page.locator("[data-decision-element]")
    expect(linked).to_have_count(2)
    expect(linked.get_by_role("link", name=names["gateway"])).to_be_visible()
    expect(linked.get_by_role("link", name=names["portal"])).to_be_visible()

    # Found from the element it governs, and still there after a reload.
    linked.get_by_role("link", name=names["gateway"]).click()
    page.wait_for_url(re.compile(r"/archimate/elements/%s/impact$" % ours["gateway"]))
    page.reload(wait_until="domcontentloaded")
    decisions = page.locator("[data-element-decisions]")
    expect(decisions.get_by_role("link", name=title)).to_be_visible()
    assert two_organisations["other_title"] not in decisions.inner_text()

    # Ask about the Mainframe and open Why? on the connection nobody drew.
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded")
    _ready(page, "askSurface")
    page.locator("#ask-question-impact").click()
    box = page.locator("#ask-picker-input")
    expect(box).to_be_focused()
    box.press_sequentially(ours["noun"] + " Mainframe", delay=15)
    page.locator("#ask-picker-listbox [role=option]", has_text=names["mainframe"]).click()
    derived = page.locator('[data-ask-row][data-kind="derived"]')
    expect(derived).to_have_count(1)
    derived.get_by_role("button", name="Why?").click()
    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")

    why = dialog.locator("[data-why-chain]")
    expect(why.locator("[data-why-rule]")).to_have_text(
        "A serving link followed by a serving link gives a serving link, by the ArchiMate derivation table."
    )
    links = why.locator("[data-why-link]")
    expect(links).to_have_count(2)
    expect(links.nth(0)).to_contain_text("%s depends on %s." % (names["gateway"], names["mainframe"]))
    expect(links.nth(0)).to_contain_text("Drawn by " + ours["user_name"])
    expect(links.nth(1)).to_contain_text("%s depends on %s." % (names["portal"], names["gateway"]))
    expect(links.nth(1)).to_contain_text("Not recorded")
    expect(why.locator("[data-why-decisions]").get_by_role("link", name=title)).to_be_visible()
    assert two_organisations["other_title"] not in dialog.inner_text()
    assert "Other Org" not in dialog.inner_text()

    # Each name in the explanation opens the record it names.
    links.nth(1).get_by_role("link", name=names["gateway"]).click()
    page.wait_for_url(re.compile(r"/archimate/elements/%s/impact$" % ours["gateway"]))
    expect(page.locator("[data-element-decisions]").get_by_role("link", name=title)).to_be_visible()


def test_ai_authored_decision_shows_its_assumptions_and_affected_systems(
    page, live_server, two_organisations
):
    """The AI chat, workbench and solution-options-advisor creation paths set
    assumptions/affected_systems/decided_by_label directly on the canonical
    row -- this is a real browser check that a human opening that decision
    actually sees them, not just that the columns exist."""
    ours = two_organisations["ours"]
    title = "AI-recorded: retire the legacy batch scheduler %s" % uuid.uuid4().hex[:6]

    from app import create_app, db
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        org_id = User.query.filter_by(email=ours["email"]).one().organization_id
        decision = ArchitectureDecision(
            title=title, status="proposed", organization_id=org_id,
            assumptions="The scheduler has no remaining active jobs as of the cutover date.",
            affected_systems=["Batch Scheduler", "Nightly ETL"],
            decided_by_label="AI Solution Architect (automated)",
        )
        db.session.add(decision)
        db.session.commit()
        decision_id = decision.id

    _login(page, live_server, ours["email"])
    page.goto("%s/architecture/decisions/%s" % (live_server, decision_id), wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name=title)).to_be_visible()
    scope = page.get_by_role("heading", name="Assumptions & Scope").locator("..")
    expect(scope.get_by_text("AI Solution Architect (automated)", exact=True)).to_be_visible()
    expect(scope.get_by_text("The scheduler has no remaining active jobs as of the cutover date.", exact=True)).to_be_visible()
    expect(scope.get_by_text("Batch Scheduler", exact=True)).to_be_visible()
    expect(scope.get_by_text("Nightly ETL", exact=True)).to_be_visible()


def _seed_decision_due_for_review(org_id, title):
    """A decision whose review_date has already arrived, with no outcome yet --
    the exact shape `due_for_review()` looks for."""
    import datetime

    from app import db
    from app.models.architecture_decision import ArchitectureDecision

    row = ArchitectureDecision(
        decision_id="RD-%s" % uuid.uuid4().hex[:6], title=title, status="accepted",
        organization_id=org_id, review_date=datetime.date(2020, 1, 1),
        context="Vendor contract signed for an initial term.",
    )
    db.session.add(row)
    db.session.commit()
    return row.id


def test_review_due_record_outcome_and_precedent_search_journey(
    page, live_server, two_organisations
):
    """A decision due for review is found from the Due For
    Review list, its outcome is recorded there, and it is afterwards
    findable by precedent search -- all three new surfaces this release
    adds, and none of another organisation's decisions leak into any of
    them.
    """
    ours = two_organisations["ours"]
    other = two_organisations["other"]
    title = "Renew the managed-database contract %s" % uuid.uuid4().hex[:6]

    from app import create_app

    app = create_app("testing")
    with app.app_context():
        from app.models.user import User

        ours_org_id = User.query.filter_by(email=ours["email"]).one().organization_id
        other_org_id = User.query.filter_by(email=other["email"]).one().organization_id
        decision_id = _seed_decision_due_for_review(ours_org_id, title)
        _seed_decision_due_for_review(other_org_id, "Their contract renewal, not ours")

    _login(page, live_server, ours["email"])

    # Due for review: shows ours, never the other organisation's.
    page.goto(live_server + "/architecture/decisions/due-for-review", wait_until="domcontentloaded")
    expect(page.get_by_role("link", name=title)).to_be_visible()
    assert "not ours" not in page.content()
    page.get_by_role("link", name="Record Outcome").first.click()
    page.wait_for_url(re.compile(r"/architecture/decisions/%s/record-outcome$" % decision_id))

    # The original decision's context is shown read-only on the way to
    # recording an outcome, so the reviewer is not judging it blind.
    details = page.locator("details", has_text="Original decision record")
    details.locator("summary").click()
    expect(details).to_contain_text("Vendor contract signed for an initial term.")

    outcome_text = "Still the right vendor; renewed for another 12 months."
    page.fill("#review_outcome", outcome_text)
    page.get_by_role("button", name="Record Outcome").click()
    page.wait_for_url(re.compile(r"/architecture/decisions/%s$" % decision_id))

    # Recorded and rendered on the decision's own page.
    expect(page.get_by_text(outcome_text)).to_be_visible()

    # No longer due: recording an outcome with no next review date clears it.
    page.goto(live_server + "/architecture/decisions/due-for-review", wait_until="domcontentloaded")
    expect(page.get_by_role("link", name=title)).not_to_be_visible()

    # Precedent search finds it by title text, never the other organisation's.
    page.goto(live_server + "/architecture/decisions/precedent-search", wait_until="domcontentloaded")
    search_form = page.locator("form[action*='precedent-search']")
    search_form.locator("input[name=q]").fill("managed-database contract")
    search_form.get_by_role("button", name="Search", exact=True).click()
    page.wait_for_url(re.compile(r"/architecture/decisions/precedent-search\?"))
    expect(page.get_by_role("link", name=title)).to_be_visible()
    assert "not ours" not in page.content()

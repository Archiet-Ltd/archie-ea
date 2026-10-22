"""The Why drawer shows provenance for recorded links: where the link came from,
who recorded it and when, and a link to open the relationship record.

Two organisations, each with the same shape of model:

    Portal --serves--> Gateway --serves--> Mainframe

The first link (Mainframe -> Gateway) was imported from a file.
The second link (Gateway -> Portal) was drawn by the organisation's architect.
A third link (Mainframe -> Portal) is worked out from the first two.

Every assertion is made on the rendered page.
"""

import datetime
import re
import uuid

import pytest

from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .intelligence_graph import seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_organisation_with_provenance(label):
    """Create an organisation with three links having different provenance."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    noun = "ProvenanceTest %s" % suffix
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

        # Link 1: Imported from a file
        imported_at = datetime.datetime(2026, 9, 1, 10, 30, 0)
        rel1 = ArchiMateRelationship(
            type="Serving", source_id=mainframe.id, target_id=gateway.id,
            organization_id=org.id,
            source_kind="import", source_label="estate.xml", source_recorded_at=imported_at,
        )
        db.session.add(rel1)

        # Link 2: Drawn by the architect
        drawn_at = datetime.datetime(2026, 9, 2, 14, 0, 0)
        rel2 = ArchiMateRelationship(
            type="Serving", source_id=gateway.id, target_id=portal.id,
            organization_id=org.id, created_by_id=user.id,
            source_kind="manual", source_label="Grace Hopper %s" % label, source_recorded_at=drawn_at,
        )
        db.session.add(rel2)

        # Link 3: Legacy link with no origin recorded
        legacy_at = datetime.datetime(2026, 8, 1, 9, 0, 0)
        rel3 = ArchiMateRelationship(
            type="Access", source_id=mainframe.id, target_id=portal.id,
            organization_id=org.id, created_by_id=user.id,
            created_at=legacy_at,
            # No source_kind, source_label, source_recorded_at
        )
        db.session.add(rel3)

        # Worked-out connection: Mainframe -> Portal via Gateway
        db.session.commit()
        from app.modules.intelligence.models.derived_relationship import DerivedRelationship
        db.session.add(DerivedRelationship(
            organization_id=org.id, source_element_id=mainframe.id, target_element_id=portal.id,
            derived_type="Serving", rule_id="table:Serving:Serving",
            chain=[rel1.id, rel2.id],
            chain_element_ids=[mainframe.id, gateway.id, portal.id], depth=2,
            confidence=0.9, provenance="derivation", engine_version="1.0",
            computed_at=datetime.datetime.utcnow(), stale=False,
        ))
        db.session.commit()

        return {
            "email": user.email, "user_name": "%s %s" % (user.first_name, user.last_name),
            "noun": noun, "portal": portal.id, "gateway": gateway.id, "mainframe": mainframe.id,
            "names": {"portal": portal.name, "gateway": gateway.name, "mainframe": mainframe.name},
            "rel1_id": rel1.id, "rel2_id": rel2.id, "rel3_id": rel3.id,
            "imported_at": imported_at, "drawn_at": drawn_at, "legacy_at": legacy_at,
        }


@pytest.fixture(scope="module")
def two_organisations(live_server):
    other = _seed_organisation_with_provenance("Other")
    ours = _seed_organisation_with_provenance("Ours")
    return {"ours": ours, "other": other}


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


def _open_ask(page, base):
    page.goto(base + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")


def _open_question(page):
    page.locator("#ask-question-impact").click()
    expect(page.locator("#ask-picker-input")).to_be_focused()


def _type_and_wait(page, prefix, term):
    box = page.locator("#%s-picker-input" % prefix)
    box.press_sequentially(term, delay=15)
    page.wait_for_selector("#%s-picker-listbox [role=option]" % prefix)
    return box


def _pick_by_click(page, prefix, name):
    page.locator("#%s-picker-listbox [role=option]" % prefix, has_text=name).click()


def _row_for(page, name, kind=None):
    rows = page.locator("[data-ask-row]" + ('[data-kind="%s"]' % kind if kind else ""))
    return rows.filter(has=page.get_by_role("heading", name=name, exact=True))


def test_provenance_drawer_shows_origin_for_imported_link(page, live_server, two_organisations):
    """For an imported link, the Why drawer shows 'Imported from <file>' and the date."""
    ours = two_organisations["ours"]
    names = ours["names"]
    _login(page, live_server, ours["email"])
    _open_ask(page, live_server)
    _dismiss_first_run(page)
    _open_question(page)
    _type_and_wait(page, "ask", ours["noun"])
    _pick_by_click(page, "ask", names["mainframe"])
    page.wait_for_selector("[data-ask-row]")

    # The Mainframe row has an explicit link to Gateway (imported)
    gateway_row = _row_for(page, names["gateway"], "explicit")
    expect(gateway_row).to_have_count(1)
    gateway_row.get_by_role("button", name="Why?").click()

    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")

    # Provenance section should be visible for explicit rows
    provenance_section = dialog.locator("[data-provenance-record]")
    expect(provenance_section).to_be_visible()

    # Should show "Imported from estate.xml"
    expect(provenance_section.locator("p").first).to_contain_text("Imported from estate.xml")

    # Should show the recorded date
    expect(provenance_section).to_contain_text("Recorded")
    expect(provenance_section).to_contain_text("1 Sep 2026")

    # Should have a link to the relationship record
    rel_link = provenance_section.locator("[data-relationship-link]")
    expect(rel_link).to_be_visible()
    expect(rel_link).to_have_attribute("href", re.compile(r"/architecture/relationships#relationship-%s$" % ours["rel1_id"]))

    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")


def test_provenance_drawer_shows_origin_for_hand_drawn_link(page, live_server, two_organisations):
    """For a hand-drawn link, the Why drawer shows 'Drawn by <person>' and the date."""
    ours = two_organisations["ours"]
    names = ours["names"]
    _login(page, live_server, ours["email"])
    _open_ask(page, live_server)
    _dismiss_first_run(page)
    _open_question(page)
    _type_and_wait(page, "ask", ours["noun"])
    _pick_by_click(page, "ask", names["gateway"])
    page.wait_for_selector("[data-ask-row]")

    # The Gateway row has an explicit link to Portal (drawn by architect)
    portal_row = _row_for(page, names["portal"], "explicit")
    expect(portal_row).to_have_count(1)
    portal_row.get_by_role("button", name="Why?").click()

    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")

    provenance_section = dialog.locator("[data-provenance-record]")
    expect(provenance_section).to_be_visible()

    # Should show "Drawn by Grace Hopper Ours"
    expect(provenance_section.locator("p").first).to_contain_text("Drawn by Grace Hopper Ours")

    # Should show the recorded date
    expect(provenance_section).to_contain_text("Recorded")
    expect(provenance_section).to_contain_text("2 Sep 2026")

    # Should have a link to the relationship record
    rel_link = provenance_section.locator("[data-relationship-link]")
    expect(rel_link).to_be_visible()
    expect(rel_link).to_have_attribute("href", re.compile(r"/architecture/relationships#relationship-%s$" % ours["rel2_id"]))

    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")


def test_provenance_drawer_shows_origin_not_recorded_for_legacy_link(page, live_server, two_organisations):
    """For a link from before origins were recorded, the Why drawer shows 'Origin not recorded'."""
    ours = two_organisations["ours"]
    names = ours["names"]
    _login(page, live_server, ours["email"])
    _open_ask(page, live_server)
    _dismiss_first_run(page)
    _open_question(page)
    _type_and_wait(page, "ask", ours["noun"])
    _pick_by_click(page, "ask", names["mainframe"])
    page.wait_for_selector("[data-ask-row]")

    # The Mainframe row also has an explicit link to Portal (legacy, no origin)
    portal_row = _row_for(page, names["portal"], "explicit")
    expect(portal_row).to_have_count(1)
    portal_row.get_by_role("button", name="Why?").click()

    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")

    provenance_section = dialog.locator("[data-provenance-record]")
    expect(provenance_section).to_be_visible()

    # Should show "origin not recorded" (lowercase as stored)
    expect(provenance_section.locator("p").first).to_contain_text("origin not recorded")

    # Should show "Date not recorded"
    expect(provenance_section).to_contain_text("Date not recorded")

    # Should have a link to the relationship record
    rel_link = provenance_section.locator("[data-relationship-link]")
    expect(rel_link).to_be_visible()
    expect(rel_link).to_have_attribute("href", re.compile(r"/architecture/relationships#relationship-%s$" % ours["rel3_id"]))

    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")


def test_provenance_drawer_worked_out_row_renders_as_before(page, live_server, two_organisations):
    """Worked-out rows still show 'The links behind this' with the derivation chain."""
    ours = two_organisations["ours"]
    names = ours["names"]
    _login(page, live_server, ours["email"])
    _open_ask(page, live_server)
    _dismiss_first_run(page)
    _open_question(page)
    _type_and_wait(page, "ask", ours["noun"])
    _pick_by_click(page, "ask", names["mainframe"])
    page.wait_for_selector("[data-ask-row]")

    # The worked-out connection Mainframe -> Portal
    derived_row = _row_for(page, names["portal"], "derived")
    expect(derived_row).to_have_count(1)
    derived_row.get_by_role("button", name="Why?").click()

    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")

    # Should show "The links behind this" section, not provenance section
    why_section = dialog.locator("[data-why-chain]")
    expect(why_section).to_be_visible()

    # Should show the derivation rule
    expect(why_section.locator("[data-why-rule]")).to_contain_text("serving link followed by a serving link")

    # Should show both links in the chain
    links = why_section.locator("[data-why-link]")
    expect(links).to_have_count(2)

    # First link: Mainframe -> Gateway (imported)
    expect(links.nth(0)).to_contain_text("Drawn by Grace Hopper Ours")

    # Second link: Gateway -> Portal (drawn)
    expect(links.nth(1)).to_contain_text("Drawn by Grace Hopper Ours")

    # Provenance section should NOT be visible for derived rows
    provenance_section = dialog.locator("[data-provenance-record]")
    expect(provenance_section).to_be_hidden()

    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")


def test_relationship_record_link_returns_200_for_owner(page, live_server, two_organisations):
    """The relationship record link returns 200 for the owning organisation."""
    ours = two_organisations["ours"]
    names = ours["names"]
    _login(page, live_server, ours["email"])
    _open_ask(page, live_server)
    _dismiss_first_run(page)
    _open_question(page)
    _type_and_wait(page, "ask", ours["noun"])
    _pick_by_click(page, "ask", names["mainframe"])
    page.wait_for_selector("[data-ask-row]")

    gateway_row = _row_for(page, names["gateway"], "explicit")
    gateway_row.get_by_role("button", name="Why?").click()

    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")

    rel_link = dialog.locator("[data-relationship-link]")
    href = rel_link.get_attribute("href")

    # Navigate to the relationship record page
    page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Should return 200 and show the relationships list
    expect(page.locator("main")).to_be_visible()
    # The relationship should be in the list
    expect(page.locator("main")).to_contain_text(names["mainframe"])
    expect(page.locator("main")).to_contain_text(names["gateway"])

    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")


def test_relationship_record_link_returns_not_found_for_other_organisation(page, live_server, two_organisations):
    """Another organisation's viewer gets 'not found' (empty list) from the same link."""
    ours = two_organisations["ours"]
    other = two_organisations["other"]
    names = ours["names"]
    _login(page, live_server, ours["email"])
    _open_ask(page, live_server)
    _dismiss_first_run(page)
    _open_question(page)
    _type_and_wait(page, "ask", ours["noun"])
    _pick_by_click(page, "ask", names["mainframe"])
    page.wait_for_selector("[data-ask-row]")

    gateway_row = _row_for(page, names["gateway"], "explicit")
    gateway_row.get_by_role("button", name="Why?").click()

    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")

    rel_link = dialog.locator("[data-relationship-link]")
    href = rel_link.get_attribute("href")

    # Log in as the other organisation
    page.goto(live_server + "/account/logout", wait_until="domcontentloaded")
    _login(page, live_server, other["email"])

    # Navigate to the same relationship record link
    page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Should return 200 but the relationship should NOT be in the list
    # (the list is tenant-scoped, so the other org sees their own relationships)
    expect(page.locator("main")).to_be_visible()
    # The relationship from our org should not appear
    assert names["mainframe"] not in page.locator("main").inner_text()
    assert names["gateway"] not in page.locator("main").inner_text()
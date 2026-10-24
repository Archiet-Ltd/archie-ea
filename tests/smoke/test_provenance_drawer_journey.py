"""The answer cards in the AI chat show provenance for every connection: where a recorded
link came from, who recorded it and when, a link to open the relationship record, and for a
worked-out connection the links it was worked out from.

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

from .chat_ask import ask, role_of
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
        # The origin columns arrive with the impact-row origin work; where the
        # model does not have them yet the link is simply recorded without one.
        has_origin = hasattr(ArchiMateRelationship, "source_kind")
        rel1 = ArchiMateRelationship(
            type="Serving", source_id=mainframe.id, target_id=gateway.id,
            organization_id=org.id,
            **(dict(source_kind="import", source_label="estate.xml",
                    source_recorded_at=imported_at) if has_origin else {}),
        )
        db.session.add(rel1)

        # Link 2: Drawn by the architect
        drawn_at = datetime.datetime(2026, 9, 2, 14, 0, 0)
        rel2 = ArchiMateRelationship(
            type="Serving", source_id=gateway.id, target_id=portal.id,
            organization_id=org.id, created_by_id=user.id,
            **(dict(source_kind="manual", source_label="Grace Hopper %s" % label,
                    source_recorded_at=drawn_at) if has_origin else {}),
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
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, timezone_id="UTC", locale="en-GB")
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


def _row_for(card, name, kind):
    """The connection card for *name* reached by a link of *kind*."""
    return card.locator("[data-slot='card']").filter(
        has=card.page.locator("[data-answer-row][data-kind='%s']" % kind)
    ).filter(has=card.page.get_by_role("link", name=name, exact=True))


def _open_row(page, base, org, centre, other, kind):
    """Sign in as the organisation's architect, ask what depends on `centre` in the chat,
    and return the card for `other`."""
    names = org["names"]
    assert role_of(org["email"]) == "enterprise_architect"  # the persona, not an administrator
    _login(page, base, org["email"])
    card = ask(page, base, "What breaks if %s fails" % names[centre])
    row = _row_for(card, names[other], kind)
    expect(row).to_have_count(1)
    return row


def _one_date(row, pattern):
    """Exactly one date line, in the product's date format, and no 'not recorded' beside it."""
    dates = row.locator("[data-provenance-date]")
    expect(dates).to_have_count(1)
    expect(dates).to_have_text(re.compile(pattern))
    assert "Date not recorded" not in row.text_content()


def test_card_says_imported_from_the_file_and_when(page, live_server, two_organisations):
    """A CTO asks what depends on the mainframe; the imported link says where it came from."""
    ours = two_organisations["ours"]
    row = _open_row(page, live_server, ours, "mainframe", "gateway", "explicit")
    expect(row.locator("[data-provenance-text]")).to_have_text("Imported from estate.xml")
    _one_date(row, r"^Recorded 1 Sept? 2026, 10:30$")


def test_card_says_drawn_by_the_person_and_when(page, live_server, two_organisations):
    """A hand-drawn link says who drew it and when."""
    ours = two_organisations["ours"]
    row = _open_row(page, live_server, ours, "gateway", "portal", "explicit")
    expect(row.locator("[data-provenance-text]")).to_have_text("Drawn by Grace Hopper Ours")
    _one_date(row, r"^Recorded 2 Sept? 2026, 14:00$")


def test_card_says_origin_not_recorded_for_an_old_link(page, live_server, two_organisations):
    """A link from before origins were kept says so, and shows the only date it has."""
    ours = two_organisations["ours"]
    row = _open_row(page, live_server, ours, "mainframe", "portal", "explicit")
    expect(row.locator("[data-provenance-text]")).to_have_text("Origin not recorded")
    _one_date(row, r"^Recorded 1 Aug 2026, 09:00$")


def test_card_link_opens_the_relationship_record(page, live_server, two_organisations):
    """Clicking 'Open relationship record' lands on a page that shows that very link."""
    ours = two_organisations["ours"]
    other = two_organisations["other"]
    names = ours["names"]
    row = _open_row(page, live_server, ours, "mainframe", "gateway", "explicit")
    row.get_by_role("link", name="Open relationship record").click()
    page.wait_for_url(re.compile(r"/architecture/relationships#relationship-%s$" % ours["rel1_id"]))

    record = page.locator("#relationship-%s" % ours["rel1_id"])
    expect(record).to_have_count(1)
    expect(record).to_contain_text(names["mainframe"])
    expect(record).to_contain_text(names["gateway"])
    expect(record).to_contain_text("Serving")
    # The page is this organisation's own and nobody else's.
    assert other["noun"] not in page.locator("main").inner_text()


def test_worked_out_card_still_shows_the_links_behind_it(page, live_server, two_organisations):
    """As before: a worked-out connection explains itself from the links it was worked out from."""
    ours = two_organisations["ours"]
    names = ours["names"]
    row = _open_row(page, live_server, ours, "mainframe", "portal", "derived")
    expect(row.locator("[data-why-rule]")).to_contain_text("serving link followed by a serving link")
    links = row.locator("[data-why-link]")
    expect(links).to_have_count(2)
    expect(links.nth(0)).to_contain_text(names["mainframe"])
    expect(links.nth(0)).to_contain_text(names["gateway"])
    expect(links.nth(1)).to_contain_text(names["gateway"])
    expect(links.nth(1)).to_contain_text(names["portal"])
    # The recorded-link section belongs to recorded connections only.
    assert row.locator("[data-provenance-text]").count() == 0


def test_worked_out_links_name_their_origin_once(page, live_server, two_organisations):
    """Each link behind a worked-out connection reads 'Imported from <file>' or 'Drawn by <person>', never 'Drawn by Imported from'."""
    ours = two_organisations["ours"]
    row = _open_row(page, live_server, ours, "mainframe", "portal", "derived")
    links = row.locator("[data-why-link]")
    expect(links).to_have_count(2)
    first = re.sub(r"\s+", " ", links.nth(0).inner_text())
    second = re.sub(r"\s+", " ", links.nth(1).inner_text())
    assert "Imported from estate.xml" in first
    assert "Drawn by Imported" not in first
    assert "Drawn by Grace Hopper Ours" in second


def test_owner_opens_the_relationship_list_with_their_links(page, live_server, two_organisations):
    """The list the drawer link opens is there for the owner, with both ends of each link."""
    ours = two_organisations["ours"]
    names = ours["names"]
    _login(page, live_server, ours["email"])
    page.goto(live_server + "/architecture/relationships", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    main = page.locator("main")
    expect(main).to_contain_text(names["mainframe"])
    expect(main).to_contain_text(names["gateway"])
    expect(main).to_contain_text(names["portal"])


def test_relationship_record_is_not_found_for_another_organisation(page, live_server, two_organisations):
    """Another organisation's viewer opening the same address sees their own links, never ours."""
    ours = two_organisations["ours"]
    other = two_organisations["other"]
    _login(page, live_server, other["email"])
    page.goto(
        live_server + "/architecture/relationships#relationship-%s" % ours["rel1_id"],
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    main = page.locator("main")
    expect(main).to_contain_text(other["names"]["gateway"])
    assert ours["noun"] not in main.inner_text()
    expect(page.locator("#relationship-%s" % ours["rel1_id"])).to_have_count(0)

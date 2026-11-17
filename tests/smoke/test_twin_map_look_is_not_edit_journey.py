"""Looking at a neighbourhood is not an edit, and every twin-map address draws one.

The person: an enterprise architect who opens the Composer from a twin-map link
to look at what a system connects to. The goal: read it and move on without a
"leave this page?" prompt, find the Digital twin entry opening the working
screen, and have the bare twin-map address show something worth looking at.
"""
import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.intelligence_graph import seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

APP = '[x-data^="composerApp"]'
SETTLED_MS = 4500


def _assert_persona(email):
    """The signed-in person is the enterprise architect, not an administrator."""
    from app import create_app
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "enterprise_architect", user.enterprise_role
        assert user.role.name not in ("Administrator", "Platform Admin"), user.role.name
        assert not user.is_org_admin


def _add_connections(org_id, element_id, how_many, prefix):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    app = create_app("testing")
    with app.app_context():
        for n in range(how_many):
            other = ArchiMateElement(name="%s %d" % (prefix, n), type="Node", layer="technology",
                                     organization_id=org_id)
            db.session.add(other)
            db.session.commit()
            db.session.add(ArchiMateRelationship(type="Serving", source_id=element_id, target_id=other.id,
                                                 organization_id=org_id))
            db.session.commit()


@pytest.fixture(scope="module")
def world():
    mine = create_fresh_org("enterprise_architect")
    graph = seed_impact_graph(mine["org_id"], "Hubmap")
    # The service now has four connections; every other element has fewer.
    _add_connections(mine["org_id"], graph["service"], 2, "Hubmap Extra")
    theirs = create_fresh_org("enterprise_architect")
    their_graph = seed_impact_graph(theirs["org_id"], "Rival")
    # Another organisation's element is better connected than anything in mine.
    _add_connections(theirs["org_id"], their_graph["service"], 9, "Rival Extra")
    return {"mine": mine, "graph": graph, "theirs": theirs, "their_graph": their_graph}


def _wait_drawn(page, names):
    page.wait_for_function(
        """([sel, n]) => { const a = Alpine.$data(document.querySelector(sel));
            return !!a.focusView && a.graph.getElements().filter(c => c.get('elementId') != null).length >= n; }""",
        arg=[APP, len(names)], timeout=5000)
    drawn = page.evaluate(
        """(sel) => Alpine.$data(document.querySelector(sel)).graph.getElements()
            .filter(c => c.get('elementId') != null).map(c => c.get('elName'))""", APP)
    for name in names:
        assert name in drawn, "%s was not drawn: %r" % (name, drawn)


def _dirty(page):
    return page.evaluate("(sel) => Alpine.$data(document.querySelector(sel)).viewpointDirty", APP)


def _leave_by_link(page, dialogs):
    """Click a real link in the page's sidebar and report whether a prompt came up."""
    page.on("dialog", lambda d: (dialogs.append(d.type), d.accept()))
    link = page.locator("a[href='/dashboard/']:visible, a[href^='/dashboard']:visible").first
    href = link.get_attribute("href")
    link.click()
    page.wait_for_url(lambda url: "/archimate/composer" not in url, timeout=PAGE_TIMEOUT)
    return href


def test_opened_to_look_at_leaves_without_a_prompt(page, live_server, world):
    email = world["mine"]["emails"]["enterprise_architect"]
    _assert_persona(email)
    sign_in(page, live_server, email)
    page.goto("%s/archimate/composer?element=%d" % (live_server, world["graph"]["service"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _wait_drawn(page, [world["graph"]["names"]["service"], world["graph"]["names"]["gateway"]])
    page.wait_for_timeout(SETTLED_MS)
    # A person has clicked on the page (a prompt is only ever shown after that).
    page.locator("#composer-focus-banner").click()
    assert _dirty(page) is False
    dialogs = []
    _leave_by_link(page, dialogs)
    assert dialogs == [], "a prompt appeared for a view that was only looked at: %r" % dialogs
    expect(page.locator("main, body").first).to_be_visible()


def test_changing_the_view_still_asks_before_leaving(page, live_server, world):
    sign_in(page, live_server, world["mine"]["emails"]["enterprise_architect"])
    page.goto("%s/archimate/composer?element=%d" % (live_server, world["graph"]["service"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _wait_drawn(page, [world["graph"]["names"]["service"]])
    page.wait_for_timeout(SETTLED_MS)
    box = page.evaluate(
        """(sel) => { const a = Alpine.$data(document.querySelector(sel));
            const c = a.graph.getElements().find(e => e.get('elementId') != null);
            const r = a.paper.findViewByModel(c).el.getBoundingClientRect();
            return {x: r.x + r.width / 2, y: r.y + r.height / 2}; }""", APP)
    page.mouse.move(box["x"], box["y"])
    page.mouse.down()
    page.mouse.move(box["x"] + 60, box["y"] + 40, steps=6)
    page.mouse.up()
    assert _dirty(page) is True
    dialogs = []
    _leave_by_link(page, dialogs)
    assert dialogs == ["beforeunload"], dialogs


def test_signed_in_digital_twin_entry_opens_the_composer(page, live_server, world):
    email = world["mine"]["emails"]["enterprise_architect"]
    _assert_persona(email)
    sign_in(page, live_server, email)
    page.goto("%s/modules/digital-twin" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert "/archimate/composer" in page.url, page.url
    _wait_drawn(page, [world["graph"]["names"]["service"], world["graph"]["names"]["gateway"]])


def test_signed_out_digital_twin_entry_is_the_public_page(page, live_server):
    page.goto("%s/modules/digital-twin" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert "/modules/digital-twin" in page.url, page.url
    expect(page.get_by_role("heading", level=1).first).to_contain_text("Digital twin")


def test_bare_twin_map_address_draws_the_most_connected_element_and_says_so(page, live_server, world):
    email = world["mine"]["emails"]["enterprise_architect"]
    _assert_persona(email)
    sign_in(page, live_server, email)
    page.goto("%s/intelligence/twin-map" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert "element=%d" % world["graph"]["service"] in page.url, page.url
    names = world["graph"]["names"]
    _wait_drawn(page, [names["service"], names["gateway"], names["portal"]])
    expect(page.locator("#composer-focus-title")).to_contain_text(names["service"])
    expect(page.locator("#composer-focus-most-connected")).to_contain_text("most connected element")
    # Nothing from the other organisation's better connected element is drawn.
    drawn = page.evaluate(
        """(sel) => Alpine.$data(document.querySelector(sel)).graph.getElements()
            .map(c => c.get('elName') || '')""", APP)
    assert not any("Rival" in n for n in drawn), drawn


def test_an_address_with_an_element_does_not_claim_it_is_the_most_connected(page, live_server, world):
    sign_in(page, live_server, world["mine"]["emails"]["enterprise_architect"])
    page.goto("%s/intelligence/twin-map?element=%d" % (live_server, world["graph"]["gateway"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _wait_drawn(page, [world["graph"]["names"]["gateway"]])
    expect(page.locator("#composer-focus-most-connected")).to_have_count(0)

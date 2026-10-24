"""A person asks what breaks in the AI chat, reads the cards, and opens the element in the Composer: in a real browser.

This drives the chat exactly as a person does, against a tenant that holds a small chain of
connected things:

    Service --serves--> Gateway --serves--> Ops Team
    Service --serves--> Portal
    Service ==worked out==> Ops Team          (a connection nobody drew)

The Gateway has an owner; the Portal and the Ops Team have none. Nothing is asked of the person
before the answer: no layer, no type, no vocabulary term.

The picker, drawer, keyboard and focus journeys that used to live here drove the retired Ask page's
own controls (a combobox, a dialog, a Full detail toggle); the chat has none of them, and its own
keyboard, focus and accessibility checks are in test_ask_in_the_chat_journey.py and the
accessibility audit. Every assertion is made on the rendered page.
"""

import re
import uuid

import pytest

from playwright.sync_api import expect

from .chat_ask import ask, role_of, sign_in, sign_in_as
from .conftest import PAGE_TIMEOUT, PASSWORD
from .intelligence_graph import mark_derived_stale, seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


# --- the tenant the journey reads -------------------------------------------


@pytest.fixture(scope="module")
def graph(seeded, live_server):
    """A chain of connected elements in the seeded organisation, with one owner
    and one worked-out connection."""
    return seed_impact_graph(seeded["ids"]["org"])


@pytest.fixture
def bare_tenant(live_server):
    """A fresh organisation with two connected elements, no owner and no worked-out
    connections, for the states that depend on something being absent. One per test:
    asking for the connections to be worked out changes the tenant it runs in."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    out = {"noun": "Nightshift %s" % suffix}
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Bare Org %s" % suffix, slug="bare-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        user = User(
            email="bare.%s@example.com" % suffix, first_name="Bare", last_name="Tenant",
            organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()

        def element(name, kind, layer):
            row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org.id)
            db.session.add(row)
            db.session.commit()
            return row

        head = element("%s Batch" % out["noun"], "ApplicationComponent", "application")
        tail = element("%s Report" % out["noun"], "ApplicationComponent", "application")
        db.session.add(ArchiMateRelationship(
            type="Serving", source_id=head.id, target_id=tail.id, organization_id=org.id,
        ))
        db.session.commit()
        out.update(email=user.email, head=head.id, tail=tail.id, org=org.id,
                   head_name=head.name, tail_name=tail.name)
    return out


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _card_for(answer, name, kind=None):
    """The connection card named *name*, optionally only one reached by a link of *kind*."""
    page = answer.page
    cards = answer.locator("[data-slot='card']").filter(has=page.get_by_role("link", name=name, exact=True))
    if kind:
        cards = cards.filter(has=page.locator("[data-answer-row][data-kind='%s']" % kind))
    return cards


@pytest.mark.parametrize("persona", ["solution_architect", "enterprise_architect"])
def test_a_first_time_user_gets_an_answer_from_a_typed_noun_and_can_open_the_element(
    persona, page, live_server, seeded, graph
):
    """From the chat to the map, as two personas, having declared nothing.

    Type what breaks if the service fails, read a card per connection (an owner where there
    is one, "No owner recorded" where there is not), read that the one nobody drew was worked
    out, and follow a connection into the Composer to see the element drawn with what it
    connects to.
    """
    sign_in_as(page, live_server, seeded, persona)
    names = graph["names"]
    answer = ask(page, live_server, "What breaks if %s fails" % names["service"])
    assert answer.locator("[data-answer-row]").count() == 4
    # The total on the answer card is the number of cards beneath it.
    expect(answer).to_contain_text("4 connections in your records")

    gateway = _card_for(answer, names["gateway"], "explicit")
    expect(gateway.get_by_label("Owner: " + graph["owner"])).to_have_count(1)
    portal = _card_for(answer, names["portal"], "explicit")
    expect(portal).to_contain_text("No owner recorded")

    # The connection nobody drew says so.
    derived = _card_for(answer, names["ops"], "derived")
    expect(derived).to_have_count(1)
    expect(derived.locator("[data-citation]")).to_have_text("Worked out from the model")
    expect(derived.locator("[data-why-link]")).to_have_count(2)

    # A connection opens its element in the Composer, drawn with what it connects to.
    gateway.get_by_role("link", name=names["gateway"], exact=True).click()
    page.wait_for_url(re.compile(r"/archimate/composer\?element=%s$" % graph["gateway"]))
    page.wait_for_function(
        "() => { const a = Alpine.$data(document.querySelector('[x-data^=\"composerApp\"]'));"
        " return !!a.focusView && a.graph.getElements().length >= 2; }")


def test_missing_owner_missing_health_and_no_derivation_read_as_distinct_reasons(
    page, live_server, bare_tenant
):
    """A tenant with no owner, no health signal and nothing worked out shows three
    different statements, each as text a screen reader reads, and not a zero."""
    assert role_of(bare_tenant["email"]) == "solution_architect"
    sign_in(page, live_server, bare_tenant["email"])
    answer = ask(page, live_server, "What breaks if %s fails" % bare_tenant["head_name"])
    card = _card_for(answer, bare_tenant["tail_name"])
    expect(card).to_have_count(1)
    expect(card).to_contain_text("No owner recorded")
    expect(card).to_contain_text("Health not recorded")
    expect(answer.locator("[data-not-computed-notice]")).to_contain_text(
        "We haven't worked out the indirect connections for your model yet")
    # No zero stands in for an absent value anywhere in the answer's own figures.
    cells = answer.evaluate(
        "el => Array.from(el.querySelectorAll('*')).filter(e => e.children.length === 0)"
        ".map(e => e.textContent.trim()).filter(t => t === '0' || t === '0%')")
    assert cells == []


@pytest.fixture
def stale_tenant(live_server):
    """A fresh organisation whose one worked-out connection has gone out of date.

    It is made stale the way the product makes it stale: a person edits a relationship
    the connection was worked out from, and the invalidation listener marks it. One per
    test, in an organisation of its own, because asking for the connections to be worked
    out again rewrites every worked-out connection in the organisation it runs in."""
    import datetime

    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Stale Org %s" % suffix, slug="stale-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        user = User(
            email="stale.%s@example.com" % suffix, first_name="Stale", last_name="Tenant",
            organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
            onboarding_completed_at=datetime.datetime.utcnow(),
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        org_id, email = org.id, user.email
    stale_graph = seed_impact_graph(org_id, "Stalepay")
    stored = mark_derived_stale(stale_graph)
    stale_graph.update(email=email, stored=stored)
    return stale_graph


def test_the_tenant_really_is_stale_the_way_the_product_makes_it_stale(stale_tenant):
    """Ground truth for the test below, read from the stored row: the edit alone set stale,
    its time and its reason."""
    stored = stale_tenant["stored"]
    assert stored["stale"] is True
    assert stored["stale_since"] is not None
    assert stored["stale_reason"] == "relationship_updated"


def test_the_chat_says_when_worked_out_connections_are_out_of_date_and_leaves_them_out(
    page, live_server, stale_tenant
):
    """An out-of-date worked-out connection is not listed as if it were current: the answer
    lists the three recorded connections and says the worked-out ones are out of date, with the
    button to work them out again, which brings back a current connection."""
    assert role_of(stale_tenant["email"]) == "solution_architect"
    sign_in(page, live_server, stale_tenant["email"])
    names = stale_tenant["names"]
    answer = ask(page, live_server, "What breaks if %s fails" % names["service"])
    assert answer.locator("[data-answer-row]").count() == 3
    expect(answer.locator("[data-answer-row][data-kind='derived']")).to_have_count(0)
    expect(answer.locator("[data-stale-notice]")).to_contain_text("out of date")
    answer.get_by_role("button", name="Work them out now").click()
    fresh = page.get_by_test_id("estate-answer").nth(0)
    expect(fresh.locator("[data-answer-row][data-kind='derived']")).to_have_count(1, timeout=PAGE_TIMEOUT)
    expect(fresh.locator("[data-stale-notice]")).to_have_count(0)

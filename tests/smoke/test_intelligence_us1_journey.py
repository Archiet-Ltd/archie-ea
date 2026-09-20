"""A person asks a question, reads the answer, asks why, and sees the map: in a real browser.

This drives the Ask page, the provenance drawer and the Twin map exactly as a
person does, against a tenant that holds a small chain of connected things:

    Service --serves--> Gateway --serves--> Ops Team
    Service --serves--> Portal
    Service ==worked out==> Ops Team          (a connection nobody drew)

The Gateway has an owner; the Portal and the Ops Team have none. Nothing is
asked of the person before the answer: no layer, no type, no vocabulary term.

What each test proves is stated in its docstring. Every assertion is made on the
rendered page; where a test compares the page with the answer the server gave, it
asks the server itself with the same session, so the two cannot drift.
"""

import json
import os
import re
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

MIN_TARGET = 24


# --- the tenant the journey reads -------------------------------------------


@pytest.fixture(scope="module")
def graph(seeded, live_server):
    """A chain of connected elements in the seeded organisation, with one owner
    and one worked-out connection."""
    import datetime

    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    noun = "Ledgerpay %s" % suffix
    org_id = seeded["ids"]["org"]
    out = {"noun": noun, "org": org_id}
    with app.app_context():
        def element(name, kind, layer):
            row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org_id)
            db.session.add(row)
            db.session.commit()
            return row

        service = element("%s Service" % noun, "ApplicationComponent", "application")
        gateway = element("%s Gateway" % noun, "ApplicationComponent", "application")
        ops = element("%s Ops Team" % noun, "BusinessActor", "business")
        portal = element("%s Portal" % noun, "Node", "technology")

        def serves(source, target):
            rel = ArchiMateRelationship(
                type="Serving", source_id=source.id, target_id=target.id, organization_id=org_id
            )
            db.session.add(rel)
            db.session.commit()
            return rel

        first = serves(service, gateway)
        second = serves(gateway, ops)
        serves(service, portal)

        db.session.add(DerivedRelationship(
            organization_id=org_id,
            source_element_id=service.id,
            target_element_id=ops.id,
            derived_type="Serving",
            rule_id="serving-through-serving",
            chain=[first.id, second.id],
            chain_element_ids=[service.id, gateway.id, ops.id],
            depth=2,
            confidence=0.82,
            provenance="derivation",
            engine_version="1.0",
            computed_at=datetime.datetime.utcnow(),
            stale=False,
        ))
        db.session.commit()

        component = ApplicationComponent(
            name="%s Gateway component" % noun, organization_id=org_id,
            archimate_element_id=gateway.id,
        )
        db.session.add(component)
        db.session.commit()
        unit = OrganizationUnit(name="Payments Platform %s" % suffix)
        db.session.add(unit)
        db.session.commit()
        db.session.add(ApplicationOwnership(
            application_id=component.id, organization_unit_id=unit.id,
            ownership_type="Business Owner",
        ))
        db.session.commit()

        out.update(
            service=service.id, gateway=gateway.id, ops=ops.id, portal=portal.id,
            names={
                "service": service.name, "gateway": gateway.name,
                "ops": ops.name, "portal": portal.name,
            },
            owner=unit.name,
        )
    return out


@pytest.fixture(scope="module")
def bare_tenant(live_server):
    """A second organisation with two connected elements, no owner and no worked-out
    connections, for the states that depend on something being absent."""
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


@pytest.fixture(scope="module")
def empty_tenant(live_server):
    """An organisation that holds nothing at all, with a user who has finished
    the first-run prompt."""
    import datetime

    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.models.vendor.vendor_organization import VendorOrganization

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        # The vendor count is global by construction, so a database that holds
        # any vendor organisation has no tenant that can read as empty.
        if db.session.query(db.func.count(VendorOrganization.id)).scalar():
            pytest.skip("vendor organisations exist in this database, so no tenant reads as empty")
        Role.insert_roles()
        org = Organization(name="Empty Org %s" % suffix, slug="empty-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        user = User(
            email="empty.%s@example.com" % suffix, first_name="Empty", last_name="Tenant",
            organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
            onboarding_completed_at=datetime.datetime.utcnow(),
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        return {"email": user.email}


# --- driving the page -------------------------------------------------------


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
    """The page's component has started, so a click will do something."""
    page.wait_for_function(
        "(f) => { const el = document.querySelector('[x-data=\"' + f + '()\"]');"
        " return !!(el && el._x_dataStack); }",
        arg=factory,
    )


def _open_ask(page, base):
    page.goto(base + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")


def _open_twin_map(page, base, element_id=None):
    url = base + "/intelligence/twin-map" + ("?element=%s" % element_id if element_id else "")
    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "twinMapSurface")


def _dismiss_first_run(page):
    try:
        page.eval_on_selector_all("[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
    except Exception:
        pass


def _type_and_wait(page, prefix, term):
    box = page.locator("#%s-picker-input" % prefix)
    box.press_sequentially(term, delay=15)
    page.wait_for_selector("#%s-picker-listbox [role=option]" % prefix)
    return box


def _pick_by_click(page, prefix, name):
    page.locator("#%s-picker-listbox [role=option]" % prefix, has_text=name).click()


def _rows(page):
    return page.locator("[data-ask-row]")


def _row_for(page, name, kind=None):
    rows = page.locator("[data-ask-row]" + ('[data-kind="%s"]' % kind if kind else ""))
    return rows.filter(has=page.get_by_role("heading", name=name, exact=True))


def _impact_api(page, base, element_id, **params):
    query = {"include_derived": "true", "max_depth": "3", "with_owner": "true"}
    query.update({k: str(v) for k, v in params.items()})
    response = page.context.request.get(
        "%s/api/v1/intelligence/impact/%s?%s" % (
            base, element_id, "&".join("%s=%s" % kv for kv in query.items()))
    )
    assert response.status == 200, response.text()
    return response.json()["data"]


def _ask_about(page, base, graph):
    """The whole first half of the journey with the mouse: sidebar, card, typing, choosing."""
    page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _dismiss_first_run(page)
    page.get_by_role("link", name="Ask a question").click()
    page.wait_for_url(re.compile(r"/intelligence/ask$"))
    _ready(page, "askSurface")
    page.locator("#ask-question-impact").click()
    _type_and_wait(page, "ask", graph["noun"])
    _pick_by_click(page, "ask", graph["names"]["service"])
    page.wait_for_selector("[data-ask-row]")


def _focus_is_on(page, locator):
    return page.evaluate(
        "(el) => document.activeElement === el", locator.element_handle()
    )


# --- item 1, 9, 21-23: the journey with a pointer ---------------------------


@pytest.mark.parametrize("persona", ["solution_architect", "enterprise_architect"])
def test_a_first_time_user_gets_an_answer_from_a_typed_noun_and_can_ask_why(
    persona, page, live_server, seeded, graph
):
    """From the sidebar to the map, as two personas, having declared nothing.

    Open Ask from the sidebar, open the question, type a noun, choose a match,
    read the rows (an owner where there is one, "Not recorded" where there is not),
    open the drawer from a connection nobody drew, read the sentence the server
    wrote, expand Full detail, follow View on twin map, move the hop depth, turn
    the worked-out connections off and on, and see the derived edge dashed.
    """
    _login(page, live_server, seeded["emails"][persona])
    _ask_about(page, live_server, graph)

    # Nothing was asked for: one text input, no layer or type control anywhere.
    assert page.locator("main select").count() == 0
    assert page.locator("main input[type=text], main input[role=combobox]").count() == 1
    names = graph["names"]
    assert _rows(page).count() == 4

    # An owner where there is one; a stated reason where there is not.
    gateway = _row_for(page, names["gateway"], "explicit")
    assert gateway.get_by_label("Owner: " + graph["owner"]).count() == 1
    portal = _row_for(page, names["portal"], "explicit")
    assert portal.get_by_label("Owner: Not recorded").count() == 1
    assert "Not recorded" in portal.inner_text()

    # The connection nobody drew says so, and asks nobody to trust it blindly.
    derived = _row_for(page, names["ops"], "derived")
    assert derived.count() == 1
    assert "We worked this out — nobody drew it directly." in derived.inner_text()

    # Why? opens the drawer with the server's own sentence, verbatim.
    derived.get_by_role("button", name="Why?").click()
    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")
    served = [
        row["relation"]["plain_terms"]
        for row in _impact_api(page, live_server, graph["service"])["rows"]
        if row["relation"]["kind"] == "derived"
    ]
    assert len(served) == 1 and served[0]
    sentence = dialog.locator("[data-plain-terms]")
    assert sentence.inner_text() == served[0]
    assert names["ops"] in served[0] and names["service"] in served[0]

    # Full detail is collapsed, opens in place, and holds the technical terms.
    toggle = dialog.locator("[data-full-detail-toggle]")
    assert toggle.get_attribute("aria-expanded") == "false"
    url_before = page.url
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"
    assert page.url == url_before
    assert len(page.context.pages) == 1
    detail = dialog.locator("[data-full-detail-region]")
    text = detail.inner_text()
    for expected in ("BusinessActor", "business", "serving-through-serving", "0.82", "1.0"):
        assert expected in text, (expected, text)
    for name in (names["service"], names["gateway"], names["ops"]):
        assert name in text  # the chain, as names

    # Close, then follow View on twin map from a row.
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    _row_for(page, names["gateway"], "explicit").get_by_role("link", name="View on twin map").click()
    page.wait_for_url(re.compile(r"/intelligence/twin-map\?element=%s$" % graph["gateway"]))
    _ready(page, "twinMapSurface")
    page.wait_for_selector("svg .intel-edge")
    assert page.locator("[data-map-row]").count() >= 1

    # The map centred on the Service, then hop depth and the worked-out toggle.
    _open_twin_map(page, live_server, graph["service"])
    page.wait_for_selector("svg .intel-edge")
    assert page.locator("svg path[data-kind=derived]").count() == 1
    assert page.locator("svg path[data-kind=explicit]").count() == 3
    assert page.locator("[data-map-row]").count() == 4

    slider = page.locator("#twin-hop-depth")
    slider.focus()
    with page.expect_request(lambda r: "max_depth=1" in r.url and "/impact/" in r.url):
        page.keyboard.press("Home")
    page.wait_for_function("document.querySelectorAll('[data-map-row]').length === 2")
    assert page.locator("svg path[data-kind=derived]").count() == 0
    with page.expect_request(lambda r: "max_depth=5" in r.url and "/impact/" in r.url):
        page.keyboard.press("End")
    page.wait_for_function("document.querySelectorAll('[data-map-row]').length === 4")

    toggle_box = page.locator("#twin-show-derived")
    with page.expect_request(lambda r: "include_derived=false" in r.url and "/impact/" in r.url):
        toggle_box.uncheck()
    page.wait_for_function("document.querySelectorAll('[data-map-row]').length === 3")
    assert page.locator("svg path[data-kind=derived]").count() == 0
    with page.expect_request(lambda r: "include_derived=true" in r.url and "/impact/" in r.url):
        toggle_box.check()
    page.wait_for_selector("svg path[data-kind=derived]")


def test_the_derived_edge_is_dashed_and_badged_and_explicit_edges_are_solid(
    page, live_server, seeded, graph
):
    """Derived-versus-explicit survives greyscale: it rides the stroke pattern and a
    written badge, read off the rendered SVG."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    _open_twin_map(page, live_server, graph["service"])
    page.wait_for_selector("svg path[data-kind=derived]")
    derived = page.locator("svg path[data-kind=derived]")
    assert derived.get_attribute("stroke-dasharray") in ("5,5", "5, 5")
    solid = page.locator("svg path[data-kind=explicit]")
    for index in range(solid.count()):
        assert solid.nth(index).get_attribute("stroke-dasharray") is None
    badges = page.locator("svg g.intel-badge")
    assert badges.count() == 1
    assert badges.locator("text").inner_text() == "Worked out"
    # No colour of its own: every fill and stroke comes from a token class.
    assert page.locator("svg [style*='#'], svg [fill^='#'], svg [stroke^='#']").count() == 0

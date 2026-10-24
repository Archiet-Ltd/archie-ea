"""The twin map opens the ArchiMate Composer, centred on the element.

The person: an enterprise architect who wants to see what one system connects to.
The goal: from the application's page, one click on the button, and the Composer
draws the application and the elements around it within five seconds; clicking a
connected element opens its details. The old twin map address, and every link that
used to lead there, goes to the same Composer view. Another organisation's element
answers 404, and an organisation with no model sees why it is empty and how to
fill it.
"""
import re

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import ask, sign_in_as
from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.intelligence_graph import seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DRAW_SECONDS = 5000
APP = '[x-data^="composerApp"]'


@pytest.fixture(scope="module")
def graph(seeded):
    return seed_impact_graph(seeded["ids"]["org"], "Twinmapped")


@pytest.fixture(scope="module")
def application_id(graph):
    from app import create_app
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        return ApplicationComponent.query.filter_by(archimate_element_id=graph["gateway"]).one().id


def _drawn_names(page):
    return page.evaluate(
        """(sel) => Alpine.$data(document.querySelector(sel)).graph.getElements()
            .filter(c => c.get('elementId') != null).map(c => c.get('elName'))""",
        APP,
    )


def _wait_drawn(page, expected_names):
    page.wait_for_function(
        """([sel, n]) => { const a = Alpine.$data(document.querySelector(sel));
            return !!a.focusView && a.graph.getElements().filter(c => c.get('elementId') != null).length >= n; }""",
        arg=[APP, len(expected_names)],
        timeout=DRAW_SECONDS,
    )
    drawn = _drawn_names(page)
    for name in expected_names:
        assert name in drawn, "%s was not drawn: %r" % (name, drawn)


def test_application_page_button_opens_the_composer_on_that_application(
    page, live_server, seeded, graph, application_id
):
    sign_in(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto("%s/applications/%d" % (live_server, application_id),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    link = page.get_by_role("link", name="Open in the Composer")
    expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
    link.click()
    page.wait_for_url(re.compile(r"/archimate/composer\?element=%d$" % graph["gateway"]), timeout=PAGE_TIMEOUT)

    names = graph["names"]
    # The application's element and what it connects to are drawn within five seconds.
    _wait_drawn(page, [names["gateway"], names["ops"]])
    expect(page.locator("#composer-focus-title")).to_contain_text(names["gateway"])

    # The connections are also a list, and the same rows are in it.
    page.locator("#composer-focus-list-toggle").click()
    rows = page.locator("[data-focus-row]")
    expect(rows.first).to_be_visible()
    expect(page.locator("#composer-focus-list")).to_contain_text(names["ops"])

    # Clicking a connected element opens its details.
    rows.filter(has_text=names["ops"]).get_by_role("button", name=names["ops"]).click()
    panel = page.locator(".detail-panel")
    expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(panel.locator('input[aria-label="Element name"]')).to_have_value(names["ops"])


def test_clicking_a_drawn_element_opens_its_details(page, live_server, seeded, graph):
    sign_in(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto("%s/archimate/composer?element=%d" % (live_server, graph["service"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    names = graph["names"]
    _wait_drawn(page, [names["service"], names["gateway"], names["portal"]])
    page.evaluate(
        """([sel, id]) => { const a = Alpine.$data(document.querySelector(sel));
            a.openFocusConnection(id); }""",
        [APP, graph["portal"]],
    )
    expect(page.locator(".detail-panel")).to_be_visible(timeout=PAGE_TIMEOUT)


def test_old_twin_map_address_goes_to_the_same_composer_view(page, live_server, seeded, graph):
    sign_in(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto("%s/intelligence/twin-map?element=%d" % (live_server, graph["service"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert re.search(r"/archimate/composer\?element=%d$" % graph["service"], page.url), page.url
    _wait_drawn(page, [graph["names"]["service"], graph["names"]["gateway"]])


def test_chat_connection_link_lands_on_the_composer_with_the_element_drawn(page, live_server, seeded, graph):
    sign_in_as(page, live_server, seeded, "enterprise_architect")
    answer = ask(page, live_server, "What breaks if %s fails" % graph["names"]["service"])
    first = answer.locator("[data-answer-row] a").first
    name = first.inner_text().strip()
    first.click()
    page.wait_for_url(re.compile(r"/archimate/composer\?element=\d+$"), timeout=PAGE_TIMEOUT)
    expect(page.locator(APP)).to_be_visible(timeout=PAGE_TIMEOUT)
    _wait_drawn(page, [name])


def test_old_twin_map_address_with_no_element_opens_the_composer(page, live_server, seeded):
    sign_in(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto("%s/intelligence/twin-map" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert page.url.rstrip("/").endswith("/archimate/composer"), page.url
    expect(page.locator(APP)).to_be_visible(timeout=PAGE_TIMEOUT)
    # The seeded company has a model, so the "nothing is modelled" state is not shown.
    expect(page.get_by_test_id("composer-empty-workspace")).to_have_count(0)


def test_an_organisation_with_no_model_sees_why_it_is_empty_and_how_to_fill_it(page, live_server):
    fresh = create_fresh_org("enterprise_architect", org_admin=True)
    sign_in(page, live_server, fresh["emails"]["enterprise_architect"])
    page.goto("%s/intelligence/twin-map" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert page.url.rstrip("/").endswith("/archimate/composer"), page.url
    empty = page.get_by_test_id("composer-empty-workspace")
    expect(empty).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(empty).to_contain_text("Nothing is modelled yet")
    connect = empty.get_by_role("link", name="Connect GitHub")
    expect(connect).to_be_visible()
    connect.click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_role("heading", name="Connect GitHub").first).to_be_visible(timeout=PAGE_TIMEOUT)


def test_another_organisations_element_answers_404_through_both_addresses(page, live_server, graph):
    other = create_fresh_org("enterprise_architect")
    sign_in(page, live_server, other["emails"]["enterprise_architect"])
    direct = page.goto("%s/archimate/composer?element=%d" % (live_server, graph["service"]),
                       wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert direct.status == 404
    old = page.goto("%s/intelligence/twin-map?element=%d" % (live_server, graph["service"]),
                    wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert old.status == 404

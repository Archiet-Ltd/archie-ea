"""A question about another organisation's service is answered from the asker's own records only.

An architect of a second, empty organisation asks the chat what breaks if the first organisation's service
fails: the chat answers with an empty state that repeats the asked name and nothing else, with no
connection row, no record and no fact of the first organisation. The first organisation's architect asks
the same question and reads the cited connections.
"""
import pytest
from playwright.sync_api import expect

from .chat_ask import ask, sign_in, sign_in_as
from .conftest import PAGE_TIMEOUT  # noqa: F401
from .fresh_org import create_fresh_org
from .intelligence_graph import seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def graph(seeded, live_server):
    return seed_impact_graph(seeded["ids"]["org"])


@pytest.fixture(scope="module")
def outsider(live_server):
    return create_fresh_org("solution_architect")["emails"]["solution_architect"]


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def test_another_organisations_question_gets_an_empty_state_and_none_of_its_facts(page, live_server, seeded, graph, outsider):
    service = graph["names"]["service"]
    question = "What breaks if %s fails" % service

    sign_in(page, live_server, outsider)
    answer = ask(page, live_server, question)
    expect(answer).to_contain_text("Nothing recorded matches")
    text = answer.inner_text()
    assert answer.locator("[data-answer-row]").count() == 0
    assert answer.locator("[data-catalogue-record]").count() == 0
    assert answer.locator("[data-citation]").count() == 0
    for name in graph["names"].values():
        assert name == service or name not in text, name
    assert "Imported from" not in text and "Worked out from the model" not in text

    sign_in_as(page, live_server, seeded, "solution_architect")
    own = ask(page, live_server, question)
    assert own.locator("[data-answer-row]").count() >= 1
    assert own.locator("[data-citation]").count() >= 1

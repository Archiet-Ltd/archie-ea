"""The answer card never spells out a missing value.

A connection with no owner, no health signal, or no recorded source must read as a plain
empty sentence ("No owner recorded", "Health not recorded", "Source not recorded"), never
as "undefined", "null", "None", or a leaked "[object Object]". The seeded chain asked about
here holds exactly those gaps: the Portal and the Ops Team have no owner, and one connection
is worked out from the model rather than recorded by a person.

The journey drives the chat the way a signed-in person does — open the AI chat, type the
question, read the answer cards — matching test_intelligence_us1_journey.py.
"""
import re

import pytest

from .chat_ask import ask, sign_in_as
from .conftest import PAGE_TIMEOUT, PASSWORD  # noqa: F401
from .intelligence_graph import seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# The ways a missing value leaks onto the card instead of a plain empty sentence.
_LEAKED_PLACEHOLDERS = ("undefined", "null", "None", "[object", "NaN")


@pytest.fixture(scope="module")
def graph(seeded, live_server):
    """A chain of connected elements in the seeded organisation, with one owner, two
    connections with no owner, and one worked-out connection: the gaps that would show a
    placeholder if a missing value were ever rendered raw."""
    return seed_impact_graph(seeded["ids"]["org"])


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def test_the_answer_cards_never_say_undefined_or_null(page, live_server, seeded, graph):
    sign_in_as(page, live_server, seeded, "solution_architect")
    answer = ask(page, live_server, "What breaks if %s fails" % graph["names"]["service"])

    # The question really produced connection cards, so the check below reads real content
    # rather than an empty answer.
    assert answer.locator("[data-answer-row]").count() >= 1

    text = answer.inner_text()
    for placeholder in _LEAKED_PLACEHOLDERS:
        assert placeholder not in text, "%r on the answer card: %s" % (placeholder, text)

    # The gaps are present and read as the plain empty sentences, not a leaked value.
    assert "No owner recorded" in text
    assert "Worked out from the model" in text

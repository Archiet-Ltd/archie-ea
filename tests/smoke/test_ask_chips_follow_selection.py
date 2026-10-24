"""Every question asked in the AI chat about one element answers its own question for that
element, one card after another in the same conversation, and the first answer stays put.

The solution architect asks what breaks, then each of the other seven questions, about the
same application, and reads each answer under its own heading.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, sign_in_as
from .test_ask_risk_lens import _seed_risk_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# lens -> (the question asked of the chat, the heading its card carries, a sentence its body must show)
QUESTIONS = {
    "strategy": ("What are we trying to achieve at %s and how is it tracking",
                 "What we are trying to achieve at %s, and how it is tracking",
                 "No initiatives recorded against this element"),
    "risk": ("What could hurt %s and what does it touch",
             "What could hurt %s, and what it touches", "single vendor dependency"),
    "portfolio": ("What is the rationalization status of %s",
                  "Rationalization status of %s",
                  "This is not something the rationalization view tracks"),
    "programme": ("What's changing at %s and is it on track",
                  "What is changing at %s, and whether it is on track",
                  "No work packages recorded against this element"),
    "data": ("What data does %s hold or produce, who stewards it, and where does it flow",
             "What data %s holds or produces, who stewards it and where it flows",
             "No data objects or data flows are recorded for this element yet"),
    "compliance": ("Which regulations and controls apply to %s and which controls have no evidence of being met",
                   "Which regulations and controls apply to %s, and which have no evidence",
                   "No compliance controls are recorded for this element yet"),
    "accountability": ("Who's accountable for %s", "Who is accountable for %s",
                       "Ownership data is not yet connected"),
}


@pytest.fixture(scope="module")
def risk_graph(seeded, live_server):
    return _seed_risk_graph(seeded["ids"]["org"])


def test_each_question_answers_for_the_chosen_element(page, live_server, seeded, risk_graph):
    sign_in_as(page, live_server, seeded, "solution_architect")
    name = risk_graph["service_name"]
    impact = ask(page, live_server, "What breaks if %s fails" % name)
    expect(impact.get_by_text("What breaks if %s fails" % name)).to_be_visible()

    for lens, (question, heading, sentence) in QUESTIONS.items():
        card = ask(page, live_server, question % name, fresh=False)
        expect(card.locator("[data-lens-cards]")).to_have_attribute("data-lens", lens)
        expect(card.get_by_text(heading % name, exact=True)).to_be_visible()
        expect(card).to_contain_text(sentence)
        assert card.locator("[data-answer-row]").count() == 0, lens
    # The first answer is still on the page, unchanged.
    expect(impact.locator("[data-answer-cards]")).to_be_visible()

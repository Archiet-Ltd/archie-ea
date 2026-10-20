"""The question chips on the Ask page: choosing a different chip after an element is
chosen answers that chip's own question for the same element.

Before the change, every chip except the one the element was chosen under left the first
answer on screen. These tests choose the element once, then click each chip in turn.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, type_and_wait
from .fresh_org import api, create_fresh_org, sign_in
from .test_ask_risk_lens import _ready, _seed_risk_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# chip key -> (results container, heading with the blank, a sentence its body must show)
CHIPS = {
    "strategy": ("ask-strategy-results", "What are we trying to achieve at %s and how is it tracking?",
                 "No initiatives recorded against this element."),
    "risk": ("ask-risk-results", "What could hurt %s and what does it touch?", "single vendor dependency"),
    "portfolio": ("ask-portfolio-results", "What is the rationalization status of %s?",
                  "This is not something the rationalization view tracks."),
    "programme": ("ask-programme-results", "What's changing at %s and is it on track?",
                  "No work packages recorded against this element."),
    "data": ("ask-data-results", "What data does %s hold or produce, who stewards it, and where does it flow?",
             "No data objects or data flows are recorded for this element yet."),
    "compliance": ("ask-compliance-results",
                   "Which regulations and controls apply to %s and which controls have no evidence of being met?",
                   "No compliance controls are recorded for this element yet."),
    "accountability": ("ask-accountability-results", "Who's accountable for %s and can they take on more?",
                       "Ownership data is not yet connected."),
}


@pytest.fixture(scope="module")
def risk_graph(seeded, live_server):
    return _seed_risk_graph(seeded["ids"]["org"])


def _login(page, base, email):
    sign_in(page, base, email)


def test_each_chip_answers_its_own_question_for_the_chosen_element(page, live_server, seeded, risk_graph):
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    page.locator("#ask-question-impact").click()
    type_and_wait(page, "ask", risk_graph["noun"])
    page.locator("#ask-picker-listbox [role=option]", has_text=risk_graph["service_name"]).click()
    page.wait_for_selector("[data-ask-row]")
    name = risk_graph["service_name"]
    expect(page.locator("#ask-results-heading")).to_have_text("What breaks if %s fails or is under-resourced?" % name)

    for key, (container, heading, sentence) in CHIPS.items():
        page.locator("#ask-question-%s" % key).click()
        box = page.locator("#%s" % container)
        expect(box).to_be_visible()
        expect(page.locator("#%s-heading" % container)).to_have_text(heading % name)
        expect(box).to_contain_text(sentence)
        expect(page.locator("#ask-results")).to_be_hidden()

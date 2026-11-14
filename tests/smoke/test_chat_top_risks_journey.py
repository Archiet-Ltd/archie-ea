"""The chat's answer to "what are the top five risks to reaching our revenue targets" is read from the
risk register: it names the five highest-scoring risks, never calls the register empty while it holds
risks, and offers to record a risk nowhere but the register."""

import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, sign_in_as
from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

QUESTION = "what are the top five risks to reaching our revenue targets"
# (likelihood, impact) -> score: 25, 20, 16, 15, 12, then 9, 8, 6
SCORES = [(5, 5), (4, 5), (4, 4), (3, 5), (3, 4), (3, 3), (2, 4), (2, 3)]


@pytest.fixture
def eight_risks(seeded, live_server):
    from app import create_app, db
    from app.models.risk import Risk

    org_id = seeded["ids"]["org"]
    tag = uuid.uuid4().hex[:6]
    titles = []
    with create_app("testing").app_context():
        for index, (likelihood, impact) in enumerate(SCORES):
            title = "Revenue risk %d %s" % (index + 1, tag)
            db.session.add(Risk(organization_id=org_id, title=title, likelihood=likelihood, impact=impact,
                                owner="Owner %d" % (index + 1)))
            titles.append(title)
        db.session.commit()
        db.session.remove()
    yield titles
    with create_app("testing").app_context():
        for risk in Risk.query.filter(Risk.organization_id == org_id, Risk.title.in_(titles)).all():
            db.session.delete(risk)
        db.session.commit()
        db.session.remove()


def test_the_chat_names_the_five_highest_scoring_risks(page, live_server, seeded, eight_risks):
    sign_in_as(page, live_server, seeded, "solution_architect")
    answer = ask(page, live_server, QUESTION)
    expect(answer).to_contain_text(eight_risks[0], timeout=PAGE_TIMEOUT)
    text = answer.inner_text()
    for title in eight_risks[:5]:
        assert title in text, "%s is missing from the answer" % title
    for title in eight_risks[5:]:
        assert title not in text, "%s scores below the top five" % title
    lowered = text.lower()
    assert "empty" not in lowered and "no risk" not in lowered and "zero" not in lowered
    # a named risk opens its page with its content
    answer.get_by_role("link", name=eight_risks[0]).click()
    page.wait_for_url("**/risks/*", timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).to_contain_text(eight_risks[0])
    expect(page.locator("main")).to_contain_text("Owner 1")


def test_one_recorded_risk_is_named_and_the_register_is_not_called_empty(page, live_server, seeded, eight_risks):
    from app import create_app, db
    from app.models.risk import Risk

    keep = eight_risks[0]
    with create_app("testing").app_context():
        for risk in Risk.query.filter(Risk.organization_id == seeded["ids"]["org"],
                                      Risk.title.in_(eight_risks[1:])).all():
            db.session.delete(risk)
        db.session.commit()
        db.session.remove()
    sign_in_as(page, live_server, seeded, "solution_architect")
    answer = ask(page, live_server, QUESTION)
    expect(answer).to_contain_text(keep, timeout=PAGE_TIMEOUT)
    assert "empty" not in answer.inner_text().lower()


def test_no_chat_tool_offers_to_record_a_risk_outside_the_register():
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS as TOOLS

    [tool] = [t for t in TOOLS if t["name"] == "create_risk"]
    assert "solution risk register" not in tool["description"].lower()
    assert "risk register" in tool["description"].lower()

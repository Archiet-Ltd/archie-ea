"""Journey, in a browser: the chat changes an application only when the request names one.

An architect asks the chat to update a solution, a risk and a work package and is
never told "I could not find an application": those requests go on to the model. They ask
which capabilities an application supports and which they do not name ("we", "our
applications") and get no card about a word that is no application. They ask for a
business owner whose name fits two people, are asked which one with each person's
email and offered no Save; they type the email of the one they mean, press Save and
the application page shows that person after a reload. An application with no capability
mapped says so and its link opens the page where one is mapped. The other organisation's
application of the same name is never read or changed.
"""

import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from . import fresh_org
from .chat_ask import open_chat, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ARCHITECT = "enterprise_architect"

# Opt-in walk screenshots: off unless a directory is named.
WALK = os.environ.get("CHAT_WALK_DIR")

NOT_APPLICATIONS = (
    "set the description of Payments Solution to Handles card payments",
    "update the status of risk R-12 to mitigated",
    "update the status of work package WP1 to done",
)


def _shot(page, name):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _seed(org_id, name):
    """An application with two mapped capabilities, a bare application, one whose name contains
    "we", and two people called Sam Same."""
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        mapped = ApplicationComponent(name=name, organization_id=org_id, description="Old description",
                                      deployment_status="production")
        bare = ApplicationComponent(name="Bare %s" % uuid.uuid4().hex[:5], organization_id=org_id,
                                    deployment_status="production")
        tweed = ApplicationComponent(name="Tweed %s" % uuid.uuid4().hex[:5], organization_id=org_id,
                                     deployment_status="production")
        db.session.add_all([mapped, bare, tweed])
        db.session.flush()
        capabilities = [BusinessCapability(name="%s %s" % (label, uuid.uuid4().hex[:4]), organization_id=org_id)
                        for label in ("Take payments", "Onboard customers")]
        db.session.add_all(capabilities)
        db.session.flush()
        for capability in capabilities:
            db.session.add(ApplicationCapabilityMapping(
                application_component_id=mapped.id, business_capability_id=capability.id,
                organization_id=org_id, support_level="primary", coverage_percentage=60))
        twins = []
        for label in ("one", "two"):
            person = User(email="sam.%s.%s@example.com" % (label, uuid.uuid4().hex[:6]), first_name="Sam",
                          last_name="Same", organization_id=org_id, confirmed=True, enterprise_role=ARCHITECT)
            person.role = Role.query.filter_by(name="User").one()
            db.session.add(person)
            twins.append(person)
        db.session.commit()
        out = {"app_id": mapped.id, "bare_id": bare.id, "bare": bare.name, "tweed": tweed.name,
               "capabilities": [c.name for c in capabilities], "twins": [p.email for p in twins]}
        db.session.remove()
    return out


def _owner_rows(app_id):
    from app import create_app, db
    from app.models.application_owner import ApplicationOwner

    app = create_app("testing")
    with app.app_context():
        rows = [(r.user_id, r.person_name) for r in ApplicationOwner.query.filter_by(application_id=app_id).all()]
        db.session.remove()
    return rows


def _send(page, text):
    """Type *text* the way a person does; return the estate-answer response the browser received."""
    box = page.locator("#user-input")
    box.fill(text)
    with page.expect_response(lambda r: r.url.endswith("/ai-chat/estate-answer"), timeout=PAGE_TIMEOUT) as seen:
        box.press("Enter")
    return seen.value.json()


def _phone_dark(page, live_server):
    """The same chat at phone width in the dark theme."""
    page.set_viewport_size({"width": 390, "height": 844})
    open_chat(page, live_server)
    page.evaluate("document.documentElement.classList.add('dark')")


def _newest_card(page, before):
    cards = page.get_by_test_id("estate-answer")
    expect(cards).to_have_count(before + 1, timeout=PAGE_TIMEOUT)
    return cards.nth(before)


@pytest.fixture
def orgs():
    name = "Zephyr %s" % uuid.uuid4().hex[:5]
    out = []
    for _ in range(2):
        org = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
        org.update(_seed(org["org_id"], name))
        org["name"] = name
        out.append(org)
    return out


def test_requests_about_a_solution_a_risk_or_a_work_package_are_not_application_changes(page, live_server, orgs):
    mine, _theirs = orgs
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, live_server, mine["emails"][ARCHITECT])
    open_chat(page, live_server)
    for sentence in NOT_APPLICATIONS:
        answer = _send(page, sentence)
        assert answer["found"] is False and "change_state" not in answer, (sentence, answer)
    expect(page.locator("[data-application-change]")).to_have_count(0)
    assert "could not find an application" not in page.locator("main").inner_text()
    _shot(page, "chat-not-an-application-1440")

    # An application of this organisation is still a change to it.
    before = page.get_by_test_id("estate-answer").count()
    shown = _send(page, "set %s's description to Runs the monthly billing" % mine["name"])
    assert shown["change_state"] == "preview"
    card = _newest_card(page, before)
    expect(card).to_contain_text("Change the description of %s" % mine["name"])
    expect(card.get_by_test_id("application-change-save")).to_be_visible()
    _shot(page, "chat-save-card-1440")

    _phone_dark(page, live_server)
    before = page.get_by_test_id("estate-answer").count()
    _send(page, "set %s's description to Runs the monthly billing" % mine["name"])
    narrow = _newest_card(page, before)
    narrow.get_by_test_id("application-change-save").scroll_into_view_if_needed()
    expect(narrow.get_by_test_id("application-change-save")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    _shot(page, "chat-save-card-dark-390")


def test_the_capability_answer_the_empty_answer_and_words_that_are_no_application(page, live_server, orgs):
    mine, theirs = orgs
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, live_server, mine["emails"][ARCHITECT])
    open_chat(page, live_server)

    before = page.get_by_test_id("estate-answer").count()
    found = _send(page, "Which capabilities does %s support?" % mine["name"])
    assert found["entry_id"] == "capabilities_of_application"
    answer = _newest_card(page, before)
    for capability in mine["capabilities"]:
        expect(answer).to_contain_text(capability)
    links = answer.get_by_test_id("catalogue-open")
    expect(links).to_have_count(2)
    _shot(page, "chat-capabilities-1440")
    href, label = links.nth(0).get_attribute("href"), links.nth(0).inner_text().strip()
    page.goto(live_server + href, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).to_contain_text(label)

    # An application with no capability mapped says so and links to where one is mapped.
    open_chat(page, live_server)
    before = page.get_by_test_id("estate-answer").count()
    _send(page, "Which capabilities does %s support?" % mine["bare"])
    empty = _newest_card(page, before)
    expect(empty).to_contain_text("has no capability mapped to it yet")
    _shot(page, "chat-capabilities-none-recorded-1440")
    empty.get_by_role("link", name="Open %s" % mine["bare"]).click()
    page.wait_for_load_state("networkidle")
    assert re.search(r"/applications/%d\?tab=capabilities" % mine["bare_id"], page.url), page.url
    expect(page.locator("main")).to_contain_text("No capabilities mapped")

    _phone_dark(page, live_server)
    for label, text, name in (
        ("capabilities", "Which capabilities does %s support?" % mine["name"], mine["capabilities"][0]),
        ("capabilities-none-recorded", "Which capabilities does %s support?" % mine["bare"], "has no capability mapped"),
    ):
        before = page.get_by_test_id("estate-answer").count()
        _send(page, text)
        card = _newest_card(page, before)
        expect(card).to_contain_text(name)
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        _shot(page, "chat-%s-dark-390" % label)

    # Words that are no application go to the model; the application whose name contains "we" is not
    # offered as the answer.
    open_chat(page, live_server)
    for question in ("Which capabilities do we support?", "Which capabilities do our applications support?"):
        reply = _send(page, question)
        assert reply.get("entry_id") != "capabilities_of_application", (question, reply)
        assert mine["tweed"] not in (reply.get("html") or "")
        assert "nothing recorded matches" not in (reply.get("html") or "").lower()

    # The other organisation's capabilities never appear in this organisation's answer.
    open_chat(page, live_server)
    before = page.get_by_test_id("estate-answer").count()
    _send(page, "Which capabilities does %s support?" % mine["name"])
    text = _newest_card(page, before).inner_text()
    for capability in theirs["capabilities"]:
        assert capability not in text


def test_a_name_that_fits_two_people_is_asked_about_and_the_email_of_one_is_saved(page, live_server, orgs):
    mine, _theirs = orgs
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, live_server, mine["emails"][ARCHITECT])
    open_chat(page, live_server)

    before = page.get_by_test_id("estate-answer").count()
    asked = _send(page, "set %s's business owner to Sam Same" % mine["name"])
    assert asked["change_state"] == "invalid"
    card = _newest_card(page, before)
    expect(card).to_contain_text("more than one person")
    for email in mine["twins"]:
        expect(card).to_contain_text(email)
    assert card.get_by_test_id("application-change-save").count() == 0
    assert _owner_rows(mine["app_id"]) == [], "an ambiguous name was recorded"
    _shot(page, "chat-ambiguous-name-1440")

    # The email of the one meant: shown, saved, and read back after a reload.
    before = page.get_by_test_id("estate-answer").count()
    picked = _send(page, "set %s's business owner to %s" % (mine["name"], mine["twins"][1]))
    assert picked["change_state"] == "preview"
    preview = _newest_card(page, before)
    preview.get_by_test_id("application-change-save").click()
    expect(preview).to_contain_text("Saved: the business owner", timeout=PAGE_TIMEOUT)
    rows = _owner_rows(mine["app_id"])
    assert len(rows) == 1 and rows[0][0] is not None and rows[0][1] is None, rows
    page.goto("%s/applications/%d" % (live_server, mine["app_id"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.reload(wait_until="networkidle")
    expect(page.locator("main")).to_contain_text("Sam Same")
    expect(page.locator("main")).not_to_contain_text("No account in this workspace")

    # Phone width, dark theme: the question and its emails still read.
    page.set_viewport_size({"width": 390, "height": 844})
    open_chat(page, live_server)
    page.evaluate("document.documentElement.classList.add('dark')")
    before = page.get_by_test_id("estate-answer").count()
    _send(page, "set %s's business owner to Sam Same" % mine["bare"])
    narrow = _newest_card(page, before)
    expect(narrow).to_contain_text(mine["twins"][0])
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    _shot(page, "chat-ambiguous-name-dark-390")

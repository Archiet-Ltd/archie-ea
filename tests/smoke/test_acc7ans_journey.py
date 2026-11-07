"""A cited answer says truthfully where each connection came from.

An enterprise architect asks the AI chat what breaks if the order hub fails. Some of the hub's
connections were drawn by people and some were read from GitHub. The answer's summary line must
count them from the rows beneath it, the link to a relationship record must open that
relationship, and the saved answer must show its date the way the product writes dates.
"""

import json
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, role_of
from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = "/home/ubuntu/verify/seats/acc7ansp/walk"
FILLERS = 25


def _seed(label):
    """An organisation whose hub has three drawn links and two read from GitHub, plus enough
    other relationships that the record sought is not on the first page of the list."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    noun = "SourceTruth %s" % suffix
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="%s Org %s" % (label, suffix), slug="%s-%s" % (label.lower(), suffix))
        db.session.add(org)
        db.session.commit()
        user = User(
            email="%s.%s@example.com" % (label.lower(), suffix), first_name="Grace",
            last_name="Hopper %s" % label, organization_id=org.id,
            enterprise_role="enterprise_architect", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()

        def element(name):
            row = ArchiMateElement(name="%s %s" % (noun, name), type="ApplicationComponent",
                                   layer="application", organization_id=org.id)
            db.session.add(row)
            db.session.commit()
            return row

        hub = element("Order hub")
        drawn = [element("Billing %d" % i) for i in range(3)]
        read = [element("Shipping %d" % i) for i in range(2)]
        for target in drawn:
            db.session.add(ArchiMateRelationship(
                type="Serving", source_id=hub.id, target_id=target.id, organization_id=org.id,
                created_by_id=user.id, source_kind="manual", source_label="Grace Hopper %s" % label))
        github_ids = []
        for i, target in enumerate(read):
            rel = ArchiMateRelationship(
                type="Serving", source_id=hub.id, target_id=target.id, organization_id=org.id,
                source_kind="connector", source_label="GitHub acme/shop",
                source_ref=json.dumps({"system": "github", "repository": "acme/shop",
                                       "commit": "abc1234def%d" % i, "file": "deploy/%d.yml" % i}))
            db.session.add(rel)
            db.session.flush()
            github_ids.append(rel.id)
        # Relationships of a type that lists before "Serving", so the record sought is past page one.
        other = element("Other")
        for _ in range(FILLERS):
            db.session.add(ArchiMateRelationship(type="Access", source_id=other.id, target_id=other.id,
                                                 organization_id=org.id))
        db.session.commit()
        return {"email": user.email, "hub": hub.name, "github_rel": github_ids[0],
                "github_name": read[0].name, "noun": noun, "org_id": org.id}


@pytest.fixture(scope="module")
def world(live_server):
    return {"ours": _seed("Ours"), "other": _seed("Other")}


@pytest.fixture(params=[("desktop", 1440, 900, "light"), ("phone", 390, 844, "dark")], ids=["1440-light", "390-dark"])
def page(browser, request):
    _, width, height, scheme = request.param
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme,
                              timezone_id="UTC", locale="en-GB")
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    pg.shot_tag = "%d-%s" % (width, scheme)
    yield pg
    ctx.close()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _shot(page, name):
    os.makedirs(WALK, exist_ok=True)
    page.screenshot(path="%s/%s-%s.png" % (WALK, name, page.shot_tag), full_page=True)


def _answer(page, base, ours):
    assert role_of(ours["email"]) == "enterprise_architect"
    _login(page, base, ours["email"])
    return ask(page, base, "What breaks if %s fails" % ours["hub"])


def test_summary_counts_connections_by_where_they_came_from(page, live_server, world):
    ours = world["ours"]
    card = _answer(page, live_server, ours)
    summary = card.locator("[data-source-summary]")
    expect(summary).to_have_text(
        re.compile(r"^5 connections in your records: 3 recorded by people, 2 read from GitHub\.$"))
    assert "all recorded by people" not in card.inner_text()
    _shot(page, "chat-summary")


def test_open_relationship_record_opens_that_relationship(page, live_server, world):
    ours, other = world["ours"], world["other"]
    card = _answer(page, live_server, ours)
    row = card.locator("[data-slot='card']").filter(has=page.get_by_role("link", name=ours["github_name"], exact=True))
    row.get_by_role("link", name="Open relationship record").click()
    page.wait_for_url(re.compile(r"/architecture/relationships\?relationship=%d#relationship-%d$"
                                 % (ours["github_rel"], ours["github_rel"])))
    record = page.locator("#relationship-%d" % ours["github_rel"])
    expect(record).to_have_count(1)
    expect(record).to_contain_text(ours["github_name"])
    expect(record).to_contain_text(ours["hub"])
    expect(page.locator("main tbody tr")).to_have_count(1)
    assert other["noun"] not in page.locator("main").inner_text()
    _shot(page, "relationship-record")
    page.get_by_role("link", name="Show all relationships").click()
    expect(page.locator("main tbody tr")).not_to_have_count(1)


def test_saved_answer_tells_the_same_story_and_writes_its_date_plainly(page, live_server, world):
    ours = world["ours"]
    card = _answer(page, live_server, ours)
    card.get_by_test_id("answer-open").click()
    page.wait_for_url(re.compile(r"/intelligence/answers/"))
    confidence = page.locator("[data-answer-basis]")
    expect(confidence).to_contain_text("3 recorded by people, 2 read from GitHub")
    assert "Every connection was recorded by a person" not in page.locator("main").inner_text()
    expect(page.locator("[data-computed-at]")).to_have_text(re.compile(r"^\d{1,2} [A-Z][a-z]{2} \d{4}, \d{2}:\d{2}$"))
    _shot(page, "saved-answer")
    page.goto(live_server + "/intelligence/answers", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("[data-answers-list] time").first).to_have_text(re.compile(r"^\d{1,2} [A-Z][a-z]{2} \d{4}, \d{2}:\d{2}$"))

"""The same fact reads the same on every screen: owner, direct relationship count and cost.

The person is a CTO whose model has hundreds of relationships. For every application in it the test
reads the owner, the directly connected count and the annual cost from each screen that shows them (the
saved answer, the chat card, the application page, the Applications list, the dashboard and the impact
page) and asserts they are identical. Indirect connections are labelled wherever they are shown.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import ask
from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.seed_estate import seed_calibration_ledger
from tests.smoke.test_citation_and_owner_agreement_journey import (  # noqa: F401
    test_1_connect_and_approve,
)
from tests.smoke.test_connect_github_to_cited_answer_journey import (  # noqa: F401
    TOKEN,
    admin,
    github_server,
    world,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _direct(page):
    """The directly connected count a page states, read from its marked figure."""
    page.wait_for_function(
        "() => document.querySelector('[data-direct-count]') || /Nothing is recorded as connected/.test(document.body.innerText)",
        timeout=PAGE_TIMEOUT,
    )
    marks = page.locator("[data-direct-count]")
    if not marks.count():
        return 0  # the page says nothing is recorded as connected
    values = {marks.nth(i).inner_text().strip() for i in range(marks.count())}
    assert len(values) == 1, "the page states more than one direct count: %s" % sorted(values)
    return int(values.pop())


@pytest.fixture(scope="module")
def estate(admin, world, app):
    """Every application of the organisation with its element, plus the large application."""
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    seed_calibration_ledger(app, world["one"]["org_id"])
    with app.app_context():
        rows = [
            (c.id, c.name, c.archimate_element_id)
            for c in ApplicationComponent.query.filter_by(organization_id=world["one"]["org_id"]).order_by(ApplicationComponent.name)
            if c.archimate_element_id
        ]
        db.session.remove()
    assert len(rows) >= 5, rows
    return rows


def _owner_words(text):
    return re.sub(r"\s+", " ", text.split(" (")[0]).strip()


def test_2_direct_count_owner_and_cost_agree_for_every_application(admin, world, estate):
    base = world["base"]
    disagreements = []
    card_owners = {}
    # The list: owner words per application name.
    admin.goto(base + "/applications/?per_page=50", wait_until="domcontentloaded")
    table = admin.get_by_role("region", name="Applications table")
    list_rows = {
        name: table.get_by_role("row", name=re.compile(r"^Select %s " % re.escape(name))).first.inner_text()
        for _, name, _ in estate
    }
    # The dashboard names the most connected element and its count.
    admin.goto(base + "/dashboard/overview", wait_until="domcontentloaded")
    dash = re.sub(r"\s+", " ", admin.locator("#main-content").inner_text())
    top = re.search(r"([\w .-]+?) is the most connected, with (\d+) relationships", dash)

    for app_id, name, element_id in estate:
        seen = {}
        # Impact page
        admin.goto(base + "/enterprise/analysis/impact-analysis?element=%d" % element_id, wait_until="domcontentloaded")
        seen["impact page"] = _direct(admin)
        # Saved answer
        admin.goto(base + "/intelligence/answers/element/%d" % element_id, wait_until="domcontentloaded")
        seen["saved answer"] = _direct(admin)
        # Chat card
        answer = ask(admin, base, "What breaks if %s fails" % name)
        chat = answer.locator("[data-testid='answer-direct-count']")
        seen["chat card"] = int(chat.first.locator("[data-direct-count]").inner_text()) if chat.count() else (
            0 if "Nothing is recorded as connected" in answer.inner_text() else None
        )
        # Application page
        admin.goto(base + "/applications/%d" % app_id, wait_until="domcontentloaded")
        seen["application page"] = _direct(admin)
        for card in answer.locator("[data-slot='card']").all():
            owner = card.locator("dt:has-text('Owner') + dd")
            if owner.count():
                card_name = card.inner_text().strip().split("\n")[0].strip()
                card_owners.setdefault(card_name, set()).add(_owner_words(owner.first.inner_text()))
        if top and top.group(1).strip() == name:
            seen["dashboard"] = int(top.group(2))
        if len(set(seen.values())) != 1:
            disagreements.append("%s: %s" % (name, seen))

        # Owner: the application page, the list row.
        page_owner = admin.locator("[data-testid='application-owner'], [data-testid='application-owner-empty']").first.inner_text()
        row_text = list_rows[name]
        if "No owner recorded" in page_owner:
            if "No owner" not in row_text:
                disagreements.append("%s: the application page says no owner, the list says %r" % (name, row_text[:120]))
        else:
            first_line = _owner_words(page_owner.strip().split("\n")[0])
            if first_line and first_line.split(" ")[0] not in row_text:
                disagreements.append("%s: the application page names %r, the list says %r" % (name, first_line, row_text[:160]))

    for card_name, owners in card_owners.items():
        row_text = list_rows.get(card_name)
        if row_text is None:
            continue
        for owner in owners:
            if owner.startswith("No owner"):
                if "No owner" not in row_text:
                    disagreements.append("%s: the chat card says %r, the list says %r" % (card_name, owner, row_text[:120]))
            elif owner.split(" ")[0] not in row_text:
                disagreements.append("%s: the chat card names %r, the list says %r" % (card_name, owner, row_text[:160]))
    assert not disagreements, "\n".join(disagreements)


def test_3_cost_reads_as_recorded_zero_or_not_recorded(admin, world, estate):
    base = world["base"]
    ledger = next(r for r in estate if r[1] == "Calibration Ledger")
    admin.goto(base + "/applications/%d?tab=cost" % ledger[0], wait_until="domcontentloaded")
    text = re.sub(r"\s+", " ", admin.locator("#main-content").inner_text())
    assert re.search(r"Annual Licence Cost £0\b", text, re.I), "a recorded zero must read as zero: %s" % text[text.find("ANNUAL LICENCE") - 50 : text.find("ANNUAL LICENCE") + 600]
    assert re.search(r"Annual Development Not recorded", text, re.I), "a cost nobody recorded must say so: %s" % text[:500]
    assert re.search(r"Total Cost of Ownership £25,200", text, re.I), text[:500]
    assert re.search(r"Monthly Infrastructure £2,100", text, re.I), text[:500]
    assert re.search(r"Annual Infrastructure £25,200", text, re.I), text[:500]

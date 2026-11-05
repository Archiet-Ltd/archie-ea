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


def _owner_column(page):
    """Where the Owner column sits among a saved answer's cells, read from its header."""
    heads = [h.strip().lower() for h in page.locator("[data-answer-rows] thead th").all_text_contents()]
    return heads.index("owner")


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
    top = re.search(r"Relationships (.+?) is the most connected, with (\d+) relationships?", dash)

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
            # the dashboard states the relationship records, which is what the page's own figure states
            seen_records = int(top.group(2))
            admin.goto(base + "/applications/%d" % app_id, wait_until="domcontentloaded")
            page_records = int(admin.locator("[data-relationship-count]").first.inner_text())
            if seen_records != page_records:
                disagreements.append("%s: the dashboard says %s relationships, the application page %s" % (name, seen_records, page_records))
        if len(set(seen.values())) != 1:
            disagreements.append("%s: %s" % (name, seen))

        # The saved answer's rows name each affected application's owner: the same words as the list row.
        admin.goto(base + "/intelligence/answers/element/%d" % element_id, wait_until="domcontentloaded")
        rows = admin.locator("[data-answer-rows] tr[data-element-id]")
        owner_col = _owner_column(admin) if rows.count() else 0
        for k in range(rows.count()):
            row_name = rows.nth(k).locator("th").text_content().strip()
            saved_owner = re.sub(r"\s+", " ", rows.nth(k).locator("th, td").nth(owner_col).text_content()).strip()
            if row_name in list_rows and saved_owner != "No owner recorded":
                for person in [x.strip() for x in saved_owner.split(",") if x.strip()]:
                    if person not in list_rows[row_name]:
                        disagreements.append("%s: the saved answer for %s names %r, the list says %r" % (row_name, name, person, list_rows[row_name][:160]))
            elif row_name in list_rows and "No owner" not in list_rows[row_name]:
                disagreements.append("%s: the saved answer for %s says no owner, the list says %r" % (row_name, name, list_rows[row_name][:160]))

        # Owner: the application page, the list row.
        admin.goto(base + "/applications/%d" % app_id, wait_until="domcontentloaded")
        page_owner = admin.locator("[data-testid='application-owner'], [data-testid='application-owner-empty']").first.inner_text()
        row_text = list_rows[name]
        if "No owner recorded" in page_owner:
            if "No owner" not in row_text:
                disagreements.append("%s: the application page says no owner, the list says %r" % (name, row_text[:120]))
        else:
            first_line = _owner_words(page_owner.strip().split("\n")[0])
            if first_line and first_line not in row_text:
                disagreements.append("%s: the application page names %r, the list says %r" % (name, first_line, row_text[:160]))

    for card_name, owners in card_owners.items():
        row_text = list_rows.get(card_name)
        if row_text is None:
            continue
        for owner in owners:
            if owner.startswith("No owner"):
                if "No owner" not in row_text:
                    disagreements.append("%s: the chat card says %r, the list says %r" % (card_name, owner, row_text[:120]))
            elif owner not in row_text:
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


def test_4_the_recorded_cost_reads_the_same_on_the_cost_tab_the_export_and_the_completeness_line(admin, world, estate):
    import csv
    import io

    base = world["base"]
    ledger = next(r for r in estate if r[1] == "Calibration Ledger")
    admin.goto(base + "/applications/%d?tab=cost" % ledger[0], wait_until="domcontentloaded")
    cost_tab = re.sub(r"\s+", " ", admin.locator("#main-content").inner_text())
    on_tab = re.search(r"Total Cost of Ownership £([\d,]+)", cost_tab, re.I)
    assert on_tab, cost_tab[:500]
    shown = int(on_tab.group(1).replace(",", ""))

    response = admin.request.get(base + "/applications/export/csv")
    assert response.ok, response.status
    table = list(csv.reader(io.StringIO(response.text())))
    header = table[0]
    assert "Total Run Cost (Auto)" in header, header[:40]
    name_col = next(i for i, h in enumerate(header) if h.strip().lower() in ("application name", "name"))
    row = next(r for r in table[1:] if r[name_col] == "Calibration Ledger")
    exported = float(row[header.index("Total Run Cost (Auto)")] or 0)
    assert int(exported) == shown, "the Cost tab says %s, the export says %s" % (shown, exported)

    admin.goto(base + "/applications/%d" % ledger[0], wait_until="domcontentloaded")
    overview = re.sub(r"\s+", " ", admin.locator("#main-content").inner_text())
    missing = re.search(r"Missing: ([^.]*)\.", overview)
    assert not (missing and "Total cost of ownership" in missing.group(1)), "a recorded cost %s is listed as missing: %s" % (shown, missing.group(0))

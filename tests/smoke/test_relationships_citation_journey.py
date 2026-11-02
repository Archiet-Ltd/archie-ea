"""A person who follows a citation to a relationship lands on the page that holds it, with that row marked.

Goal: an architect reads an answer that cites a relationship, follows "Open relationship record", and sees which of
the many relationships in the list is the answer's record, without paging to find it. The list stays one page long.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .test_connect_github_to_cited_answer_journey import remove_organisations

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

COUNT = 45
SIZES = {"1440": {"width": 1440, "height": 900}, "390": {"width": 390, "height": 844}}


def _linked_estate(app, org_id, count, prefix):
    from app import db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    with app.app_context():
        elements = [ArchiMateElement(name="%s %02d" % (prefix, n), type="ApplicationComponent", layer="Application",
                                     organization_id=org_id) for n in range(count + 1)]
        db.session.add_all(elements)
        db.session.commit()
        rels = [ArchiMateRelationship(type="Serving", source_id=elements[n].id, target_id=elements[n + 1].id,
                                      organization_id=org_id) for n in range(count)]
        db.session.add_all(rels)
        db.session.commit()
        ids = sorted(rel.id for rel in rels)
        db.session.remove()
    return ids


@pytest.fixture(scope="module")
def estates(app, live_server):
    ours = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    ours["noun"], theirs["noun"] = "Ledger", "Foreign"
    ours["ids"] = _linked_estate(app, ours["org_id"], COUNT, "Ledger")
    theirs["ids"] = _linked_estate(app, theirs["org_id"], 3, "Foreign")
    yield {"ours": ours, "theirs": theirs}
    remove_organisations(app, [ours["org_id"], theirs["org_id"]])


@pytest.mark.parametrize("size", sorted(SIZES))
def test_a_citation_lands_on_its_own_page_with_its_row_marked_and_in_view(page, live_server, estates, size):
    ours = estates["ours"]
    cited = ours["ids"][-1]
    page.set_viewport_size(SIZES[size])
    sign_in(page, live_server, ours["emails"]["enterprise_architect"])

    page.goto(live_server + "/architecture/relationships?focus=%d#relationship-%d" % (cited, cited),
              wait_until="load", timeout=PAGE_TIMEOUT)
    rows = page.get_by_test_id("relationship-row")
    assert 0 < rows.count() <= 20, "the list never holds more than a page of rows"
    row = page.locator("#relationship-%d" % cited)
    expect(row).to_have_count(1)
    expect(row).to_have_attribute("data-state", "selected")
    expect(row).to_contain_text("Cited")
    expect(row).to_contain_text("Ledger %02d" % (COUNT - 1))
    assert page.locator("[data-state='selected'][data-testid='relationship-row']").count() == 1
    expect(page.get_by_text("Relationships 41 to 45 of 45")).to_be_visible()
    box = row.bounding_box()
    assert box and 0 <= box["y"] and box["y"] + box["height"] <= SIZES[size]["height"], "the anchor leaves the row in view: %s" % box
    assert "Foreign" not in page.locator("main").inner_text()
    wide = page.locator("[data-slot='table-container']").evaluate("e => e.scrollWidth - e.clientWidth")
    assert wide <= 1, "all three columns fit the screen without sideways scrolling (%d px over)" % wide

    # the page it opened is part of a list a person can walk: Previous lands on the page before it, with content
    page.get_by_role("link", name="Previous").click()
    page.wait_for_url(re.compile(r"[?&]page=2\b"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("Relationships 21 to 40 of 45")).to_be_visible()
    assert page.get_by_test_id("relationship-row").count() == 20
    expect(page.locator("[data-state='selected'][data-testid='relationship-row']")).to_have_count(0)


def test_the_list_pages_with_next_and_an_empty_organisation_says_why(page, live_server, estates):
    ours = estates["ours"]
    sign_in(page, live_server, ours["emails"]["enterprise_architect"])
    page.goto(live_server + "/architecture/relationships", wait_until="load", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("Relationships 1 to 20 of 45")).to_be_visible()
    page.get_by_role("link", name="Next").click()
    page.wait_for_url(re.compile(r"[?&]page=2\b"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("Relationships 21 to 40 of 45")).to_be_visible()

    empty = create_fresh_org("enterprise_architect")
    try:
        page.context.clear_cookies()
        sign_in(page, live_server, empty["emails"]["enterprise_architect"])
        page.goto(live_server + "/architecture/relationships", wait_until="load", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("No relationships yet.")).to_be_visible()
        expect(page.get_by_text("Draw one between two elements in the Composer", exact=False)).to_be_visible()
    finally:
        from app import create_app

        remove_organisations(create_app("testing"), [empty["org_id"]])

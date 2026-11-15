"""An architect reaches every relationship of their organisation, page by page, and none of another's.

The goal: "go through all 45 of our relationships". Organisation A holds 45 relationships and organisation B holds
10. The journey signs in as A's architect, opens the relationships page, reads "Page 1 of 3", presses next twice and
reaches the 45th row; the last page shows the remaining five rows, and first and previous lead back. No name of B
appears on any page.

Run at 1440 wide in the light scheme and at 390 wide in the dark scheme.
"""

import os
import tempfile
import uuid
from pathlib import Path

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = Path(os.environ.get("RELATIONSHIPS_WALK_DIR") or tempfile.mkdtemp(prefix="relationships-walk-"))
VIEWS = [("light", 1440, 900), ("dark", 390, 844)]


@pytest.fixture(scope="module")
def estate(live_server, app):
    from app import db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        def element(org_id, name):
            row = ArchiMateElement(organization_id=org_id, name=name, type="BusinessService", layer="Business")
            db.session.add(row)
            db.session.flush()
            return row

        hub = element(mine["org_id"], "Hub mine %s" % tag)
        for i in range(1, 46):
            leaf = element(mine["org_id"], "Mine %s %02d" % (tag, i))
            db.session.add(ArchiMateRelationship(organization_id=mine["org_id"], source_id=hub.id,
                                                 target_id=leaf.id, type="Serving"))
        other_hub = element(theirs["org_id"], "Hub theirs %s" % tag)
        for i in range(1, 11):
            leaf = element(theirs["org_id"], "Theirs %s %02d" % (tag, i))
            db.session.add(ArchiMateRelationship(organization_id=theirs["org_id"], source_id=other_hub.id,
                                                 target_id=leaf.id, type="Serving"))
        db.session.commit()
    return {"email": mine["emails"]["enterprise_architect"], "tag": tag}


@pytest.fixture(params=VIEWS, ids=lambda v: "%s-%d" % (v[0], v[1]))
def page(request, browser, live_server, estate):
    scheme, width, height = request.param
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = context.new_page()
    sign_in(page, live_server, estate["email"])
    page._view = "%s-%d" % (scheme, width)
    yield page
    context.close()


def _shot(page, name):
    WALK.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(WALK / ("relationships-%s-%s.png" % (name, page._view))), full_page=True)


def _row_texts(page):
    return [row.inner_text() for row in page.get_by_test_id("relationship-row").all()]


def test_an_architect_reaches_every_relationship_and_none_of_another_organisations(page, live_server, estate):
    tag = estate["tag"]
    response = page.goto(live_server + "/architecture/relationships", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 200
    seen = []
    assert page.get_by_text("Page 1 of 3").is_visible()
    assert page.get_by_role("button", name="Go to first page").count() == 0
    first = _row_texts(page)
    assert len(first) == 20
    seen += first
    _shot(page, "page-1")

    page.get_by_role("link", name="Next page").click()
    page.wait_for_load_state("domcontentloaded")
    assert page.get_by_text("Page 2 of 3").is_visible()
    second = _row_texts(page)
    assert len(second) == 20
    seen += second

    page.get_by_role("link", name="Next page").click()
    page.wait_for_load_state("domcontentloaded")
    assert page.get_by_text("Page 3 of 3").is_visible()
    third = _row_texts(page)
    assert len(third) == 5
    seen += third
    assert page.get_by_text("Rows 41 to 45 of 45").is_visible()
    assert page.get_by_role("link", name="Next page").count() == 0
    _shot(page, "page-3")

    assert any("Mine %s 45" % tag in text for text in third), "the 45th row is not on the last page"
    assert len(seen) == 45 and len(set(seen)) == 45
    for text in seen:
        assert "Theirs %s" % tag not in text and "Hub theirs" not in text

    page.get_by_role("link", name="Previous page").click()
    page.wait_for_load_state("domcontentloaded")
    assert page.get_by_text("Page 2 of 3").is_visible()
    page.get_by_role("link", name="Last page").click()
    page.wait_for_load_state("domcontentloaded")
    assert page.get_by_text("Page 3 of 3").is_visible()
    page.get_by_role("link", name="First page").click()
    page.wait_for_load_state("domcontentloaded")
    assert page.get_by_text("Page 1 of 3").is_visible()
    assert "Theirs %s" % tag not in page.content()


def test_rows_per_page_changes_the_number_of_pages(page, live_server, estate):
    page.goto(live_server + "/architecture/relationships", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_label("Rows per page").select_option("50")
    page.get_by_role("button", name="Apply").click()
    page.wait_for_load_state("domcontentloaded")
    assert page.get_by_text("Page 1 of 1").is_visible()
    assert len(_row_texts(page)) == 45
    _shot(page, "fifty-per-page")

"""An architect sees only their own organisation's neighbours when a relationship row crosses organisations.

The goal: "what is this element connected to". Organisation A's element has one neighbour in A and one
relationship row whose far end belongs to organisation B. The journey signs in as A's architect and opens the
element page, the relationships list, the enterprise relationships list and the product page. On each, B's name
is absent and A's neighbour is present; the neighbour's link opens a page with content. B's element id answers
not found.

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

WALK = Path(os.environ.get("CROSS_ORG_WALK_DIR") or tempfile.mkdtemp(prefix="cross-org-walk-"))
VIEWS = [("light", 1440, 900), ("dark", 390, 844)]


@pytest.fixture(scope="module")
def estate(live_server, app):
    from sqlalchemy import text

    from app import db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.archimate_missing_elements import Product

    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    tag = uuid.uuid4().hex[:6]
    names = {"centre": "Orders mine %s" % tag, "neighbour": "Billing mine %s" % tag,
             "foreign": "Orders theirs %s" % tag, "product": "Portal mine %s" % tag}
    with app.app_context():
        def element(org_id, name, type_, layer):
            row = ArchiMateElement(organization_id=org_id, name=name, type=type_, layer=layer)
            db.session.add(row)
            db.session.flush()
            return row

        centre = element(mine["org_id"], names["centre"], "BusinessService", "Business")
        neighbour = element(mine["org_id"], names["neighbour"], "BusinessService", "Business")
        foreign = element(theirs["org_id"], names["foreign"], "BusinessService", "Business")
        product = element(mine["org_id"], names["product"], "Product", "business")
        db.session.add(Product(organization_id=mine["org_id"], name=product.name, archimate_element_id=product.id))
        db.session.add(ArchiMateRelationship(organization_id=mine["org_id"], source_id=centre.id,
                                             target_id=neighbour.id, type="Serving"))
        db.session.add(ArchiMateRelationship(organization_id=mine["org_id"], source_id=product.id,
                                             target_id=centre.id, type="Aggregation"))
        for source, target in ((centre.id, foreign.id), (product.id, foreign.id)):
            db.session.execute(text(
                "INSERT INTO archimate_relationships (organization_id, source_id, target_id, type) "
                "VALUES (:o, :s, :t, 'Serving')"), {"o": mine["org_id"], "s": source, "t": target})
        db.session.commit()
        ids = {"centre": centre.id, "neighbour": neighbour.id, "foreign": foreign.id, "product": product.id}
    return {"email": mine["emails"]["enterprise_architect"], "names": names, "ids": ids}


@pytest.fixture(params=VIEWS, ids=lambda v: "%s-%d" % (v[0], v[1]))
def page(request, browser, live_server, estate):
    scheme, width, height = request.param
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = context.new_page()
    sign_in(page, live_server, estate["email"])
    page._view = "%s-%d" % (scheme, width)
    yield page
    context.close()


def _open(page, base, path):
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 200, "%s answered %s" % (path, response.status)
    return page.content()


def _element_path(estate):
    return "/architecture/business/BusinessService/%d?ae=1" % estate["ids"]["centre"]


def test_element_page_lists_own_neighbour_and_not_the_other_organisation(page, live_server, estate):
    html = _open(page, live_server, _element_path(estate))
    WALK.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(WALK / ("element-%s.png" % page._view)), full_page=True)
    assert estate["names"]["neighbour"] in html
    assert estate["names"]["foreign"] not in html
    link = page.get_by_role("link", name=estate["names"]["neighbour"]).first
    link.click()
    page.wait_for_load_state("domcontentloaded")
    assert estate["names"]["neighbour"] in page.content()
    assert estate["names"]["foreign"] not in page.content()


@pytest.mark.parametrize("path", ["/architecture/relationships", "/enterprise/architecture/relationships"])
def test_relationship_lists_show_own_row_and_not_the_other_organisation(page, live_server, estate, path):
    html = _open(page, live_server, path)
    page.screenshot(path=str(WALK / ("%s-%s.png" % (path.strip("/").replace("/", "-"), page._view))), full_page=True)
    assert estate["names"]["foreign"] not in html
    assert estate["names"]["neighbour"] in html or estate["names"]["centre"] in html


def test_product_page_lists_own_members_and_not_the_other_organisation(page, live_server, estate):
    html = _open(page, live_server, "/architecture/business/Product/%d?ae=1" % estate["ids"]["product"])
    page.screenshot(path=str(WALK / ("product-%s.png" % page._view)), full_page=True)
    assert estate["names"]["centre"] in html
    assert estate["names"]["foreign"] not in html


def test_other_organisations_element_id_is_not_found(page, live_server, estate):
    response = page.request.get(live_server + "/architecture/api/elements/%d/relationships" % estate["ids"]["foreign"])
    assert response.status == 404


def test_element_drawer_and_endpoint_answer_with_own_neighbour_only(page, live_server, estate):
    centre = estate["ids"]["centre"]
    ends = page.request.get(live_server + "/architecture/api/elements/%d/relationships" % centre)
    assert ends.status == 200
    assert [r["target_id"] for r in ends.json()["outgoing"]] == [estate["ids"]["neighbour"]]
    assert str(estate["ids"]["foreign"]) not in ends.text()
    drawer = page.request.get(live_server + "/architecture/api/elements/%d/detail" % centre)
    assert drawer.status == 200
    assert estate["names"]["neighbour"] in drawer.text()
    assert estate["names"]["foreign"] not in drawer.text()

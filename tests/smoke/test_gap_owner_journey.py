"""Journey, in a browser: a gap can be given an owner through the same picker
every other element uses.

A business architect opens a gap that has no owner. The page says "No owner
recorded" and offers the owner picker. They search for a colleague, pick them,
save, and on reload the gap names that person. The picker offers only people in
the signed-in organisation: a person from another organisation is never listed,
and the other organisation's own gap still reads "No owner recorded".
"""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from . import fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ASSIGNER = "business_architect"
OWNER = "application_manager"


def _seed_gap(org_id):
    from app import create_app, db
    from app.models.implementation_migration import Gap

    app = create_app("testing")
    with app.app_context():
        gap = Gap(name="Owner journey gap %s" % uuid.uuid4().hex[:6], organization_id=org_id)
        db.session.add(gap)
        db.session.commit()
        ids = {"gap_id": gap.id, "gap_name": gap.name}
        db.session.remove()
    return ids


def _person_name(org):
    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        name = db.session.get(User, org["user_ids"][OWNER]).full_name()
        db.session.remove()
    return name


def _gap_url(base, ids):
    return "%s/architecture/implementation/Gap/%d" % (base, ids["gap_id"])


@pytest.fixture
def two_orgs():
    org_a = fresh_org.create_fresh_org(ASSIGNER, org_admin=True, extra_roles=(OWNER,))
    org_b = fresh_org.create_fresh_org(ASSIGNER, org_admin=True, extra_roles=(OWNER,))
    org_a["gap"] = _seed_gap(org_a["org_id"])
    org_b["gap"] = _seed_gap(org_b["org_id"])
    return org_a, org_b


def test_a_gap_is_given_an_owner_through_the_picker(page, live_server, two_orgs):
    org_a, org_b = two_orgs
    owner_name = _person_name(org_a)
    other_name = _person_name(org_b)
    assert owner_name and owner_name != "—"

    fresh_org.sign_in(page, live_server, org_a["emails"][ASSIGNER])
    page.goto(_gap_url(live_server, org_a["gap"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    cell = page.locator("[data-testid='element-owner']")
    assert "No owner recorded" in cell.inner_text()
    box = cell.locator("[data-owner-picker] [data-owner-search]")
    assert box.count() == 1, "the gap offers no way to record an owner"

    box.fill(owner_name.split()[-1])
    results = cell.locator("[data-owner-results] button")
    results.first.wait_for(timeout=PAGE_TIMEOUT)
    listed = " ".join(results.all_inner_texts())
    assert owner_name in listed
    assert other_name not in listed or other_name == owner_name, "the picker listed a person from another organisation"
    cell.locator("[data-owner-results] button", has_text=owner_name).first.click()
    with page.expect_response(lambda r: r.request.method == "POST" and "/owners" in r.url) as saved:
        cell.locator("[data-owner-save]").click()
    assert saved.value.status == 201

    page.goto(_gap_url(live_server, org_a["gap"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    cell = page.locator("[data-testid='element-owner']")
    assert owner_name in cell.inner_text()
    assert "No owner recorded" not in cell.inner_text()

    # The other organisation's gap is untouched.
    page.goto(live_server + "/account/logout", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    fresh_org.sign_in(page, live_server, org_b["emails"][ASSIGNER])
    page.goto(_gap_url(live_server, org_b["gap"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    text = page.locator("[data-testid='element-owner']").inner_text()
    assert "No owner recorded" in text and owner_name not in text

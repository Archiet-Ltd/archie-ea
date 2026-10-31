"""The RACI matrix keeps its shape when it is empty, and the demonstration company's matrix is populated.

(a) An organisation with no stakeholders opens the matrix and sees its header row, the legend and one example row
with a single add control, adds its first stakeholder from that control, assigns a cell and finds it after a reload.
(b) The demonstration's business architect opens the populated matrix, sets a cell to A with the keyboard, sees the
row with two people accountable flagged in words, and at phone width scrolls sideways with the first column fixed.
"""
import os
import pathlib
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from app.commands.seed_demo_company import _RACI_ROWS
from .fresh_org import create_fresh_org, sign_in
from .test_seeded_boards_journey import demo_org  # noqa: F401  (fixture)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]
expect.set_options(timeout=PAGE_TIMEOUT)

RACI = "/organization/raci"
from .test_seeded_boards_journey import DEMO_EMAIL as ARCHITECT  # noqa: E402  (the demonstration architect)
SHOTS = pathlib.Path(os.environ.get("RACI_WALK_DIR", "/tmp/raci-walk"))


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / name), full_page=False)


def _seed_capability(org_id, name="Order Handling"):
    from app import create_app, db
    from app.models.unified_capability import UnifiedCapability

    with create_app("testing").app_context():
        cap = UnifiedCapability(name=name, code="RC%d" % (org_id % 100000), level=1, organization_id=org_id)
        db.session.add(cap)
        db.session.commit()


def _handle(cell):
    from app.models.user import User

    return User.query.get(cell.stakeholder_id).email.split("@")[0]


@pytest.fixture
def architect_may_write(demo_org):
    """The seeded architects hold the read-only Viewer platform role, which the cell-save route refuses
    (it admits admin, architect and business architect platform roles). Give the demonstration architect the
    Architect platform role for the journey and put the seeded role back afterwards."""
    from app import create_app, db
    from app.models.user import Role, User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=ARCHITECT).one()
        seeded_role_id = user.role_id
        user.role_id = Role.query.filter_by(name="Architect").one().id
        db.session.commit()
    yield
    from app.models.organization_model import EnterpriseRaciAssignment

    with create_app("testing").app_context():
        user = User.query.filter_by(email=ARCHITECT).one()
        user.role_id = seeded_role_id
        # Put the seeded matrix back: the route that clears a cell is for administrators only.
        seeded_cells = {(code, handle) for code, cells in _RACI_ROWS for handle in cells}
        for cell in EnterpriseRaciAssignment.query.filter_by(organization_id=user.organization_id).all():
            if cell.raci == "A" and cell.capability and (cell.capability.code, _handle(cell)) not in seeded_cells:
                db.session.delete(cell)
        db.session.commit()


def _open(browser, live_server, email, width=1440, height=900, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = context.new_page()
    sign_in(page, live_server, email)
    page.goto(live_server + RACI, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector("[data-testid=raci-matrix]", timeout=PAGE_TIMEOUT)
    return context, page


def test_empty_matrix_keeps_its_shape_and_takes_the_first_stakeholder(browser, live_server):
    org = create_fresh_org("business_architect")
    _seed_capability(org["org_id"])
    email = org["emails"]["business_architect"]
    context, page = _open(browser, live_server, email)
    try:
        matrix = page.locator("[data-testid=raci-matrix]")
        expect(matrix).to_be_visible()
        # The header row, the legend and one example row are on screen without scrolling.
        expect(matrix.locator("thead th").first).to_contain_text("Capability")
        legend = page.locator("[data-testid=raci-legend]")
        expect(legend).to_be_visible()
        for word in ("Responsible", "Accountable", "Consulted", "Informed"):
            expect(legend).to_contain_text(word)
        example = page.locator("[data-testid=raci-example-row]")
        expect(example).to_have_count(1)
        expect(example).to_contain_text("Approve the production release")
        expect(example.locator("[data-raci-cell]")).to_have_count(0)  # empty cells are empty
        assert legend.bounding_box()["y"] + legend.bounding_box()["height"] < 900
        assert example.bounding_box()["y"] + example.bounding_box()["height"] < 900
        # One add control inside the matrix, and the prompt says what to do.
        add = matrix.locator("[data-testid=raci-add-first] button")
        expect(add).to_have_count(1)
        expect(matrix.get_by_role("button")).to_have_count(1)
        expect(matrix).to_contain_text("Add the roles involved")
        _shot(page, "empty-1440-light.png")
        phone = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
        try:
            small = phone.new_page()
            sign_in(small, live_server, email)
            small.goto(live_server + RACI, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(small.locator("[data-testid=raci-example-row]")).to_be_visible()
            expect(small.locator("[data-testid=raci-legend]")).to_be_visible()
            prompt = small.locator("[data-testid=raci-add-first]")
            box, frame = prompt.bounding_box(), small.locator("[data-testid=raci-scroll]").bounding_box()
            assert box["x"] + box["width"] <= frame["x"] + frame["width"] + 1, "the prompt runs past the matrix"
            _shot(small, "empty-390-dark.png")
        finally:
            phone.close()

        add.click()
        page.fill("#raci-stakeholder-search", "Finance Lead")
        page.get_by_role("button", name=re.compile(r'Create "Finance Lead"')).click()
        header = matrix.locator("thead th", has_text="Finance Lead")
        expect(header).to_be_visible()
        expect(page.locator("[data-testid=raci-example-row]")).to_have_count(0)

        cell = matrix.locator("[data-raci-cell]").first
        cell.focus()
        page.keyboard.press("R")
        expect(cell).to_have_text(re.compile(r"^\s*R\s*$"))

        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid=raci-matrix]")
        expect(page.locator("[data-raci-cell]").first).to_have_text(re.compile(r"^\s*R\s*$"))
        expect(page.locator("[data-testid=raci-matrix] thead th", has_text="Finance Lead")).to_be_visible()
    finally:
        context.close()


def _row(page, name):
    return page.locator("[data-testid=raci-matrix] tbody tr", has_text=name)


def test_architect_sets_a_cell_by_keyboard_and_two_accountable_are_flagged(browser, live_server, demo_org, architect_may_write):
    context, page = _open(browser, live_server, ARCHITECT)
    try:
        matrix = page.locator("[data-testid=raci-matrix]")
        expect(matrix.locator("thead th")).not_to_have_count(0)
        assert matrix.locator("thead th").count() >= 6
        row = _row(page, "Sensor Calibration")
        expect(row).to_have_count(1)
        letters = [t.strip() for t in row.locator("[data-raci-cell]").all_inner_texts()]
        assert letters.count("A") == 1, letters
        expect(row.locator("[data-testid=raci-flag]")).to_have_count(0)
        # The inventory row has nobody accountable, and says so in words.
        expect(_row(page, "Inventory Control").locator("[data-testid=raci-flag]")).to_contain_text("No one is accountable")
        _shot(page, "populated-1440-light.png")

        # An empty cell is empty; set it with the keyboard.
        index = letters.index("")
        empty = row.locator("[data-raci-cell]").nth(index)
        expect(empty).to_have_attribute("aria-label", re.compile(r"not involved"))
        empty.focus()
        page.keyboard.press("A")
        expect(empty).to_have_text(re.compile(r"^\s*A\s*$"))
        expect(empty).to_have_attribute("aria-label", re.compile(r"Accountable"))
        expect(row.locator("[data-testid=raci-flag]")).to_contain_text("2 people are accountable")

        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid=raci-matrix]")
        expect(_row(page, "Sensor Calibration").locator("[data-testid=raci-flag]")).to_contain_text("2 people are accountable")
        expect(_row(page, "Sensor Calibration").locator("[data-raci-cell]").nth(index)).to_have_text(re.compile(r"^\s*A\s*$"))
    finally:
        context.close()


def test_phone_scrolls_sideways_and_keeps_the_first_column(browser, live_server, demo_org):
    context, page = _open(browser, live_server, ARCHITECT, width=390, height=844, scheme="dark")
    try:
        scroller = page.locator("[data-testid=raci-scroll]")
        dims = scroller.evaluate("el => [el.scrollWidth, el.clientWidth]")
        assert dims[0] > dims[1], dims
        first = page.locator("[data-testid=raci-matrix] tbody tr").first.locator("th, td").first
        pos = first.evaluate("el => getComputedStyle(el).position")
        assert pos == "sticky", pos
        _shot(page, "populated-390-dark-start.png")
        before = first.bounding_box()["x"]
        scroller.evaluate("el => { el.scrollLeft = 200; }")
        page.wait_for_function("document.querySelector('[data-testid=raci-scroll]').scrollLeft > 0", timeout=PAGE_TIMEOUT)
        after = first.bounding_box()["x"]
        assert abs(after - before) <= 1, (before, after)
        head = page.locator("[data-testid=raci-matrix] thead th").first
        assert head.evaluate("el => getComputedStyle(el).position") == "sticky"
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        _shot(page, "populated-390-dark.png")
    finally:
        context.close()


def test_a_stakeholder_kept_on_the_matrix_is_always_one_of_the_organisations_own_people(browser, live_server):
    """Organisation A's architect cannot put organisation B's person on A's matrix, by the cell or by the keep path;
    the refusal is a 404 in the organisation's terms, nothing is written, and A's own member is kept."""
    from .fresh_org import api, create_fresh_org

    a = create_fresh_org("business_architect", extra_roles=("data_architect",))
    b = create_fresh_org("business_architect")
    _seed_capability(a["org_id"])
    from app import create_app
    from app.models.unified_capability import UnifiedCapability

    with create_app("testing").app_context():
        cap = UnifiedCapability.query.filter_by(organization_id=a["org_id"]).one().id
    outsider = b["user_ids"]["business_architect"]
    own = a["user_ids"]["data_architect"]
    context, page = _open(browser, live_server, a["emails"]["business_architect"])
    try:
        refusals = []
        cell = {"stakeholder_type": "user", "stakeholder_id": outsider, "stakeholder_name": "Outsider",
                "capability_id": cap, "raci": "A"}
        keep = {"stakeholder_type": "user", "stakeholder_id": outsider, "stakeholder_name": "Outsider"}
        for path, body in (("/organization/raci/api/cell", cell), ("/organization/raci/api/stakeholder/keep", keep)):
            status, reply = api(page, "POST", path, body)
            assert status == 404, (path, status, reply)
            refusals.append(str(reply))
        assert all("not found" in r.lower() for r in refusals), refusals
        status, data = api(page, "GET", "/organization/raci/api/data")
        assert status == 200
        assert not any(x["stakeholder_id"] == outsider and x["stakeholder_type"] == "user"
                       for x in data["data"]["assignments"]), data
        # The page itself shows nobody from the other organisation.
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid=raci-matrix]")
        expect(page.locator("[data-testid=raci-matrix]")).not_to_contain_text("Outsider")
        # A's own member is kept, and appears on the matrix after a reload.
        status, reply = api(page, "POST", "/organization/raci/api/stakeholder/keep",
                            {"stakeholder_type": "user", "stakeholder_id": own, "stakeholder_name": "Launch data_archite"})
        assert status == 200, (status, reply)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid=raci-matrix]")
        expect(page.locator("[data-testid=raci-matrix] thead th", has_text="Launch data_archite")).to_be_visible()
        page.set_viewport_size({"width": 1440, "height": 900})
        _shot(page, "c-keep-1440-light.png")
    finally:
        context.close()
    # B's matrix is untouched too.
    context, bpage = _open(browser, live_server, b["emails"]["business_architect"])
    try:
        status, data = api(bpage, "GET", "/organization/raci/api/data")
        assert data["data"]["assignments"] == [], data
    finally:
        context.close()

"""Journey, in a browser, at 390 wide: a person reading Gap analysis on a phone can scroll every column of the
table into view and nothing frozen covers it.

Gap analysis is a list table whose first column is wide (the gap's title). Freezing the first and last
columns there left the Gap and Covered columns taking more than the box, so Domain, Phase, Status and Work
packages were never visible at any scroll position. The table now freezes its edges only while the two
together take at most 60 per cent of the box. At 1440 the same table keeps them frozen.
"""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from . import fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ROLE = "business_architect"
WIDE = "Month-end reconciliation by hand %s"


def _seed(org):
    from app import create_app, db
    from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard, create_adm_phases

    app = create_app("testing")
    with app.app_context():
        create_adm_phases()
        phase = ADMPhase.query.filter_by(code="A").first()
        board = KanbanBoard(name="Phone columns board", organization_id=org["org_id"],
                            created_by_id=org["user_ids"][ROLE])
        db.session.add(board)
        db.session.flush()
        for i in range(3):
            db.session.add(KanbanCard(
                title=WIDE % uuid.uuid4().hex[:6], description="A wide first column", board_id=board.id,
                adm_phase_id=phase.id, card_type="requirement", status="todo", arch_element_type="Gap",
                arch_domain="Application", created_by_id=org["user_ids"][ROLE],
                organization_id=org["org_id"]))
        db.session.commit()
        db.session.remove()


def _measure(page, testid="gaps-table"):
    """Where each header cell sits, and whether a frozen cell covers it."""
    return page.evaluate("""(testid) => {
      const box = document.querySelector('[data-testid=' + testid + ']').closest('.workbench-table-scroll');
      const ths = Array.from(box.querySelectorAll('thead th'));
      const b = box.getBoundingClientRect();
      const frozen = ths.filter(t => getComputedStyle(t).position === 'sticky');
      return {
        frozen: frozen.length, overflow: box.scrollWidth - box.clientWidth,
        cells: ths.map(t => {
          const r = t.getBoundingClientRect(), own = getComputedStyle(t).position === 'sticky';
          const covered = !own && frozen.some(f => {
            const fr = f.getBoundingClientRect();
            return Math.min(r.right, fr.right) - Math.max(r.left, fr.left) > 1;
          });
          return {text: t.textContent.trim(), left: r.left - b.left, right: r.right - b.left,
                  width: b.width, covered, own};
        })};
    }""", testid)


def _assert_fade_stops_at_the_frozen_column(box):
    """--fade-end is the frozen last column's width plus the 1px border, or 1px when nothing is frozen."""
    got = box.evaluate("""e => {
      const last = e.querySelector('thead tr').lastElementChild;
      return {frozen: e.hasAttribute('data-sticky-cols'), lastWidth: last.offsetWidth,
              fadeEnd: getComputedStyle(e).getPropertyValue('--fade-end').trim()};
    }""")
    want = (got["lastWidth"] if got["frozen"] else 0) + 1
    assert got["fadeEnd"] == "%dpx" % want, "fade end %s, expected %dpx (%s)" % (got["fadeEnd"], want, got)


def _scroll_whole(page, box, testid, label):
    """Scroll the box in steps; true when the named header cell is, at some position, whole and uncovered."""
    for fraction in (0, 0.2, 0.4, 0.5, 0.6, 0.8, 1):
        box.evaluate("(e, f) => { e.scrollLeft = (e.scrollWidth - e.clientWidth) * f; e.dispatchEvent(new Event('scroll')); }", fraction)
        page.wait_for_timeout(100)
        cell = next(c for c in _measure(page, testid)["cells"] if c["text"].startswith(label))
        if not cell["covered"] and cell["left"] >= -1 and cell["right"] <= cell["width"] + 1:
            return True
    return False


def _scroll_box(page, testid):
    return page.locator("[data-testid=%s]" % testid).locator(
        "xpath=ancestor::*[contains(@class,'workbench-table-scroll')][1]")


@pytest.fixture
def org():
    o = fresh_org.create_fresh_org(ROLE, org_admin=True)
    _seed(o)
    return o


def test_every_gap_column_scrolls_into_view_on_a_phone(browser, live_server, org):
    page = browser.new_page(viewport={"width": 390, "height": 900})
    try:
        fresh_org.sign_in(page, live_server, org["emails"][ROLE])
        page.goto(live_server + "/adm-kanban/gap-analysis", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert page.locator("[data-testid=gap-row]").count() == 3, "the seeded gaps are not listed"
        box = page.locator("[data-testid=gaps-table]").locator("xpath=ancestor::*[contains(@class,'workbench-table-scroll')][1]")
        assert box.evaluate("e => e.scrollWidth - e.clientWidth") > 8, "the table does not overflow on a phone"
        for want in ("Domain", "Phase", "Status", "Work packages that close it"):
            seen = False
            for fraction in (0, 0.25, 0.5, 0.75, 1):
                box.evaluate("(e, f) => { e.scrollLeft = (e.scrollWidth - e.clientWidth) * f; e.dispatchEvent(new Event('scroll')); }", fraction)
                page.wait_for_timeout(100)
                cell = next(c for c in _measure(page)["cells"] if c["text"].startswith(want))
                if not cell["covered"] and cell["left"] >= -1 and cell["right"] <= cell["width"] + 1:
                    seen = True
                    break
            assert seen, "%s is never visible and uncovered on a phone: %s" % (want, _measure(page))
        # The fade never reaches the box border or a frozen column.
        _assert_fade_stops_at_the_frozen_column(box)
    finally:
        page.close()


def test_the_edges_stay_frozen_on_a_desktop(browser, live_server, org):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        fresh_org.sign_in(page, live_server, org["emails"][ROLE])
        page.goto(live_server + "/adm-kanban/gap-analysis", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert page.locator("[data-testid=gap-row]").count() == 3
        assert _measure(page)["frozen"] == 2, "the first and last columns are not frozen at 1440"
    finally:
        page.close()


def _work_packages(org_id):
    from app import create_app, db
    from app.models.unified_work_package import UnifiedWorkPackage

    app = create_app("testing")
    with app.app_context():
        for i in range(3):
            db.session.add(UnifiedWorkPackage(
                name="Consolidate regional ledgers %d" % i,
                status="planned", organization_id=org_id))
        db.session.commit()
        db.session.remove()


def test_the_work_package_name_becomes_whole_on_a_phone(browser, live_server, org):
    """Work packages at 390 froze its edges and left a window narrower than the Name column, so Name was
    never whole. The table must not freeze then, or must leave room for the widest column."""
    _work_packages(org["org_id"])
    page = browser.new_page(viewport={"width": 390, "height": 900})
    try:
        fresh_org.sign_in(page, live_server, org["emails"][ROLE])
        page.goto(live_server + "/enterprise/implementation/work-packages", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert page.locator("[data-testid=work-package-row]").count() == 3
        box = _scroll_box(page, "work-packages-table")
        assert _scroll_whole(page, box, "work-packages-table", "Name"), \
            "Name is never whole and uncovered at 390: %s" % _measure(page, "work-packages-table")
        _assert_fade_stops_at_the_frozen_column(box)
    finally:
        page.close()


def test_the_team_email_becomes_whole_at_844(browser, live_server):
    o = fresh_org.create_fresh_org("business_architect", org_admin=True)
    from app import create_app, db
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        from app.models.organization import Organization
        from app.services.billing_plans import set_contract_plan

        set_contract_plan(db.session.get(Organization, o["org_id"]), "enterprise", None)
        db.session.commit()
        role = Role.query.filter_by(name="Architect").one()
        for i in range(3):
            u = User(email="long.member.address.%d.%s@long-company-name.example.com" % (i, o["suffix"]),
                     first_name="Member", last_name="Number%d" % i, organization_id=o["org_id"],
                     enterprise_role="business_architect", confirmed=True)
            u.role = role
            u.password = "x"
            db.session.add(u)
        db.session.commit()
        db.session.remove()
    page = browser.new_page(viewport={"width": 844, "height": 600})
    try:
        fresh_org.sign_in(page, live_server, o["emails"][ROLE])
        page.goto(live_server + "/admin/team", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        box = _scroll_box(page, "members-table")
        assert _scroll_whole(page, box, "members-table", "Email"), \
            "Email is never whole and uncovered at 844: %s" % _measure(page, "members-table")
        _assert_fade_stops_at_the_frozen_column(box)
    finally:
        page.close()

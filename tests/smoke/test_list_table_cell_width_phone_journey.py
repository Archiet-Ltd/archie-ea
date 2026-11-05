"""Journey, in a browser, at 390 and 768 wide: a person reading a list on a phone or a tablet can read every
cell whole at some scroll position.

Four list pages capped one column wider than the table's own scroll box (Solutions 320px, Gap analysis 380px,
Feature flags 420px, Traceability broken chains 360px in a 244px box), so however far the person scrolled,
the column was cut off. Each page is opened in the standard seeded organisation with a long value in the
named column, and every visible cell of every list table must be no wider than its scroll box.
"""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

LONG = "Quarterly intercompany settlement and month-end reconciliation workstream %s"

PAGES = [
    ("/solutions/", "Solution"),
    ("/adm-kanban/gap-analysis", "Gap"),
    ("/enterprise/implementation/gap-analysis", "Gap"),
    ("/admin/feature-flags", "Feature"),
    ("/architecture/traceability", "Element"),
]


@pytest.fixture(scope="module")
def long_values(seeded):
    from app import create_app, db
    from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard, create_adm_phases
    from app.models.archimate_core import ArchiMateElement
    from app.models.feature_flags import FeatureFlag
    from app.models.implementation_migration import Gap
    from app.models.solution_models import Solution

    org_id = seeded["ids"]["org"]
    user_id = seeded["ids"]["solution_architect_user"]
    suffix = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        db.session.add(Solution(name=LONG % suffix, description="A long name in the Solution column",
                                organization_id=org_id, created_by_id=user_id, governance_status="draft",
                                adm_phase="C", has_acm_domains=True))
        create_adm_phases()
        phase = ADMPhase.query.filter_by(code="A").first()
        board = KanbanBoard(name="Cell width board %s" % suffix, organization_id=org_id, created_by_id=user_id)
        db.session.add(board)
        db.session.flush()
        db.session.add(KanbanCard(title=LONG % suffix, description="A long name in the Gap column",
                                  board_id=board.id, adm_phase_id=phase.id, card_type="requirement",
                                  status="todo", arch_element_type="Gap", arch_domain="Application",
                                  created_by_id=user_id, organization_id=org_id))
        db.session.add(Gap(name=LONG % suffix, severity="high", organization_id=org_id))
        db.session.add(FeatureFlag(key="long_%s" % suffix, name=LONG % suffix, feature_type="functionality",
                                   enabled=True))
        db.session.add(ArchiMateElement(name=LONG % suffix, type="ApplicationComponent", layer="application",
                                        organization_id=org_id))
        db.session.commit()
        db.session.remove()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").dispatch_event("click")
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def _too_wide(page):
    """Every visible cell, in every list table, wider than the scroll box that holds it."""
    return page.evaluate("""() => {
      const out = {tables: 0, cells: 0, wide: []};
      for (const box of document.querySelectorAll('.workbench-table-scroll')) {
        if (!box.offsetWidth) continue;
        out.tables += 1;
        for (const c of box.querySelectorAll('th, td')) {
          if (!c.offsetWidth) continue;
          out.cells += 1;
          if (c.offsetWidth > box.clientWidth + 1)
            out.wide.push({text: c.textContent.trim().slice(0, 40), cell: c.offsetWidth, box: box.clientWidth});
        }
      }
      return out;
    }""")


def _spill_and_titles(page):
    """Cells whose content paints past the cell, and clipped values that do not say what they are.

    A cell with overflow visible and nowrap text lays a long value out as one line and draws it over the
    next column even when the cell is narrow, so width alone cannot tell a cell that holds its value from
    one that spills it. A value clipped with an ellipsis must carry the full text in a title.
    """
    return page.evaluate("""() => {
      const out = {cells: 0, spill: [], untitled: [], first: 0};
      for (const box of document.querySelectorAll('.workbench-table-scroll')) {
        if (!box.offsetWidth) continue;
        for (const c of box.querySelectorAll('td')) {
          if (!c.offsetWidth) continue;
          out.cells += 1;
          if (c.textContent.includes('Quarterly intercompany')) out.first = Math.max(out.first, c.offsetWidth);
          if (getComputedStyle(c).overflowX === 'visible' && c.scrollWidth > c.clientWidth + 1)
            out.spill.push({text: c.textContent.trim().slice(0, 40), content: c.scrollWidth, cell: c.clientWidth});
          for (const el of [c, ...c.querySelectorAll('*')]) {
            if (!el.offsetWidth || el.classList.contains('sr-only') || getComputedStyle(el).overflowX === 'visible') continue;
            if (el.scrollWidth <= el.clientWidth + 1) continue;
            const full = el.textContent.trim().replace(/\\s+/g, ' ');
            let holder = el;
            while (holder && holder !== c.parentNode && !holder.getAttribute('title')) holder = holder.parentElement;
            let title = holder && holder !== c.parentNode ? holder.getAttribute('title') : '';
            if (!title && el === c) title = (c.querySelector('[title]') || {getAttribute: () => ''}).getAttribute('title');
            if (!title || !full.startsWith(title.trim().slice(0, 20)))
              out.untitled.push({text: full.slice(0, 40), title: title || null});
          }
        }
      }
      return out;
    }""")


@pytest.mark.parametrize("width", [390, 768])
@pytest.mark.parametrize("path,column", PAGES)
def test_no_list_cell_is_wider_than_its_scroll_box(browser, live_server, seeded, long_values, path, column, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + path, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        got = _too_wide(page)
        assert got["tables"] >= 1 and got["cells"] > 0, "%s shows no list table, so nothing is tested: %s" % (path, got)
        assert "Quarterly intercompany" in page.content(), "%s does not list the long %s value" % (path, column)
        assert not got["wide"], "%s at %d: %s cannot be read whole at any scroll position: %s" % (
            path, width, column, got["wide"])
        more = _spill_and_titles(page)
        assert more["cells"] > 0, "%s has no cell to measure" % path
        assert not more["spill"], "%s at %d: %s paints past its cell onto the next column: %s" % (
            path, width, column, more["spill"])
        if width == 768:
            # the cap is a share of the screen, so a tablet keeps most of the value instead of a phone-sized 220px
            assert more["first"] >= 340, "%s at 768: the %s column is only %s wide, a phone-sized cap on a tablet" % (
                path, column, more["first"])
        assert not more["untitled"], "%s at %d: a clipped value does not carry its full text in a title: %s" % (
            path, width, more["untitled"])
    finally:
        page.close()

"""The Vendor Catalogue's sticky first/last table columns must have a fully
opaque background, or the columns scrolling underneath them show through.

Found 13 Sep 2026 in a full-app design pass on production: at a viewport
narrower than the table's natural width, the sticky "Actions" header (frozen
via `position: sticky; right: 0` in app/static/styles/shadcn-components.css)
rendered with a semi-transparent background (`hsl(var(--muted) / 0.5)`),
so the scrolled-under "Status"/"Contract" header text showed through it --
both texts visible in the same screen region, reading as garbled/broken
("ACTIONSTR"). The body-cell rule right next to it already used a fully
opaque `hsl(var(--card))`; only the header rule was transparent. Root-caused
via Chrome DevTools Protocol (CSS.getMatchedStylesForNode) against the live
page, not guessed from a screenshot. Fixed by making the header background
opaque too.

NOTE on what this test does NOT assert: the sticky header's bounding box
DOES geometrically overlap the columns it freezes past whenever the table
hasn't been scrolled -- that is the intended mechanism (a frozen column sits
on top of the ones underneath), not a bug. An earlier draft of this test
asserted non-overlapping bounding boxes and failed even against the fix,
because that is the wrong invariant. The actual correctness requirement is
that the sticky cell's background is fully opaque (alpha 1) and its z-index
places it above the cells it covers, so the covered content is truly hidden
rather than showing through -- that's what this test checks.
"""
import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _sticky_cell_opacity(page):
    return page.evaluate("""
        () => {
          const cells = Array.from(document.querySelectorAll('thead th:first-child, thead th:last-child'));
          return cells.map(el => {
            const cs = getComputedStyle(el);
            // rgba(r, g, b, a) or rgb(r, g, b) (implicit alpha 1)
            const m = cs.backgroundColor.match(/rgba?\\(([^)]+)\\)/);
            const parts = m ? m[1].split(',').map(s => parseFloat(s.trim())) : [];
            const alpha = parts.length === 4 ? parts[3] : 1;
            return {
              text: el.textContent.trim(),
              position: cs.position,
              zIndex: cs.zIndex,
              backgroundColor: cs.backgroundColor,
              alpha: alpha,
            };
          });
        }
    """)


def _give_a_vendor_every_column(seeded):
    """The Vendors table shows only the columns that have data, so a vendor with a system and an active
    contract is what makes it as wide as it gets for a customer who has filled the register in."""
    from datetime import date, timedelta
    import uuid

    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        suffix = uuid.uuid4().hex[:8]
        vendor = VendorOrganization(name="Wide table vendor %s" % suffix)
        db.session.add(vendor)
        db.session.flush()
        db.session.add(ApplicationComponent(name="Wide table system %s" % suffix, organization_id=org_id,
                                            vendor_organization_id=vendor.id, lifecycle_status="active"))
        db.session.add(VendorContract(
            contract_name="Wide table contract %s" % suffix, vendor_id=vendor.id, annual_cost=120000.0,
            status="active", start_date=date.today() - timedelta(days=30),
            end_date=date.today() + timedelta(days=200), organization_id=org_id))
        db.session.commit()


def _freeze_facts(page):
    return page.evaluate("""
        () => {
          const box = document.querySelector('.workbench-table-scroll');
          const ths = Array.from(box.querySelectorAll('thead th'));
          const w = t => t.offsetWidth;
          const first = ths[0], last = ths[ths.length - 1];
          const middle = ths.slice(1, -1).filter(t => !t.hidden).map(w);
          return {over: box.scrollWidth - box.clientWidth, box: box.clientWidth, frozen: box.hasAttribute('data-sticky-cols'),
                  edges: w(first) + w(last), widest: Math.max(0, ...middle),
                  sticky: ths.filter(t => getComputedStyle(t).position === 'sticky').length};
        }
    """)


def test_vendor_catalogue_sticky_header_cells_are_opaque(browser, live_server, seeded):
    """Measured on a vendor with every column filled, so the table really overflows at 700. At 700 the first
    column and the actions column take more than 60 per cent of the box, so the table does not freeze them
    (they would cover the middle columns); at 1440 it fits and they are frozen. Wherever cells are frozen they
    must be opaque."""
    _give_a_vendor_every_column(seeded)
    page = browser.new_page(viewport={"width": 700, "height": 900})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        for width in (700, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(live_server + "/vendors/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(500)
            facts = _freeze_facts(page)
            if width == 700:
                assert facts["over"] > 8, "the vendors table does not overflow at 700, so nothing is tested: %s" % facts
            wants = width == 1440
            assert facts["frozen"] is wants, "the decided outcome is frozen=%s at %d: %s" % (wants, width, facts)
            assert facts["sticky"] == (2 if wants else 0), "the decided outcome is %d sticky cells at %d: %s" % (2 if wants else 0, width, facts)

            cells = _sticky_cell_opacity(page)
            sticky_cells = [c for c in cells if c["position"] == "sticky"]
            translucent = [c for c in sticky_cells if c["alpha"] < 0.999]
            assert not translucent, (
                "%d sticky header cell(s) have a translucent background at %d, so "
                "content scrolling underneath them shows through instead of "
                "being hidden:\n  %s"
                % (len(translucent), width, "\n  ".join(
                    "%r: %s (alpha=%.2f)" % (c["text"], c["backgroundColor"], c["alpha"])
                    for c in translucent))
            )
    finally:
        page.close()


def test_vendor_catalogue_scroll_fade_signals_more_columns(browser, live_server, seeded):
    """A user on a narrow/docked window whose table columns overflow the
    visible area must see SOME indication that scrolling right reveals more
    -- reported 17 Sep 2026 as a real customer-facing defect: content ran off
    or got crushed with zero affordance. workbench_table.html's
    .workbench-table-scroll container applies a right-edge mask-image fade,
    removed via .at-scroll-end-x once scrolled to the end (or immediately if
    the table was never scrollable at all, so the fade never lies about
    content that isn't there)."""
    _give_a_vendor_every_column(seeded)
    page = browser.new_page(viewport={"width": 700, "height": 900})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/vendors/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)

        state = page.evaluate("""
            () => {
                const el = document.querySelector('.workbench-table-scroll');
                if (!el) return null;
                const scrollable = el.scrollWidth > el.clientWidth;
                const maskBefore = getComputedStyle(el).maskImage || getComputedStyle(el).webkitMaskImage;
                el.scrollLeft = el.scrollWidth;
                el.dispatchEvent(new Event('scroll'));
                const maskAfter = getComputedStyle(el).maskImage || getComputedStyle(el).webkitMaskImage;
                return { scrollable, maskBefore, maskAfter };
            }
        """)
        assert state is not None, (
            "no .workbench-table-scroll container found on /vendors/ -- the "
            "scroll-fade affordance markup is missing"
        )
        assert state["scrollable"], (
            "the vendor table did not overflow its container at a 700px "
            "viewport -- either columns shrank enough to fit (nothing to "
            "test here) or the table stopped rendering its usual columns; "
            "confirm which before trusting this test"
        )
        assert state["maskBefore"] not in (None, "none"), (
            "the table overflows but no mask-image fade is applied before "
            "scrolling -- a user has no indication that scrolling right "
            "reveals more columns"
        )
        assert state["maskAfter"] == "none", (
            "the mask-image fade is still applied after scrolling to the "
            "end of the table -- it now falsely implies there is more "
            "content to the right when there isn't"
        )
    finally:
        page.close()

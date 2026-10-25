"""Browser journey: the chief architect opens the week's briefing.

The enterprise architect types the old workbench address, lands on the weekly
briefing and finds the week's changes, the reviews past their limit, the risks
rising and each solution's conformance. They open one item from every section
and land on a record with content. Nothing on the page is a dash, a "not
available" line or an Ask control.
"""

import os
import re
import uuid
from datetime import datetime, timedelta

import pytest

from .conftest import PAGE_TIMEOUT, _require_explicit_test_database
from .test_archetype_journeys import PAGE_STATE, _login, _visit, page  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

OLD_ADDRESS = "/solutions/architect-synthesis"
BRIEFING = "/solutions/briefings"
PERSONA = "enterprise_architect"
SECTIONS = ("changes", "decisions", "risks", "conformance")

#: Set to a directory to capture the desktop/mobile evidence screenshots.
SCREENSHOT_DIR = os.environ.get("SMOKE_SCREENSHOT_DIR")


def _screenshot(page, name):
    """Capture the whole page: the shell scrolls an inner element, so the
    viewport is grown to the content height first."""
    if not SCREENSHOT_DIR:
        return
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    width = page.viewport_size["width"]
    content = page.evaluate(
        """() => {
            let tallest = document.documentElement.scrollHeight;
            document.querySelectorAll('*').forEach(el => {
                const s = getComputedStyle(el);
                if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > tallest) {
                    tallest = el.scrollHeight;
                }
            });
            return tallest;
        }"""
    )
    page.set_viewport_size({"width": width, "height": min(int(content) + 120, 8000)})
    page.wait_for_timeout(600)
    page.screenshot(path=os.path.join(SCREENSHOT_DIR, name), full_page=True)
    page.set_viewport_size({"width": width, "height": 900})


@pytest.fixture
def week_records(app, seeded):
    """Linked records for the seeded organisation: a changed element, two
    reviews past their limit, two rising risks and a solution with a design."""
    _require_explicit_test_database(dict(os.environ))
    from app import db
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.archimate_core import ArchiMateElement
    from app.models.entity_history import EntityHistory
    from app.models.risk import Risk
    from app.models.risk_score_history import RiskScoreHistory
    from app.models.solution_models import Solution, SolutionArchiMateElement
    from app.models.user import User

    org_id = seeded["ids"]["org"]
    tag = uuid.uuid4().hex[:6]
    made = {"element": None, "reviews": [], "risks": [], "solution": None}
    with app.app_context():
        db.session.remove()
        submitter = User.query.filter_by(
            email=seeded["emails"][PERSONA], organization_id=org_id
        ).one()
        assert submitter.enterprise_role == "enterprise_architect"
        now = datetime.utcnow()

        element = ArchiMateElement(
            name=f"Billing Service {tag}", type="ApplicationComponent",
            layer="application", organization_id=org_id,
        )
        db.session.add(element)
        db.session.flush()
        stamp = now - timedelta(hours=1)
        db.session.add(EntityHistory(
            organization_id=org_id, table_name="archimate_elements",
            record_id=element.id,
            snapshot={"name": element.name, "type": "ApplicationComponent", "layer": "application"},
            valid_from=stamp, recorded_at=stamp, source="trigger",
        ))
        made["element"] = (element.id, element.name)

        for number, priority, days in (("A", "critical", 20), ("B", "high", 30)):
            review = ARBReviewItem(
                review_number=f"JB-{tag}-{number}", title=f"Journey review {number} {tag}",
                review_type="architecture_change", priority=priority,
                status="under_review", organization_id=org_id,
                submitter_id=submitter.id, submitted_at=now - timedelta(days=days),
            )
            db.session.add(review)
            db.session.flush()
            made["reviews"].append((review.id, review.title))

        for label in ("Vendor lock-in", "Data loss"):
            risk = Risk(title=f"{label} {tag}", likelihood=2, impact=2, organization_id=org_id)
            db.session.add(risk)
            db.session.flush()
            for (likelihood, impact), when in (((2, 2), timedelta(days=30)), ((4, 5), timedelta(hours=2))):
                db.session.add(RiskScoreHistory(
                    risk_id=risk.id, score_kind="residual", likelihood=likelihood,
                    impact=impact, recorded_at=now - when, organization_id=org_id,
                ))
            made["risks"].append((risk.id, risk.title))

        solution = Solution(name=f"Payments redesign {tag}", organization_id=org_id)
        db.session.add(solution)
        db.session.flush()
        db.session.add(SolutionArchiMateElement(
            solution_id=solution.id, layer_type="business", element_id=1,
            element_table="business_actors", element_name="Actor",
        ))
        made["solution"] = (solution.id, solution.name)
        db.session.commit()
    yield made
    with app.app_context():
        db.session.remove()
        mirrored = [r[0] for r in db.session.execute(
            db.text("SELECT archimate_element_id FROM risks WHERE id = ANY(:ids) AND archimate_element_id IS NOT NULL"),
            {"ids": [r[0] for r in made["risks"]]})]
        db.session.execute(db.text("DELETE FROM risk_score_history WHERE risk_id = ANY(:ids)"),
                           {"ids": [r[0] for r in made["risks"]]})
        db.session.execute(db.text("DELETE FROM risks WHERE id = ANY(:ids)"),
                           {"ids": [r[0] for r in made["risks"]]})
        for element_id in mirrored:
            db.session.execute(db.text("DELETE FROM entity_history WHERE record_id = :id AND table_name = 'archimate_elements'"), {"id": element_id})
            db.session.execute(db.text("DELETE FROM archimate_elements WHERE id = :id"), {"id": element_id})
        db.session.execute(db.text("DELETE FROM arb_review_items WHERE id = ANY(:ids)"),
                           {"ids": [r[0] for r in made["reviews"]]})
        db.session.execute(db.text("DELETE FROM solution_archimate_elements WHERE solution_id = :id"),
                           {"id": made["solution"][0]})
        db.session.execute(db.text("DELETE FROM solutions WHERE id = :id"),
                           {"id": made["solution"][0]})
        db.session.execute(db.text("DELETE FROM entity_history WHERE record_id = :id AND table_name = 'archimate_elements'"),
                           {"id": made["element"][0]})
        db.session.execute(db.text("DELETE FROM archimate_elements WHERE id = :id"),
                           {"id": made["element"][0]})
        db.session.commit()


def _section_text(page, key):
    return " ".join(page.locator(f"[data-testid='briefing-{key}']").inner_text().split())


def test_old_address_lands_on_a_briefing_with_linked_items_in_every_section(
    page, live_server, seeded, week_records
):
    _login(page, live_server, seeded["emails"][PERSONA])
    response, state = _visit(page, live_server, OLD_ADDRESS)

    assert response is not None and response.status < 400
    assert page.url.split("?")[0].endswith(BRIEFING), "landed on %s" % page.url
    assert state["alpine"] == "object"
    assert not state["unnamed"], "unnamed controls: %s" % state["unnamed"]
    assert page.locator("h1").count() == 1
    assert page.locator("nav[aria-label='Breadcrumb']").count() == 1

    headline = page.locator("[data-testid='briefing-headline']").inner_text()
    assert re.search(r"[2-9]\d* reviews past the time limit", headline), headline
    assert re.search(r"[2-9]\d* risks rising", headline), headline

    for key in SECTIONS:
        links = page.locator(f"[data-testid='briefing-{key}'] li a")
        assert links.count() >= 1, "section %s has no linked items" % key
        for index in range(links.count()):
            assert links.nth(index).get_attribute("href") not in (None, "", "#", "None")
        assert links.count() <= 5
        dates = page.locator(f"[data-testid='briefing-{key}'] li time")
        assert dates.count() == links.count(), "every item in %s shows its date" % key

    content = " ".join(page.locator("main, [role=main], #main-content").first.inner_text().split())
    briefing_text = " ".join(
        [headline] + [_section_text(page, key) for key in SECTIONS]
    )
    assert not re.search(r"(^|\s)[—–-](\s|$)", briefing_text), "a bare dash is shown"
    assert "not available" not in content.lower()
    assert "how to read this page" not in content.lower()
    # The shell carries a search box and a page-guide drawer on every page; the
    # briefing's own content adds no Ask button, question box or suggestion.
    own = page.locator("[data-testid='briefing-page']")
    assert own.locator("button, input, textarea, select").count() == 0
    assert own.get_by_text(re.compile(r"\bask\b", re.I)).count() == 0
    _screenshot(page, "weekly-briefing-desktop-light.png")
    page.evaluate("document.documentElement.classList.add('dark')")
    _screenshot(page, "weekly-briefing-desktop-dark.png")
    page.evaluate("document.documentElement.classList.remove('dark')")


@pytest.mark.parametrize("section, expected", [
    ("changes", "element"), ("decisions", "review"),
    ("risks", "risk"), ("conformance", "solution"),
])
def test_each_section_item_opens_a_record_with_content(
    page, live_server, seeded, week_records, section, expected
):
    _login(page, live_server, seeded["emails"][PERSONA])
    _visit(page, live_server, BRIEFING)

    link = page.locator(f"[data-testid='briefing-{section}'] li a").first
    title = link.inner_text().strip()
    # The link names a record the seed made, never the page it is on.
    target = link.get_attribute("href")
    assert target.split("?")[0] != BRIEFING

    link.click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1200)
    assert page.url.split("?")[0].endswith(target.split("?")[0])
    body = " ".join(page.inner_text("body").split())
    assert "page not found" not in body.lower() and "forbidden" not in body.lower()
    # The landing page names the thing that was clicked.
    name_part = title.split("·")[-1].strip()
    assert name_part in body, "%s page does not show %r" % (expected, name_part)


@pytest.mark.parametrize("page", [390], indirect=True)
def test_the_briefing_fits_a_phone(page, live_server, seeded, week_records):
    _login(page, live_server, seeded["emails"][PERSONA])
    response, state = _visit(page, live_server, BRIEFING)
    assert response is not None and response.status < 400
    assert state["overflow"] == 0, "overflows by %spx at 390px" % state["overflow"]
    _screenshot(page, "weekly-briefing-mobile-light.png")
    page.evaluate("document.documentElement.classList.add('dark')")
    _screenshot(page, "weekly-briefing-mobile-dark.png")


def test_the_briefing_prints_on_one_or_two_a4_pages(page, live_server, seeded, week_records):
    _login(page, live_server, seeded["emails"][PERSONA])
    _visit(page, live_server, BRIEFING)
    page.emulate_media(media="print")
    pdf = page.pdf(format="A4")
    pages = len(re.findall(rb"/Type\s*/Page\b", pdf))
    assert 1 <= pages <= 2, "prints on %d A4 pages" % pages
    printed = " ".join(page.inner_text("body").split())
    assert "Decisions due" in printed and "Risks rising" in printed

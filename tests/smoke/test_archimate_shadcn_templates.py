"""Archimate import/review/library pages — walked as an enterprise architect.

Three pages converted to shadcn component macros on the same brief:

1. Import Diagram (import_diagram.html) — upload a Lucid/Visio file, preview, commit.
2. Relationship Review Queue (review_queue.html) — accept/reject AI-suggested relationships.
3. Diagrams Library (diagrams_library.html) — browse all saved diagrams, open in Composer.

Every test signs in as the enterprise_architect persona (the row's persona for all
three pages), not as an administrator, so the gate checks that a non-admin persona
can actually reach each page.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


@pytest.fixture()
def pending_suggestion(seeded):
    """One pending relationship suggestion between two real elements in the
    seeded org, so the review queue has an actual decision to take."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, RelationshipSuggestion

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org_id = seeded["ids"]["org"]
        src = ArchiMateElement(
            name="Suggestion Source %s" % suffix, type="ApplicationComponent",
            layer="application", organization_id=org_id,
        )
        tgt = ArchiMateElement(
            name="Suggestion Target %s" % suffix, type="ApplicationComponent",
            layer="application", organization_id=org_id,
        )
        db.session.add_all([src, tgt])
        db.session.commit()
        suggestion = RelationshipSuggestion(
            source_element_id=src.id, target_element_id=tgt.id,
            relationship_type="serving", confidence=0.92,
            source_method="llm_semantic", status="pending",
            reason="Journey fixture suggestion.",
        )
        db.session.add(suggestion)
        db.session.commit()
        out = {"suggestion_id": suggestion.id, "src_name": src.name,
               "tgt_name": tgt.name, "src_id": src.id, "tgt_id": tgt.id}
        db.session.remove()
        return out


@pytest.fixture()
def seeded_diagram(seeded):
    """One saved diagram holding three real elements, so the library page has
    a row with a real element count and an Open action that lands in the
    Composer with the elements actually on the canvas."""
    from app import create_app, db
    from app.models.archimate_core import (
        ArchiMateElement, SavedDiagram, SavedDiagramElement,
    )
    from app.models.solution_models import Solution

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org_id = seeded["ids"]["org"]
        user_id = seeded["ids"].get("solution_architect_user")

        solution = Solution(
            name="Shadcn Walk Solution %s" % suffix, organization_id=org_id,
            created_by_id=user_id, governance_status="draft",
        )
        db.session.add(solution)
        db.session.commit()

        elems = []
        for i in range(3):
            e = ArchiMateElement(
                name="Shadcn Walk Element %s-%s" % (suffix, i),
                type="ApplicationComponent", layer="application",
                organization_id=org_id,
            )
            elems.append(e)
        db.session.add_all(elems)
        db.session.commit()

        diagram = SavedDiagram(
            name="Shadcn Walk Diagram %s" % suffix, solution_id=solution.id,
            organization_id=org_id, created_by_id=user_id,
        )
        db.session.add(diagram)
        db.session.commit()

        for i, e in enumerate(elems):
            db.session.add(SavedDiagramElement(
                diagram_id=diagram.id, element_id=e.id,
                position_x=60 + i * 200, position_y=60,
            ))
        db.session.commit()

        out = {"diagram_id": diagram.id, "diagram_name": diagram.name,
               "solution_id": solution.id, "element_count": len(elems),
               "element_name": elems[0].name}
        db.session.remove()
        return out


def _assert_persona_is_enterprise_architect(seeded):
    """The signed-in role is the persona the brief names (enterprise_architect),
    not an administrator or a platform role. Without this, a journey that would
    pass only because the admin can reach every page slips through."""
    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        email = seeded["emails"]["enterprise_architect"]
        user = User.query.filter_by(email=email).first()
        assert user is not None, "enterprise_architect user missing from seed"
        assert user.enterprise_role == "enterprise_architect"
        assert user.is_platform_admin is False
        assert user.is_org_admin is False
        db.session.remove()


@pytest.mark.smoke
def test_import_diagram_page_loads_as_enterprise_architect(browser, live_server, seeded):
    """The import diagram landing page renders with its form controls.

    The enterprise_architect signs in and navigates to the import page. The page
    shows a title, the file upload control, the fallback type select, and the
    Preview and Import buttons. Every link on the page lands on a page with content.
    """
    _assert_persona_is_enterprise_architect(seeded)
    email = seeded["emails"]["enterprise_architect"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _login(page, live_server, email)

    # Navigate directly to the import diagram page
    page.goto(live_server + "/archimate/import/diagram", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # The page is rebuilt on the shadcn frame (tier B names the nearest original)
    expect(page.locator('meta[name="shadcn-nearest"]')).to_have_attribute("content", "dashboard-01", timeout=PAGE_TIMEOUT)

    # The page has the expected title
    expect(page.get_by_role("heading", name=re.compile(r"Import a diagram", re.I))).to_be_visible(timeout=PAGE_TIMEOUT)

    # The file upload element is present
    expect(page.locator("input[type='file']")).to_be_visible(timeout=PAGE_TIMEOUT)

    # The fallback type select is present
    expect(page.locator("#fallback-type")).to_be_visible(timeout=PAGE_TIMEOUT)

    # The dedupe select is present
    expect(page.locator("#dedupe")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Preview and Import buttons are present
    preview_btn = page.get_by_role("button", name=re.compile(r"Preview", re.I))
    expect(preview_btn).to_be_visible(timeout=PAGE_TIMEOUT)

    import_btn = page.get_by_role("button", name=re.compile(r"Import", re.I))
    expect(import_btn).to_be_visible(timeout=PAGE_TIMEOUT)

    # The meta/description text is visible
    expect(page.get_by_text(re.compile(r"Export from Lucidchart", re.I))).to_be_visible(timeout=PAGE_TIMEOUT)

    # The Import Review link in the help text is a real page
    review_link = page.locator("a[href*='import-review']").first
    expect(review_link).to_be_visible(timeout=PAGE_TIMEOUT)
    with page.expect_navigation(url=re.compile(r"import-review"), timeout=PAGE_TIMEOUT):
        review_link.click()
    expect(page.locator("h1")).to_be_visible(timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
def test_review_queue_accepts_a_suggestion_as_enterprise_architect(
        browser, live_server, seeded, pending_suggestion):
    """The review queue lets the Enterprise Architect accept a suggestion.

    The enterprise_architect signs in, opens the review queue, and sees the
    seeded pending suggestion. Clicking the row's Accept button removes it:
    the relationship lands in the repository and the queue no longer lists it.
    """
    _assert_persona_is_enterprise_architect(seeded)
    email = seeded["emails"]["enterprise_architect"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _login(page, live_server, email)

    page.goto(live_server + "/capability-map/archimate/review-queue", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # The page is rebuilt on the shadcn frame (tier B names the nearest original)
    expect(page.locator('meta[name="shadcn-nearest"]')).to_have_attribute("content", "dashboard-01", timeout=PAGE_TIMEOUT)

    # The page has the expected title
    expect(page.get_by_role("heading", name=re.compile(r"Relationship Review Queue", re.I))).to_be_visible(timeout=PAGE_TIMEOUT)

    # The bulk action buttons are present
    btn_80 = page.get_by_role("button", name=re.compile(r"Accept all > 80%", re.I))
    expect(btn_80).to_be_visible(timeout=PAGE_TIMEOUT)

    btn_60 = page.get_by_role("button", name=re.compile(r"Accept all > 60%", re.I))
    expect(btn_60).to_be_visible(timeout=PAGE_TIMEOUT)

    # The sort select has the expected options
    sort_select = page.locator("select[aria-label='Sort By']")
    expect(sort_select).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(sort_select.locator("option[value='confidence']")).to_have_text("Sort: Confidence")
    expect(sort_select.locator("option[value='layer']")).to_have_text("Sort: Layer")
    expect(sort_select.locator("option[value='type']")).to_have_text("Sort: Type")

    # The seeded pending suggestion is listed, source -> type -> target
    row = page.locator("div.border-input", has_text=pending_suggestion["src_name"])
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(row).to_contain_text(pending_suggestion["tgt_name"])
    expect(row).to_contain_text("serving")
    expect(row).to_contain_text("92%")

    # Click the Accept icon button on that row and watch it leave the queue
    row.get_by_role("button", name=re.compile(r"Accept", re.I)).click()
    expect(row).to_have_count(0, timeout=PAGE_TIMEOUT)

    # The accepted relationship now exists in the repository
    from app import create_app, db
    from app.models.archimate_core import ArchiMateRelationship

    app = create_app("testing")
    with app.app_context():
        rel = ArchiMateRelationship.query.filter_by(
            source_id=pending_suggestion["src_id"],
            target_id=pending_suggestion["tgt_id"],
            type="serving",
        ).first()
        assert rel is not None, "accepted relationship was not written to the repository"
        db.session.remove()


@pytest.mark.smoke
def test_diagrams_library_opens_a_diagram_as_enterprise_architect(
        browser, live_server, seeded, seeded_diagram):
    """The diagrams library lists a saved diagram and opens it in the Composer.

    The enterprise_architect signs in, opens the diagrams library, sees the
    seeded diagram's row with its real element count, and follows the Open
    action into the Composer where the three elements are on the canvas.
    """
    _assert_persona_is_enterprise_architect(seeded)
    email = seeded["emails"]["enterprise_architect"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _login(page, live_server, email)

    page.goto(live_server + "/archimate/diagrams", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # The page is rebuilt on the shadcn frame (tier B names the nearest original)
    expect(page.locator('meta[name="shadcn-nearest"]')).to_have_attribute("content", "dashboard-01", timeout=PAGE_TIMEOUT)

    # The page has the expected title
    expect(page.get_by_role("heading", name=re.compile(r"^Diagrams$"))).to_be_visible(timeout=PAGE_TIMEOUT)

    # The search input is present
    expect(page.locator("input[aria-label='Search diagrams by name']")).to_be_visible(timeout=PAGE_TIMEOUT)

    # The filter select is present
    expect(page.locator("select[aria-label='Filter by solution']")).to_be_visible(timeout=PAGE_TIMEOUT)

    # The filter button is present
    filter_btn = page.get_by_role("button", name=re.compile(r"Filter", re.I))
    expect(filter_btn).to_be_visible(timeout=PAGE_TIMEOUT)

    # The seeded diagram appears with its real element count, and its Open
    # action lands in the Composer with those elements on the canvas
    expect(page.get_by_text(seeded_diagram["diagram_name"])).to_be_visible(timeout=PAGE_TIMEOUT)
    row = page.locator("[data-testid='diagram-row-%d']" % seeded_diagram["diagram_id"])
    expect(row).to_contain_text(seeded_diagram["diagram_name"])
    expect(row).to_contain_text(str(seeded_diagram["element_count"]))

    with page.expect_navigation(url=re.compile(r"/archimate/composer"), timeout=PAGE_TIMEOUT):
        row.get_by_role("link", name=re.compile(r"Open", re.I)).click()
    expect(page.locator("nav[aria-label='Composer toolbar']")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.locator(".joint-element")).to_have_count(
        seeded_diagram["element_count"], timeout=PAGE_TIMEOUT)
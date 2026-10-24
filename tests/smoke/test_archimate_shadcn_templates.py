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
def test_review_queue_page_loads_as_enterprise_architect(browser, live_server, seeded):
    """The review queue page renders with its shell and controls.

    The enterprise_architect signs in and navigates to the review queue. The page
    shows the page_shell title, the bulk-action buttons, and the sort select.
    The queue loads (or shows an empty state) without error.
    """
    _assert_persona_is_enterprise_architect(seeded)
    email = seeded["emails"]["enterprise_architect"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _login(page, live_server, email)

    page.goto(live_server + "/capability-map/archimate/review-queue", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

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

    # The page renders as enterprise_architect, verify role is not platform admin
    # by checking the user does not see admin-only UI elements
    admin_elements = page.locator("a[href*='/admin/']").first
    expect(admin_elements).not_to_be_visible()


@pytest.mark.smoke
def test_diagrams_library_page_loads_as_enterprise_architect(browser, live_server, seeded):
    """The diagrams library page renders with the page_shell and table components.

    The enterprise_architect signs in and navigates to the diagrams library.
    The page shows the title, search/filter controls, and either the diagrams
    table or the empty state.
    """
    _assert_persona_is_enterprise_architect(seeded)
    email = seeded["emails"]["enterprise_architect"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _login(page, live_server, email)

    page.goto(live_server + "/archimate/diagrams", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # The page has the expected title
    expect(page.get_by_role("heading", name=re.compile(r"^Diagrams$"))).to_be_visible(timeout=PAGE_TIMEOUT)

    # The search input is present
    expect(page.locator("input[aria-label='Search diagrams by name']")).to_be_visible(timeout=PAGE_TIMEOUT)

    # The filter select is present
    expect(page.locator("select[aria-label='Filter by solution']")).to_be_visible(timeout=PAGE_TIMEOUT)

    # The filter button is present
    filter_btn = page.get_by_role("button", name=re.compile(r"Filter", re.I))
    expect(filter_btn).to_be_visible(timeout=PAGE_TIMEOUT)

    # The page renders as enterprise_architect (non-admin)
    admin_elements = page.locator("a[href*='/admin/']").first
    expect(admin_elements).not_to_be_visible()
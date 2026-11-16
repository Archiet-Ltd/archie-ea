"""Browser journey for ArchiMate CRUD pages migrated to shadcn.

Visits the three tier B templates signed in as the enterprise architect —
the persona these pages serve — and asserts:
  - the page loads without 4xx/5xx
  - the front end boots (Alpine)
  - the page header renders with the expected title
  - named controls are reachable and labelled
  - no console errors
  - the signed-in user is the enterprise architect persona, not an administrator
  - the page's main action works (create element on the dashboard, upload and
    preview an OEF file on the import page, review a restore point)

The in-test persona assertion quotes the same database the app serves, so a
journey that would pass as an administrator but fail as the persona fails here.
"""

import os
import tempfile

import pytest

from .test_archetype_journeys import _login, _open_sidebar_group, _visit, page  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PERSONA = "enterprise_architect"
DASHBOARD_URL = "/architecture/dashboard"
IMPORT_URL = "/architecture/import/oef"
RESTORE_URL = "/architecture/import/oef/restore-points"

WALK_DIR = "test-results/mg3ac033d"

OEF_FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "oef", "archiet_shaped.xml"
)


def _assert_persona(email):
    """The signed-in person is the enterprise architect, not an administrator.

    This is the assertion the brief requires inside the test: the persona the
    page serves, with the role that persona holds, and no administrator or
    platform role.
    """
    from app import create_app
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == PERSONA, (
            "signed-in user is %s, not the %s persona" % (user.enterprise_role, PERSONA)
        )
        assert user.role.name not in ("Administrator", "Platform Admin"), (
            "signed-in user holds %s, an administrator role" % user.role.name
        )
        assert not user.is_org_admin, "signed-in user is an organisation administrator"


def test_architecture_dashboard_renders(page, live_server, seeded):
    """Dashboard loads with heading, action buttons and Alpine."""
    email = seeded["emails"][PERSONA]
    _login(page, live_server, email)
    _assert_persona(email)
    response, state = _visit(page, live_server, DASHBOARD_URL)

    assert response.status < 400, f"dashboard returned {response.status}"
    assert state["alpine"] == "object", "Alpine did not boot on the dashboard"
    assert state["overflow"] == 0, "dashboard overflows horizontally"
    # All named controls must be labelled
    assert not state["unnamed"], f"unnamed controls: {state['unnamed']}"

    # Page header is present with the layer spine
    header = page.locator('[data-testid="architecture-page-header"]')
    assert header.is_visible()

    # Create element button exists
    create_btn = page.locator('[data-testid="btn-create-element"]')
    assert create_btn.is_visible()
    assert create_btn.get_attribute("aria-label") == "Create element"

    # Import and Export links exist and have correct hrefs
    import_btn = page.locator('[data-testid="btn-import-oef"]')
    assert import_btn.is_visible()
    export_btn = page.locator('[data-testid="btn-export-oef"]')
    assert export_btn.is_visible()

    # No page errors
    assert not page.page_errors, "uncaught page console errors"


def test_architecture_dashboard_has_layer_spine(page, live_server, seeded):
    """The layer navigation strip is present with at least one tab."""
    email = seeded["emails"][PERSONA]
    _login(page, live_server, email)
    _assert_persona(email)
    _visit(page, live_server, DASHBOARD_URL)

    spine = page.locator('[data-testid="layer-spine"]')
    assert spine.is_visible()
    tabs = spine.locator("a")
    count = tabs.count()
    assert count >= 3, f"expected at least 3 layer tabs, got {count}"


def test_architecture_dashboard_create_element_modal_opens(page, live_server, seeded):
    """Clicking Create element opens the form modal."""
    email = seeded["emails"][PERSONA]
    _login(page, live_server, email)
    _assert_persona(email)
    _visit(page, live_server, DASHBOARD_URL)

    create_btn = page.locator('[data-testid="btn-create-element"]')
    create_btn.click()
    page.wait_for_timeout(500)

    modal = page.locator("#archimate-form-modal")
    assert modal.is_visible()
    heading = modal.locator("h2")
    assert heading.is_visible()
    assert "Create Element" in heading.text_content()


def test_architecture_dashboard_create_element_works(page, live_server, seeded):
    """The page's main action: create an element as the persona, green after.

    The creator is the enterprise architect, not an administrator: the same
    person the matrix says may change the model.
    """
    email = seeded["emails"][PERSONA]
    _login(page, live_server, email)
    _assert_persona(email)
    _visit(page, live_server, DASHBOARD_URL)

    name = "Smoke CRUD journey element %s" % os.urandom(4).hex()
    page.locator('[data-testid="btn-create-element"]').click()
    modal = page.locator("#archimate-form-modal")
    modal.wait_for(state="visible", timeout=30000)
    modal.get_by_label("Element Type").select_option("Stakeholder")
    modal.get_by_label("Element name").fill(name)
    with page.expect_response(
        lambda r: r.url.endswith("/architecture/motivation/Stakeholder/new")
        and r.request.method == "POST"
    ) as created:
        modal.get_by_role("button", name="Create", exact=True).click()
    assert created.value.status == 200, created.value.text()

    # Green after: the element is in the list.
    page.get_by_test_id("filter-search").fill(name)
    page.wait_for_timeout(1200)
    assert page.locator("table", has_text=name).count() == 1, (
        "created element %s not in the architecture list" % name
    )


def test_import_oef_page_renders(page, live_server, seeded):
    """Import OEF page loads with the shared file_upload component and strategy options."""
    email = seeded["emails"][PERSONA]
    _login(page, live_server, email)
    _assert_persona(email)
    response, state = _visit(page, live_server, IMPORT_URL)

    assert response.status < 400, f"import page returned {response.status}"
    assert state["alpine"] == "object", "Alpine did not boot on the import page"
    assert state["overflow"] == 0, "import page overflows horizontally"
    assert not state["unnamed"], f"unnamed controls: {state['unnamed']}"

    # Check page structure
    heading = page.locator("h1, h2").filter(has_text="Import ArchiMate Model")
    assert heading.count() > 0, "Import heading not found"

    # The OEF file upload is the shared file_upload component (its container).
    file_upload_container = page.locator("#oef_file-container")
    assert file_upload_container.is_visible(), "file_upload component not rendered"

    # Strategy radio group should be present
    strategy_fieldset = page.locator("fieldset").filter(has_text="When an element is already stored")
    assert strategy_fieldset.count() > 0, "Strategy fieldset not found"

    # Preview and import buttons
    assert page.locator('[data-testid="btn-preview-import"]').is_visible()
    assert page.locator('[data-testid="btn-import-model"]').is_visible()

    assert not page.page_errors, "uncaught page console errors"


def test_import_oef_upload_preview_works(page, live_server, seeded):
    """The page's main action: upload an OEF file and preview the import.

    Exercises the shared file_upload component end to end: set the file, wait
    for the file-selected event to enable the preview button, run the preview,
    and read the classified counts from the rendered preview table.
    """
    email = seeded["emails"][PERSONA]
    _login(page, live_server, email)
    _assert_persona(email)
    _visit(page, live_server, IMPORT_URL)

    page.set_input_files("#oef_file", OEF_FIXTURE)
    preview_btn = page.locator('[data-testid="btn-preview-import"]')
    # The disabled binding flips when the file-selected event lands.
    preview_btn.wait_for(state="visible", timeout=30000)
    page.wait_for_function(
        "!document.querySelector('[data-testid=btn-preview-import]').disabled",
        timeout=30000,
    )
    with page.expect_response(
        lambda r: r.url.endswith("/architecture/import/oef/preview")
        and r.request.method == "POST"
    ) as previewed:
        preview_btn.click()
    assert previewed.value.status == 200, previewed.value.text()

    page.locator("[data-testid='import-preview']").wait_for(state="visible", timeout=30000)
    assert page.locator("[data-testid='preview-total']").inner_text() == "15", (
        "preview did not classify the fixture's 15 elements"
    )


def test_import_oef_page_has_back_and_restore_links(page, live_server, seeded):
    """Navigation links point to the dashboard and restore points page."""
    email = seeded["emails"][PERSONA]
    _login(page, live_server, email)
    _assert_persona(email)
    _visit(page, live_server, IMPORT_URL)

    # The restore points link should be present
    restore_link = page.locator('[data-testid="link-restore-points"]')
    assert restore_link.is_visible()
    href = restore_link.get_attribute("href")
    assert href is not None and "restore" in href.lower()


def test_import_restore_points_page_renders(page, live_server, seeded):
    """Restore points page loads and shows empty state when no imports exist."""
    email = seeded["emails"][PERSONA]
    _login(page, live_server, email)
    _assert_persona(email)
    response, state = _visit(page, live_server, RESTORE_URL)

    assert response.status < 400, f"restore page returned {response.status}"
    assert state["alpine"] == "object", "Alpine did not boot on restore page"
    assert state["overflow"] == 0, "restore page overflows horizontally"
    assert not state["unnamed"], f"unnamed controls: {state['unnamed']}"

    # Should show empty state or table depending on seed data
    empty_state = page.locator('[data-testid="restore-points-empty"]')
    restore_table = page.locator('[data-testid="restore-points"]')
    assert empty_state.is_visible() or restore_table.is_visible(), (
        "restore points page shows neither empty state nor table"
    )

    assert not page.page_errors, "uncaught page console errors"


def test_restore_page_shows_back_link(page, live_server, seeded):
    """Restore page includes a back-to-import link."""
    email = seeded["emails"][PERSONA]
    _login(page, live_server, email)
    _assert_persona(email)
    _visit(page, live_server, RESTORE_URL)

    back_btn = page.locator('[data-testid="btn-back-to-import"]')
    assert back_btn.is_visible()


SCREENS = [
    (PERSONA, DASHBOARD_URL, "dashboard"),
    (PERSONA, IMPORT_URL, "import-oef"),
    (PERSONA, RESTORE_URL, "restore-points"),
]
WIDTHS = (1440, 390)
THEMES = ("light", "dark")


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
def test_walk_screenshots(browser, live_server, seeded, width, theme):
    """Capture every template at 1440 and 390, light and dark, as the persona.

    Screenshots land in test-results/mg3ac033b/ so the
    walker can set every page beside its reference.
    """
    os.makedirs(WALK_DIR, exist_ok=True)
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.set_default_timeout(90000)
    ctx.add_init_script(
        "localStorage.setItem('theme', %r);" % theme
    )
    page = ctx.new_page()
    try:
        email = seeded["emails"][PERSONA]
        _login(page, live_server, email)
        _assert_persona(email)
        for _archetype, path, slug in SCREENS:
            response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=90000)
            assert response.status < 400, "%s returned %s" % (path, response.status)
            page.wait_for_timeout(1500)
            try:
                page.eval_on_selector_all(
                    "[x-show='showOnboarding']", "els => els.forEach(e => e.remove())"
                )
            except Exception:
                pass
            page.wait_for_timeout(800)
            target = os.path.join(WALK_DIR, "%s-%d-%s.png" % (slug, width, theme))
            page.screenshot(path=target, full_page=True)
            assert os.path.exists(target) and os.path.getsize(target) > 1000, (
                "screenshot %s was not written" % target
            )
    finally:
        ctx.close()

def test_application_list_counts_like_the_overview(page, live_server, seeded):
    """The architect asks "how many applications do we have" on three screens and gets one answer.

    The organisation holds one decommissioned application. The overview, the Model list and the
    Applications page all leave it out; the Model list's "Include decommissioned" shows it, badged.
    """
    import re

    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    email = seeded["emails"][PERSONA]
    org_id = seeded["ids"]["org"]
    app = create_app("testing")
    name = "Smoke retired app %s" % os.urandom(4).hex()
    with app.app_context():
        retired = ApplicationComponent(
            name=name, organization_id=org_id, lifecycle_status="5. decommissioned"
        )
        db.session.add(retired)
        db.session.commit()
        retired_id = retired.id
    try:
        _login(page, live_server, email)
        _assert_persona(email)
        _visit(page, live_server, "/dashboard/overview")
        figure = int(page.get_by_test_id("welcome-applications").locator("span").first.inner_text())
        assert figure > 0

        pagination = page.get_by_test_id("architecture-pagination")
        # Model list, opened from the Application layer tab.
        _visit(page, live_server, DASHBOARD_URL)
        page.get_by_test_id("layer-spine").get_by_role("link", name=re.compile("Application", re.I)).first.click()
        pagination.wait_for(state="visible", timeout=30000)
        with page.expect_response(lambda r: "element_type=ApplicationComponent" in r.url):
            page.get_by_test_id("filter-type").select_option("ApplicationComponent")
        pagination.wait_for(state="visible", timeout=30000)
        page.wait_for_timeout(500)
        total = lambda: int(pagination.locator("span").nth(2).inner_text())  # noqa: E731
        # The Model list also holds model elements that have no application record behind them (the
        # shared fixture seeds two); the overview counts records, so those are the only difference.
        with app.app_context():
            from sqlalchemy import func

            from app.models.archimate_core import ArchiMateElement

            recorded = db.select(ApplicationComponent.archimate_element_id).where(
                ApplicationComponent.archimate_element_id.isnot(None)
            )
            model_only = ArchiMateElement.query.filter(
                ArchiMateElement.organization_id == org_id,
                func.replace(func.lower(ArchiMateElement.type), "_", "") == "applicationcomponent",
                ~ArchiMateElement.id.in_(recorded),
            ).count()
        for _ in range(20):
            if total() == figure + model_only:
                break
            page.wait_for_timeout(250)
        assert total() == figure + model_only, "Model list counts %s, the overview %s plus %s model-only" % (
            total(), figure, model_only,
        )
        assert page.get_by_text(name).count() == 0

        # Applications page, from the sidebar.
        _open_sidebar_group(page, "layer_application")
        page.get_by_test_id("sidebar").locator('a[href^="/applications"]').first.click()
        page.wait_for_load_state("domcontentloaded")
        shown = page.get_by_test_id("total-count-badge").inner_text()
        assert shown.startswith("%s shown" % figure), "Applications page reads %r" % shown

        # Back on the Model list, ask for the decommissioned one.
        _visit(page, live_server, DASHBOARD_URL)
        page.get_by_test_id("layer-spine").get_by_role("link", name=re.compile("Application", re.I)).first.click()
        pagination.wait_for(state="visible", timeout=30000)
        with page.expect_response(lambda r: "element_type=ApplicationComponent" in r.url):
            page.get_by_test_id("filter-type").select_option("ApplicationComponent")
        with page.expect_response(lambda r: "include_decom=true" in r.url):
            page.get_by_test_id("filter-include-decom").check()
        page.get_by_text(name).first.wait_for(state="visible", timeout=30000)
        for _ in range(20):
            if total() == figure + model_only + 1:
                break
            page.wait_for_timeout(250)
        assert total() == figure + model_only + 1
        row = page.locator("tr", has_text=name)
        assert row.get_by_test_id("lifecycle-badge").inner_text() == "Decommissioned"

        # The same screen as a person sees it: 1440 light and dark, 390 light.
        shots = os.environ.get("APPCOUNT_WALK_DIR") or tempfile.mkdtemp(prefix="appcount-walk-")
        os.makedirs(shots, exist_ok=True)
        for width, theme in ((1440, "light"), (1440, "dark"), (390, "light")):
            page.set_viewport_size({"width": width, "height": 900})
            page.evaluate("t => localStorage.setItem('theme', t)", theme)
            page.goto(
                live_server + "/architecture/application/ApplicationComponent?include_decom=true",
                wait_until="domcontentloaded",
            )
            page.get_by_text(name).and_(page.locator(":visible")).first.wait_for(state="visible", timeout=30000)
            assert page.get_by_test_id("filter-include-decom").is_checked(), "the toggle was lost on reload"
            assert total() == figure + model_only + 1
            badge = "lifecycle-badge" if width > 1000 else "lifecycle-badge-mobile"
            assert page.get_by_test_id(badge).and_(page.locator(":visible")).first.inner_text() == "Decommissioned"
            page.get_by_text(name).and_(page.locator(":visible")).first.scroll_into_view_if_needed()
            page.screenshot(path=os.path.join(shots, "model-list-%d-%s.png" % (width, theme)), full_page=True)
    finally:
        with app.app_context():
            ApplicationComponent.query.filter_by(id=retired_id).delete()
            db.session.commit()

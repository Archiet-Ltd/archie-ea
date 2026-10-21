"""Diagrams library page + repository-level element delete, walked through
as a solution architect would actually use them.

Two features added 10 Sep 2026 answering the owner's questions:

1. "Is there a list to see all available diagrams?" -- there wasn't one
   outside the Composer's own per-solution picker. Added a cross-cutting
   "Diagrams" library page (sidebar, alongside Applications/Capabilities/
   Vendors), listing every saved diagram with its solution, element count,
   and a link straight into the Composer.

2. "When should the user control what is added to/removed from the
   repository?" -- creation-side control already existed (accept/reject
   per generated element). Removal did not: the Composer's only delete
   action ("Remove from Canvas") explicitly leaves the element in the
   repository. A "Delete from Repository" control existed in the context
   menu markup but called a URL that does not exist and read usage counts
   from a response that does not carry them -- it has never worked. Fixed
   with a real usage-check endpoint and a real delete endpoint that blocks
   when an element is still referenced elsewhere unless the caller is an
   admin.

This test walks both, as an architect would: find the library page from the
sidebar, open a diagram from it, then try to delete a shared element (should
be blocked with real counts) and a free-standing one (should succeed).
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD, all_modules_link

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


@pytest.fixture()
def two_diagrams(seeded):
    """One shared element used by two diagrams + a relationship, one
    free-standing element used by neither -- exercises both delete paths."""
    from app import create_app, db
    from app.models.archimate_core import (
        ArchiMateElement, ArchiMateRelationship, SavedDiagram,
        SavedDiagramElement, SavedDiagramRelationship,
    )
    from app.models.solution_models import Solution

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org_id = seeded["ids"]["org"]
        user_id = seeded["ids"]["solution_architect_user"]

        solution = Solution(
            name="Diagrams Library Demo %s" % suffix, organization_id=org_id,
            created_by_id=user_id, governance_status="draft",
        )
        db.session.add(solution)
        db.session.commit()

        shared = ArchiMateElement(
            name="Shared Integration Hub %s" % suffix, type="ApplicationComponent",
            layer="application", organization_id=org_id,
        )
        free = ArchiMateElement(
            name="Orphan Component %s" % suffix, type="ApplicationComponent",
            layer="application", organization_id=org_id,
        )
        other_a = ArchiMateElement(
            name="Diagram A Partner %s" % suffix, type="ApplicationComponent",
            layer="application", organization_id=org_id,
        )
        other_b = ArchiMateElement(
            name="Diagram B Partner %s" % suffix, type="ApplicationComponent",
            layer="application", organization_id=org_id,
        )
        db.session.add_all([shared, free, other_a, other_b])
        db.session.commit()

        rel = ArchiMateRelationship(
            source_id=shared.id, target_id=other_a.id, type="serving",
            organization_id=org_id,
        )
        db.session.add(rel)
        db.session.commit()

        diagram_a = SavedDiagram(name="Diagram A %s" % suffix, solution_id=solution.id,
                                  organization_id=org_id, created_by_id=user_id)
        diagram_b = SavedDiagram(name="Diagram B %s" % suffix, solution_id=solution.id,
                                  organization_id=org_id, created_by_id=user_id)
        db.session.add_all([diagram_a, diagram_b])
        db.session.commit()

        db.session.add_all([
            SavedDiagramElement(diagram_id=diagram_a.id, element_id=shared.id, position_x=60, position_y=60),
            SavedDiagramElement(diagram_id=diagram_a.id, element_id=other_a.id, position_x=300, position_y=60),
            SavedDiagramElement(diagram_id=diagram_a.id, element_id=free.id, position_x=60, position_y=300),
            SavedDiagramElement(diagram_id=diagram_b.id, element_id=shared.id, position_x=60, position_y=60),
            SavedDiagramElement(diagram_id=diagram_b.id, element_id=other_b.id, position_x=300, position_y=60),
        ])
        db.session.add(SavedDiagramRelationship(diagram_id=diagram_a.id, relationship_id=rel.id))
        db.session.commit()

        ids = {
            "solution_id": solution.id,
            "diagram_a_id": diagram_a.id, "diagram_b_id": diagram_b.id,
            "shared_id": shared.id, "free_id": free.id,
            "diagram_a_name": diagram_a.name,
        }
        db.session.remove()
        return ids


@pytest.mark.smoke
def test_diagrams_library_lists_and_opens_diagrams(browser, live_server, seeded, two_diagrams):
    email = seeded["emails"]["solution_architect"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1960, "height": 1080})
    page = context.new_page()
    _login(page, live_server, email)

    # Found from the sidebar, not a typed URL -- this is the real discovery path.
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    sidebar_link = all_modules_link(page, "Diagrams")
    expect(sidebar_link).to_be_visible(timeout=PAGE_TIMEOUT)
    sidebar_link.click()
    page.wait_for_url(re.compile(r"/archimate/diagrams"), timeout=PAGE_TIMEOUT)

    expect(page.get_by_text(two_diagrams["diagram_a_name"])).to_be_visible(timeout=PAGE_TIMEOUT)
    row = page.locator("[data-testid='diagram-row-%d']" % two_diagrams["diagram_a_id"])
    expect(row).to_contain_text("3")  # element_count for diagram A

    with page.expect_navigation(url=re.compile(r"/archimate/composer"), timeout=PAGE_TIMEOUT):
        row.get_by_role("link", name=re.compile("Open", re.I)).click()
    expect(page.locator("nav[aria-label='Composer toolbar']")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.locator(".joint-element")).to_have_count(3, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
def test_repository_delete_blocked_when_shared_allowed_when_free(browser, live_server, seeded, two_diagrams):
    email = seeded["emails"]["solution_architect"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1960, "height": 1080})
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())  # Platform.modal.confirm may fall back to native in this harness
    _login(page, live_server, email)

    page.goto(
        live_server + "/archimate/composer?viewpoint_id=%d&solution_id=%d"
        % (two_diagrams["diagram_a_id"], two_diagrams["solution_id"]),
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    expect(page.locator(".joint-element")).to_have_count(3, timeout=PAGE_TIMEOUT)

    def _delete_element_named(name_substr):
        node = page.locator(".joint-element", has_text=name_substr).first
        node.click(button="right")
        # Alpine's x-show leaves a prior menu in the DOM (display:none), so a
        # second right-click leaves two ".context-menu"-matching nodes; only
        # the currently-visible one is the one this click just opened.
        menu = page.locator('[aria-label="Element actions"]:visible').first
        expect(menu).to_be_visible(timeout=PAGE_TIMEOUT)
        # dispatch_event, not click(): the context menu repositions itself
        # after showing (composer.js _fitCtxMenu, on $nextTick), which races
        # a coordinate-based click against the reflow. Dispatching the click
        # event directly on the resolved node sidesteps the hit-test.
        menu.get_by_text(re.compile("Delete from Repository", re.I)).dispatch_event("click")

        # deleteFromRepository() first fetches usage, then opens a custom
        # Platform.modal.confirm dialog (ui/modal.js -- not a native browser
        # dialog, so page.on("dialog") never fires for it). Answer it for
        # real, or the delete request is never even sent.
        confirm_dialog = page.locator('[id^="modal-confirm-"]:visible').first
        expect(confirm_dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        confirm_dialog.get_by_role("button", name=re.compile("^Confirm$")).dispatch_event("click")

    # Shared element: referenced by relationship + a second diagram -> blocked.
    _delete_element_named("Shared Integration Hub")
    page.wait_for_timeout(1500)
    # Blocked: still 3 elements on canvas (nothing removed), and the shared
    # element is still there to prove the block, not just a stalled UI.
    expect(page.locator(".joint-element", has_text="Shared Integration Hub")).to_have_count(1, timeout=PAGE_TIMEOUT)

    # Free-standing element: zero usage -> real delete succeeds.
    _delete_element_named("Orphan Component")
    expect(page.locator(".joint-element")).to_have_count(2, timeout=PAGE_TIMEOUT)
    expect(page.locator(".joint-element", has_text="Orphan Component")).to_have_count(0, timeout=PAGE_TIMEOUT)

    # And it is really gone from the repository, not just off this canvas --
    # the exact distinction "Remove from Canvas" vs "Delete from Repository"
    # exists to make real.
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    app = create_app("testing")
    with app.app_context():
        assert db.session.get(ArchiMateElement, two_diagrams["free_id"]) is None
        assert db.session.get(ArchiMateElement, two_diagrams["shared_id"]) is not None
        db.session.remove()

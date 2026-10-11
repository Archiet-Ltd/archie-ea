"""The Solutions list must never tell a user "no solutions found" while their
organisation actually has some -- it must disclose what is hiding them
(ownership/role filter, or the default shell/archived filter), per S-01's
precedent and its 17 Sep 2026 extension (bucket:
arb-chart-and-solutions-data-disagreement, Task B).

Ground truth (production, 2026-09-17, org 11): `qa-solution-architect`'s two
"hidden" solutions were owned by the SAME user, not someone else -- they were
excluded by the default shell filter (draft, no description, no narrative,
version 1), not by the ownership filter. This journey drives the ownership
case with a second, non-privileged persona in the seeded org (who did not
create the fixture's Solution rows) to prove the general disclosure mechanism
-- not only the specific shell-filter case reproduced in production.
"""

from __future__ import annotations

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").dispatch_event("click")
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def test_solutions_list_discloses_hidden_count_instead_of_false_empty_state(browser, live_server, seeded):
    """The seeded org's `solution` and `reference_solution` fixtures are owned
    by the solution_architect persona. An application_manager in the SAME org,
    who created neither, must be told solutions exist and are hidden by
    ownership -- not "create your first solution", which would be a false
    claim that the org has none.

    application_manager (not business_architect) is used deliberately: per
    app/models/user.py's can_vote_arb()/can_manage_portfolio(), a
    business_architect is granted `_can_see_all = True` in the route, so a
    business_architect sees every org solution regardless of ownership and
    the disclosure code path (`if not _can_see_all`) is never exercised --
    round-1's test passed for that reason, not because anything was actually
    disclosed. application_manager holds none of the roles in that check, so
    `_can_see_all` is genuinely False and this test exercises the real
    disclosure path.
    """
    page = browser.new_page()
    _login(page, live_server, seeded["emails"]["application_manager"])
    page.goto(live_server + "/solutions/", wait_until="networkidle", timeout=PAGE_TIMEOUT)

    body_text = page.locator("body").inner_text()
    assert "Get started by creating your first solution" not in body_text, (
        "false first-run empty state: the org has solutions, they are just "
        "not owned by this viewer"
    )
    disclosure = page.locator('[data-testid="solutions-hidden-disclosure"]')
    assert disclosure.count() > 0, (
        "the role-hidden disclosure block must actually render when solutions "
        "are hidden by ownership, not merely avoid the false empty-state text"
    )
    heading_text = page.locator('[data-testid="solutions-empty-state-heading"]').inner_text()
    assert "Showing 0 of" in heading_text and "0 of 0" not in heading_text, (
        f"expected the disclosed org_total to be a real non-zero figure, got: {heading_text!r}"
    )


def test_solutions_list_status_all_discloses_for_non_privileged_persona(browser, live_server, seeded):
    """B-2 regression test: the ?status=all escape hatch the S-01 fix added
    must not silently reset org_total/disclosure counters to their zero/None
    defaults for a non-privileged persona -- that was the exact false
    "create your first solution" empty state resurfacing through the fix's
    own new escape hatch."""
    page = browser.new_page()
    _login(page, live_server, seeded["emails"]["application_manager"])
    page.goto(live_server + "/solutions/?status=all", wait_until="networkidle", timeout=PAGE_TIMEOUT)

    body_text = page.locator("body").inner_text()
    assert "Get started by creating your first solution" not in body_text, (
        "?status=all must not regress into the false first-run empty state "
        "for a non-privileged user when the org actually has solutions"
    )
    # R2-3 (round-3 refuter fix): the assertion above would also pass on a
    # 500, a redirect, or a silently blank page -- none of which prove
    # disclosure actually happened. Assert the disclosure block is present
    # AND carries a real, non-zero total, mirroring the stronger assertions
    # in the previous test.
    disclosure = page.locator('[data-testid="solutions-hidden-disclosure"]')
    assert disclosure.count() > 0, (
        "the hidden-disclosure block must actually render for ?status=all, "
        "not merely avoid the false empty-state text"
    )
    heading_text = page.locator('[data-testid="solutions-empty-state-heading"]').inner_text()
    assert "Showing 0 of" in heading_text and "0 of 0" not in heading_text, (
        f"expected a real non-zero disclosed org_total, got: {heading_text!r}"
    )


def test_solutions_list_status_all_returns_rows_for_the_owner(browser, live_server, seeded):
    page = browser.new_page()
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/solutions/?status=all", wait_until="networkidle", timeout=PAGE_TIMEOUT)

    body_text = page.locator("body").inner_text()
    assert "Get started by creating your first solution" not in body_text


def test_empty_state_copy_matches_design(browser, live_server):
    """The empty state (when org has no solutions at all) must use the
    approved copy: 'No solutions yet' heading and
    'Create a solution to start its architecture blueprint.' description."""
    import uuid
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Empty Org %s" % suffix, slug="empty-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        user = User(
            email="empty.%s@example.com" % suffix, first_name="Empty", last_name="Tenant",
            organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        email = user.email

    page = browser.new_page()
    _login(page, live_server, email)
    page.goto(live_server + "/solutions/", wait_until="networkidle", timeout=PAGE_TIMEOUT)

    heading = page.locator('[data-testid="solutions-empty-state-heading"]')
    assert heading.count() > 0, "empty-state heading must be present"
    heading_text = heading.inner_text()
    assert "No solutions yet" in heading_text, (
        f"empty state must read 'No solutions yet', got: {heading_text!r}"
    )
    body_text = page.locator("body").inner_text()
    assert "Create a solution to start its architecture blueprint." in body_text, (
        "empty state description must be present"
    )


def test_hidden_draft_state_copy_matches_design(browser, live_server):
    """When the only hidden rows are the default filter's empty drafts,
    the disclosure must use the approved copy: 'Your draft is waiting'
    and 'Untitled drafts stay out of this list until they have a problem
    statement.'

    Creates a fresh org with a single draft solution that has no
    description, so the default shell filter hides it and the
    hidden-draft state renders unconditionally."""
    import uuid
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.solution_models import Solution
    from app.models.user import Role, User

    import datetime

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Hidden Draft Org %s" % suffix, slug="hidden-draft-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        user = User(
            email="hidden-draft.%s@example.com" % suffix, first_name="Hidden", last_name="Draft",
            organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
            onboarding_completed_at=datetime.datetime.now(datetime.timezone.utc),
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        # Create a draft solution with no description — this is exactly what
        # the default shell filter hides, triggering the hidden-draft state.
        solution = Solution(
            name="Hidden draft %s" % suffix,
            description=None,
            organization_id=org.id,
            created_by_id=user.id,
            status="draft",
            governance_status="draft",
        )
        db.session.add(solution)
        db.session.commit()
        email = user.email

    page = browser.new_page()
    _login(page, live_server, email)
    page.goto(live_server + "/solutions/", wait_until="networkidle", timeout=PAGE_TIMEOUT)

    body_text = page.locator("body").inner_text()
    assert "Your draft is waiting" in body_text, (
        "hidden-draft state must render 'Your draft is waiting' heading"
    )
    assert "Untitled drafts stay out of this list" in body_text, (
        "hidden-draft state must render the disclosure copy"
    )

"""Journey, in a browser: creating and editing a solution (Slice S2).

A solution is created only when "Create" is pressed on the New solution dialog -- opening
the dialog or cancelling it leaves no record behind. Everyone in the organisation can read a
solution; only an architect, the organisation's admin, or the solution's own creator can edit
it or download its document. A second organisation cannot see or touch the first one's work.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def cto_org():
    return create_fresh_org("cto", platform_role="Architect")


@pytest.fixture(scope="module")
def architect_org():
    return create_fresh_org("enterprise_architect", org_admin=True, platform_role="Administrator")


def _solution_count(org_id):
    from app import create_app

    app = create_app("testing")
    with app.app_context():
        from app.models.solution_models import Solution

        return Solution.query.filter_by(organization_id=org_id).count()


def _role_of(email):
    from app import create_app

    app = create_app("testing")
    with app.app_context():
        from app.models.user import User

        user = User.query.filter_by(email=email).one()
        return user.enterprise_role


def test_cto_cancels_new_solution_dialog_creates_nothing(live_server, cto_org, page):
    """A startup CTO opens New solution, cancels, and no record exists afterward."""
    email = cto_org["emails"]["cto"]
    assert _role_of(email) == "cto"

    sign_in(page, live_server, email)
    before = _solution_count(cto_org["org_id"])

    page.goto(live_server + "/solutions/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.click('[data-testid="btn-new-solution"]')
    dialog = page.locator("#new-solution-dialog")
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    page.fill("#new-sol-name", "Should not be created")
    page.click('[data-testid="btn-new-solution-cancel"]')
    expect(dialog).to_be_hidden(timeout=PAGE_TIMEOUT)

    after = _solution_count(cto_org["org_id"])
    assert after == before, "cancelling the dialog must create no record"


def test_cto_creates_solution_and_lands_on_its_record(live_server, cto_org, page):
    """The CTO fills the dialog, presses Create, and lands on the new solution's record."""
    email = cto_org["emails"]["cto"]
    sign_in(page, live_server, email)
    before = _solution_count(cto_org["org_id"])

    page.goto(live_server + "/solutions/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.click('[data-testid="btn-new-solution"]')
    dialog = page.locator("#new-solution-dialog")
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    page.fill("#new-sol-name", "Move monitoring to Grafana Cloud")
    page.fill(
        "#new-sol-problem",
        "If we move monitoring to Grafana Cloud, what does it touch and what will it cost?",
    )
    page.click('[data-testid="btn-new-solution-create"]')
    # page.wait_for_url(lambda ...) waits for a *navigation event*; if the
    # dialog's fetch+location.assign completes before the listener attaches,
    # the event already happened and this hangs for the full timeout. Poll
    # the URL instead -- expect() re-checks on an interval, so it does not
    # depend on catching a one-shot event.
    expect(page).to_have_url(
        re.compile(r"/solutions/\d+$"), timeout=PAGE_TIMEOUT
    )

    after = _solution_count(cto_org["org_id"])
    assert after == before + 1, "pressing Create must add exactly one record"

    solution_id = int(page.url.rstrip("/").split("/")[-1])
    from app import create_app

    app = create_app("testing")
    with app.app_context():
        from app.models.solution_models import Solution

        solution = Solution.query.get(solution_id)
        assert solution.name == "Move monitoring to Grafana Cloud"
        assert solution.description == (
            "If we move monitoring to Grafana Cloud, what does it touch and what will it cost?"
        )
        assert solution.organization_id == cto_org["org_id"]


def test_architect_edits_and_downloads_without_refusal(live_server, architect_org, page):
    """D6/D8: an enterprise architect, not the creator, can edit the demo solution and
    download its document -- the old refusal fires only for someone the role check excludes."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.solution_models import Solution
        from app.models.user import User

        creator = User.query.filter_by(organization_id=architect_org["org_id"]).first()
        solution = Solution(
            name="Demo solution for edit test",
            description="A solution created by someone else, to prove edit is role-based.",
            organization_id=architect_org["org_id"],
            created_by_id=creator.id if creator else None,
        )
        db.session.add(solution)
        db.session.commit()
        solution_id = solution.id

    email = architect_org["emails"]["enterprise_architect"]
    assert _role_of(email) == "enterprise_architect"
    sign_in(page, live_server, email)

    status, body = api(
        page,
        "POST",
        "/solutions/%d/edit" % solution_id,
        {"name": "Demo solution, edited by the architect"},
    )
    assert status in (200, 302), "an enterprise architect must not be refused edit: %r" % (body,)

    with app.app_context():
        from app.models.solution_models import Solution

        refreshed = Solution.query.get(solution_id)
        assert refreshed.name == "Demo solution, edited by the architect"

    # A successful download never completes a page navigation -- Playwright
    # raises precisely because the response became a download, not a
    # rendered page. Use the request API, which returns the response without
    # trying to load it as a page, to check the actual status.
    download_response = page.request.get(
        live_server + "/solutions/%d/sad/download" % solution_id
    )
    assert download_response.status == 200, (
        "an enterprise architect must not be refused the document download"
    )


def test_viewer_without_architect_role_is_refused_edit(live_server, cto_org, page):
    """A plain CTO persona (not an architect role, not the creator, not the admin) is cleanly
    refused -- not a 500, not a silent no-op."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.solution_models import Solution

        solution = Solution(
            name="Solution owned by someone else",
            description="Created by a different user in the same org.",
            organization_id=cto_org["org_id"],
            created_by_id=None,
        )
        db.session.add(solution)
        db.session.commit()
        solution_id = solution.id

    email = cto_org["emails"]["cto"]
    sign_in(page, live_server, email)

    status, _body = api(page, "POST", "/solutions/%d/edit" % solution_id, {"name": "Hijacked"})
    assert status == 403, "a non-architect, non-creator user must be cleanly refused, got %r" % status

    with app.app_context():
        from app.models.solution_models import Solution

        unchanged = Solution.query.get(solution_id)
        assert unchanged.name == "Solution owned by someone else"


def test_second_organisation_cannot_see_or_edit_the_first(live_server, cto_org, architect_org, page):
    """Two real organisations: the architect org's solutions are invisible to the CTO org,
    and attempting to reach one by id from the other org is refused."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.solution_models import Solution

        solution = Solution(
            name="Architect org's private solution",
            description="Must never be visible to another organisation.",
            organization_id=architect_org["org_id"],
        )
        db.session.add(solution)
        db.session.commit()
        other_org_solution_id = solution.id

    email = cto_org["emails"]["cto"]
    sign_in(page, live_server, email)

    response = page.goto(
        live_server + "/solutions/%d/edit" % other_org_solution_id, timeout=PAGE_TIMEOUT
    )
    assert response.status in (403, 404), (
        "a user in one organisation must not reach another organisation's solution by id, got %r"
        % response.status
    )

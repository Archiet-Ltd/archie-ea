"""Journey: a fresh workspace must have a working way to create its first
Solution (E2E-4).

/solutions/ offered "New from Template" (needs an existing solution to make
one from -- circular for a fresh workspace) and "Start Architecture
Journey", whose primary action (POST /architecture-journey/start-architecture)
creates a separate ArchitectureJourney artifact with, by that route's own
docstring, "no Solution" at all. /solutions/new 404s. A fresh tenant had no
path to a first Solution.

The real create-a-solution endpoint (POST /architecture-journey/start)
already existed and worked -- it was only reachable from a small secondary
button buried in a sidebar card on the journey hub page
("Need a solution specifically?"). This adds a directly-labelled "New
Solution" action to /solutions/ itself, calling that same working endpoint.
"""
import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey


def test_new_solution_button_creates_a_real_solution(app, client):
    """/solutions/ must expose a control that actually creates a Solution."""
    from app import db
    from app.models.solution_models import Solution

    with app.app_context():
        org_id = make_org(db, "NewSolBtn")
        architect_id = make_user(
            db, org_id, "newsolbtn", enterprise_role="enterprise_architect",
            role_name="Architect",
        )

    login(client, architect_id)

    # The page must actually render the control before we exercise its endpoint.
    page = client.get("/solutions/")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert 'data-testid="btn-new-solution"' in body
    assert "startSolution" in body

    with app.app_context():
        before = db.session.execute(
            db.select(db.func.count(Solution.id)).filter_by(organization_id=org_id)
        ).scalar()

    response = client.post("/architecture-journey/start", json={})
    assert response.status_code == 200, response.get_data(as_text=True)[:500]
    payload = response.get_json()
    assert payload["data"]["solution_id"] is not None
    assert payload["data"]["redirect"] == f"/architecture-journey/{payload['data']['solution_id']}"

    # PERSISTED -- read the database, not the response the route just built.
    with app.app_context():
        db.session.expunge_all()
        row = db.session.get(Solution, payload["data"]["solution_id"])
        assert row is not None
        assert row.organization_id == org_id
        assert row.created_by_id == architect_id

        after = db.session.execute(
            db.select(db.func.count(Solution.id)).filter_by(organization_id=org_id)
        ).scalar()
        assert after == before + 1


def test_new_solution_is_the_primary_header_button(app, client):
    """/solutions/ header must have exactly one filled primary button, and it
    must be "New Solution" — not "Start Architecture Journey"."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "PrimaryBtn")
        architect_id = make_user(
            db, org_id, "primarybtn", enterprise_role="enterprise_architect",
            role_name="Architect",
        )

    login(client, architect_id)
    page = client.get("/solutions/")
    assert page.status_code == 200
    body = page.get_data(as_text=True)

    # The primary (filled) button must be "New Solution"
    assert 'data-testid="btn-new-solution"' in body
    # "Start Architecture Journey" must NOT be the filled primary button
    assert 'data-testid="btn-start-journey"' in body
    # The primary button must use bg-primary (filled style)
    # Find the btn-new-solution element and verify it has bg-primary.
    # The button component renders class= before data-testid=, so match
    # either ordering.
    import re
    btn_match = re.search(
        r'<button[^>]*data-testid="btn-new-solution"[^>]*>',
        body
    )
    assert btn_match is not None, "btn-new-solution must be present"
    btn_tag = btn_match.group(0)
    assert "bg-primary" in btn_tag, (
        "New Solution must be the filled primary button (bg-primary), "
        f"got: {btn_tag[:200]}"
    )
    # "Start Architecture Journey" must be an outline button, not filled
    journey_match = re.search(
        r'<a[^>]*data-testid="btn-start-journey"[^>]*>',
        body
    )
    assert journey_match is not None, "btn-start-journey must be present"
    journey_tag = journey_match.group(0)
    assert "bg-primary" not in journey_tag, (
        "Start Architecture Journey must not be the filled primary button, "
        f"got: {journey_tag[:200]}"
    )


def test_empty_state_has_new_solution_as_primary(app, client):
    """When the org has no solutions, the empty state must show "New Solution"
    as the primary action with correct copy."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "EmptyState")
        architect_id = make_user(
            db, org_id, "emptystate", enterprise_role="enterprise_architect",
            role_name="Architect",
        )

    login(client, architect_id)
    page = client.get("/solutions/")
    assert page.status_code == 200
    body = page.get_data(as_text=True)

    # Empty state must use the empty_state component with correct copy
    assert "No solutions yet" in body, (
        "empty state must read 'No solutions yet'"
    )
    assert "Create a solution to start its architecture blueprint." in body, (
        "empty state description must be present"
    )
    # The primary action must be "New Solution"
    assert 'data-testid="btn-new-solution"' in body
    # "Start Architecture Journey" must not appear as the primary empty-state CTA
    assert "Start Architecture Journey" not in body or \
        'data-testid="btn-start-journey"' in body

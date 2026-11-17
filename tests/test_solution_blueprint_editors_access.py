"""Who may open and save the solution section editors (?edit=blueprint) and their save routes.

Two real organisations: the author and a same-organisation viewer in one, an outsider in the other.
What a person sees and clicks is proved by the blueprint smoke journeys.
"""
import uuid

import pytest

from tests.test_solution_record import _login, _user


@pytest.fixture
def setup(app, db_session, make_org):
    from app.models.solution_models import Solution

    org = make_org("sections")
    other = make_org("sections-other")
    author = _user(db_session, org, "solution_architect", "author")
    viewer = _user(db_session, org, "viewer", "viewer")
    outsider = _user(db_session, other, "solution_architect", "outsider")
    solution = Solution(
        name="Sections %s" % uuid.uuid4().hex[:6], organization_id=org.id, created_by_id=author.id,
        governance_status="draft",
    )
    db_session.add(solution)
    db_session.flush()
    return app.test_client(), author, viewer, outsider, solution


def test_the_author_opens_the_editors_from_the_record_menu(setup):
    client, author, viewer, outsider, solution = setup
    _login(client, author.id)
    record = client.get("/solutions/%d" % solution.id)
    assert record.status_code == 200
    assert b"Edit design sections" in record.data
    assert ("/solutions/%d?edit=blueprint" % solution.id).encode() in record.data
    page = client.get("/solutions/%d?edit=blueprint" % solution.id)
    assert page.status_code == 200
    assert b"application_cooperation" in page.data


def test_a_viewer_is_not_offered_or_given_the_editors(setup):
    client, author, viewer, outsider, solution = setup
    _login(client, viewer.id)
    record = client.get("/solutions/%d" % solution.id)
    if record.status_code == 200:
        assert b"Edit design sections" not in record.data
    assert client.get("/solutions/%d?edit=blueprint" % solution.id).status_code == 403


def test_a_viewer_cannot_save_through_the_section_routes(setup, db_session):
    client, author, viewer, outsider, solution = setup
    _login(client, viewer.id)
    resp = client.post(
        "/solutions/%d/governance-exceptions" % solution.id,
        json={"exception_description": "Viewer wrote this"},
    )
    assert resp.status_code == 403
    _login(client, author.id)
    resp = client.post(
        "/solutions/%d/governance-exceptions" % solution.id,
        json={"exception_description": "Author wrote this"},
    )
    assert resp.status_code == 201


def test_another_organisation_is_refused_the_editors_and_the_save(setup):
    client, author, viewer, outsider, solution = setup
    _login(client, outsider.id)
    assert client.get("/solutions/%d?edit=blueprint" % solution.id).status_code in (403, 404)
    resp = client.post(
        "/solutions/%d/governance-exceptions" % solution.id,
        json={"exception_description": "Outsider wrote this"},
    )
    assert resp.status_code in (403, 404)

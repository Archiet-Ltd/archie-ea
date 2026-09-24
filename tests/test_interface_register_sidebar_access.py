"""Task 05 round 2 (D-05-1/D-05-2): security_architect and data_architect are
authorised (per interface_register's own ``_guard()``, which checks the
``data_integration`` section — see app/modules/interface_register/routes/
register_routes.py) to reach ``/interface-register/``, but until this fix
neither role's rendered sidebar carried a link to it at all — an "authorised
but undiscoverable" defect the browser-driven review round proved was still
open despite an earlier (inert) fix attempt.

This is an in-process, Flask-test-client check rather than a Playwright one
deliberately: ``tests/smoke/`` runs the app in a subprocess (see
``tests/smoke/conftest.py``'s ``live_server`` fixture), so requests it serves
are invisible to ``scripts/route_verification_audit``'s pytest-plugin half,
which monkeypatches ``Flask.full_dispatch_request`` in the *test* process.
Running in-process is what makes ``interface_register.index`` show up in
``route_verification.json`` and therefore lets the ``nav-verified`` gate see
this route as both nav-reachable and test-exercised. The equivalent real
browser walk (clicking the sidebar link as each persona) lives in
``tests/smoke/test_archetype_journeys.py::
test_non_solution_architect_reaches_interface_register_from_own_sidebar``.
"""

from __future__ import annotations
import datetime

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

from app.models.user import ROLE_DATA_ARCHITECT, ROLE_SECURITY_ARCHITECT


def _make_user(db_session, make_org, *, enterprise_role: str):
    from app.models.user import User

    org = make_org("ifr-sidebar")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"ifr-sidebar-{enterprise_role}-{suffix}@example.com",
        first_name="Test",
        last_name=enterprise_role,
        organization_id=org.id,
        confirmed=True, onboarding_completed_at=datetime.datetime.utcnow(),
        enterprise_role=enterprise_role,
    )
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()
    return user


@pytest.mark.parametrize(
    "enterprise_role", [ROLE_SECURITY_ARCHITECT, ROLE_DATA_ARCHITECT]
)
def test_sidebar_offers_interface_register_link(
    app, db_session, make_org, client, login_as, enterprise_role
):
    """The rendered sidebar for these two roles must contain a real
    ``interface_register.index`` link -- not just role_access.py's own
    in-memory zone data, which is necessary but not sufficient (a template
    could still fail to render it)."""
    user = _make_user(db_session, make_org, enterprise_role=enterprise_role)
    login_as(client, user)

    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200, response.get_data(as_text=True)[:400]
    body = response.get_data(as_text=True)
    assert "/interface-register" in body, (
        f"{enterprise_role}'s rendered dashboard/sidebar carries no link to "
        "the interface register module"
    )


@pytest.mark.parametrize(
    "enterprise_role", [ROLE_SECURITY_ARCHITECT, ROLE_DATA_ARCHITECT]
)
def test_interface_register_index_reachable_and_renders(
    app, db_session, make_org, client, login_as, enterprise_role
):
    """The route the new link points at must actually admit the role (the
    module's own ``_guard()`` checks the ``data_integration`` section) and
    render without error -- proving the link is not merely present but
    functional end to end."""
    user = _make_user(db_session, make_org, enterprise_role=enterprise_role)
    login_as(client, user)

    response = client.get("/interface-register/")
    assert response.status_code == 200, response.get_data(as_text=True)[:400]
    body = response.get_data(as_text=True)
    assert "Interface" in body
    assert "Internal Server Error" not in body
    assert "Traceback" not in body

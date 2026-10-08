"""Regression guards for three confirmed HTTP 500s found by a live browser audit.

Each of these was a crash on a GET with no arguments — the plain link in the
UI — so nothing but an executed request catches them:

* ``GET /arb/dashboard``  — the ``@arb_bp.route("/dashboard")`` decorator sat
  above a block comment, so Flask bound the URL to the next ``def`` in the
  file (``_typed_actor``) rather than to ``dashboard_redirect``. The view
  returned an ``ActorContext``, and Flask raised "the view function did not
  return a valid response".
* ``GET /architecture/adrs/new`` — rendered ``architecture/adrs/form.html``,
  a template that does not exist in the tree (TemplateNotFound). The same
  dead directory was referenced by ``GET /architecture/adrs/<id>`` and
  ``GET /architecture/adrs/<id>/edit``, which 500'd identically; those need
  an existing row to reach, which is why the audit's anonymous crawl did not
  surface them.
* ``GET /architecture/elements/new`` — passed ``layer=""`` into ``url_for``
  for a route whose ``<layer>`` uses the ``any`` converter, raising
  ``ValueError: '' is not one of 'application', 'business', ...``.

The assertions are deliberately about the *response*, not about internals:
these are "does this URL serve" tests, and each fix is a redirect to a URL
that does exist.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def org(make_org):
    return make_org("routes500")


@pytest.fixture
def logged_in_client(app, db_session, org, login_as):
    """A test client authenticated as an administrator in a fresh org."""
    from app.models.user import Permission, Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()

    user = User(
        email=f"routes500-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Route",
        last_name="Audit",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    login_as(client, user)
    return client


@pytest.fixture
def decision(db_session, org):
    """A real `architecture_decisions` row owned by the client's org.

    `organization_id` is set explicitly because `TenantMixin`'s before_flush
    hook reads `g.current_org_id`, which the `db_session` fixture does not
    establish — see tests/conftest.py.
    """
    from app.models.architecture_decision import ArchitectureDecision

    row = ArchitectureDecision(
        title=f"Route audit decision {uuid.uuid4().hex[:8]}",
        status="proposed",
        organization_id=org.id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_arb_dashboard_returns_a_response_not_an_actor_context(logged_in_client):
    """/arb/dashboard must redirect to the canonical /arb/ URL."""
    resp = logged_in_client.get("/arb/dashboard")

    assert resp.status_code == 302, resp.status_code
    assert resp.headers["Location"].rstrip("/").endswith("/arb")


def test_new_adr_form_does_not_500(logged_in_client):
    """/architecture/adrs/new must reach the canonical decision form."""
    resp = logged_in_client.get("/architecture/adrs/new")

    assert resp.status_code == 302, resp.status_code
    assert "/new" in resp.headers["Location"]


def test_view_adr_does_not_500(logged_in_client, decision):
    """/architecture/adrs/<id> must reach the canonical detail page."""
    resp = logged_in_client.get(f"/architecture/adrs/{decision.id}")

    assert resp.status_code == 302, resp.status_code
    assert resp.headers["Location"].endswith(f"/architecture/decisions/{decision.id}")


def test_edit_adr_does_not_500(logged_in_client, decision):
    """/architecture/adrs/<id>/edit must reach the canonical edit form."""
    resp = logged_in_client.get(f"/architecture/adrs/{decision.id}/edit")

    assert resp.status_code == 302, resp.status_code
    assert resp.headers["Location"].endswith(
        f"/architecture/decisions/{decision.id}/edit"
    )


def test_adr_redirect_targets_actually_render(logged_in_client, decision):
    """Following the redirects must land on a real page, not another 500.

    Asserting only on the Location header would still pass if the canonical
    surface were itself broken, which is the exact failure mode being fixed.
    """
    for url in (
        "/architecture/adrs/new",
        f"/architecture/adrs/{decision.id}",
        f"/architecture/adrs/{decision.id}/edit",
    ):
        resp = logged_in_client.get(url, follow_redirects=True)
        assert resp.status_code == 200, (url, resp.status_code)


def test_new_archimate_element_without_a_layer_does_not_500(logged_in_client):
    """No layer chosen yet is a legitimate state, not a ValueError."""
    resp = logged_in_client.get("/architecture/elements/new")

    assert resp.status_code == 302, resp.status_code
    assert "dashboard" in resp.headers["Location"]


def test_new_archimate_element_with_unknown_layer_does_not_500(logged_in_client):
    """An unrecognised layer must fall back to the picker, not build a bad URL."""
    resp = logged_in_client.get("/architecture/elements/new?layer=nonsense&type=Goal")

    assert resp.status_code == 302, resp.status_code
    assert "dashboard" in resp.headers["Location"]


def test_new_archimate_element_with_a_known_pair_still_reaches_the_create_page(
    logged_in_client,
):
    """The happy path the redirect exists for must keep working."""
    resp = logged_in_client.get("/architecture/elements/new?layer=Motivation&type=Goal")

    assert resp.status_code == 302, resp.status_code
    assert resp.headers["Location"].endswith("/motivation/Goal/new")


def test_create_element_page_itself_does_not_500(logged_in_client):
    """Capgemini walkthrough (F-18-adjacent): the redirect above landed on
    /architecture/motivation/Goal/new, and THAT page 500'd —
    dashboard.html's title block reads selected_layer.title unconditionally
    and create_element() never passed selected_layer at all. The redirect
    test above only proved the Location header was right, never that the
    page it points at actually renders."""
    resp = logged_in_client.get(
        "/architecture/motivation/Goal/new", follow_redirects=True
    )
    assert resp.status_code == 200, resp.status_code


def test_edit_archimate_element_page_does_not_500(logged_in_client, db_session, org):
    """Same missing selected_layer bug, on the edit render — this is F-18's
    literal report: 'Edit returns HTTP 500'."""
    from app.models.motivation import Goal

    goal = Goal(name="Route audit goal", organization_id=org.id)
    db_session.add(goal)
    db_session.flush()

    resp = logged_in_client.get(
        f"/architecture/motivation/Goal/{goal.id}/edit", follow_redirects=True
    )
    assert resp.status_code == 200, resp.status_code

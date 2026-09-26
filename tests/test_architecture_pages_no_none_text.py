"""Regression guard for R4-2: the literal word "None" rendered as page text.

A whole-product audit found the bare word ``None`` in the rendered body of 17
routes under ``/architecture/`` — application detail/edit/new, the dashboard,
the health redirect, motivation and its four sub-pages, and seven technology
sub-pages. All 17 share one root cause: ``archimate_crud/dashboard.html``
includes ``partials/_repository_workspace.html``, which in turn includes
``partials/_motivation_swimlane.html`` (for the motivation-flavoured views)
or ``partials/_technology_lifecycle.html`` (for the technology views) —
both of which had a static ``<p>None</p>`` placeholder for an empty swim-lane
column, shown via Alpine's ``x-show`` when a column has no elements. It is
not a Python ``None`` leaking through Jinja; it is a literal string in the
template that reads exactly like one.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

# Every route the audit named, verbatim.
ARCHITECTURE_ROUTES = [
    "/architecture/application/1",
    "/architecture/application/1/1/edit",
    "/architecture/application/1/new",
    "/architecture/dashboard",
    "/architecture/health",
    "/architecture/motivation",
    "/architecture/motivation/constraints",
    "/architecture/motivation/goals",
    "/architecture/motivation/outcomes",
    "/architecture/motivation/requirements",
    "/architecture/technology/artifacts",
    "/architecture/technology/devices",
    "/architecture/technology/functions",
    "/architecture/technology/interfaces",
    "/architecture/technology/nodes",
    "/architecture/technology/processes",
    "/architecture/technology/system-software",
]

_MAIN_RE = re.compile(r"<main\b.*?</main>", re.DOTALL)
_STANDALONE_NONE_RE = re.compile(r"\bNone\b")


@pytest.fixture
def org(make_org):
    return make_org("archnonetext")


@pytest.fixture
def logged_in_client(app, db_session, org, login_as):
    """A signed-in administrator, named to avoid colliding with the word "None"."""
    from app.models.user import Permission, Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()

    user = User(
        email=f"archnonetext-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Arch",
        last_name="Auditor",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    login_as(client, user)
    return client


@pytest.mark.parametrize("path", ARCHITECTURE_ROUTES)
def test_architecture_page_main_has_no_standalone_none(logged_in_client, path):
    """Two of the 17 (the ``/application/1/...`` create and edit routes) redirect
    first — ``element_type="1"`` is not a real ArchiMate type, so the view flashes
    a warning and sends the browser back to the dashboard, which is where the
    shared partial actually rendered the text the audit saw. Following the
    redirect reaches the same page a browser would.
    """
    response = logged_in_client.get(path, follow_redirects=True)
    assert response.status_code == 200, f"{path} did not render (got {response.status_code})"

    body = response.get_data(as_text=True)
    main_match = _MAIN_RE.search(body)
    assert main_match, f"{path}: no <main> element found in the response"

    main_html = main_match.group(0)
    assert not _STANDALONE_NONE_RE.search(main_html), (
        f"{path}: the rendered <main> content still contains the standalone "
        f"word 'None'"
    )

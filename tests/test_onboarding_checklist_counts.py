"""The setup checklist reads what the workspace really holds, and links to the
real create and import actions.

It used to call a workspace "empty" because it counted only applications and
application-to-capability links -- an organisation that held ArchiMate elements
still saw "Let's get your workspace ready" -- and its second step sent the user
to /capability-map/, which has no create action, while Import sent them to the
bare applications list.

Every case runs two organisations: one that holds something and one that holds
nothing, so a count that leaks across organisations shows up as the empty
organisation losing its checklist.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import insert

pytestmark = pytest.mark.usefixtures("db_session")


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid

    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _make_user(db_session, make_org, label, enterprise_role="enterprise_architect"):
    from app.models.user import User

    org = make_org(f"setup-{label}")
    user = User(
        email=f"setup-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Setup",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    db_session.add(user)
    db_session.flush()
    return user, org


def _add_element(db_session, org):
    from app.models.archimate_core import ArchiMateElement

    db_session.execute(
        insert(ArchiMateElement.__table__).values(
            name=f"Setup element {uuid.uuid4().hex[:6]}",
            type="Capability",
            layer="Strategy",
            organization_id=org.id,
        )
    )
    db_session.flush()


def _overview(app, user):
    client = app.test_client()
    _login(client, user.id)
    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    return resp.get_data(as_text=True)


def _add_capability(db_session, org):
    """A capability of ``org`` and nothing else (its element is created beside it)."""
    from flask import g

    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability

    name = f"Setup capability {uuid.uuid4().hex[:6]}"
    element_id = db_session.execute(
        insert(ArchiMateElement.__table__).values(
            name=name, type="Capability", layer="Strategy", organization_id=org.id
        )
    ).inserted_primary_key[0]
    g.current_org_id = org.id  # the capability's write listeners read the tenant from here
    db_session.add(
        BusinessCapability(
            name=name, level=1, organization_id=org.id, archimate_element_id=element_id
        )
    )
    db_session.flush()


def test_workspace_holding_elements_is_not_called_empty(app, db_session, make_org):
    holder, holder_org = _make_user(db_session, make_org, "holder")
    empty, _empty_org = _make_user(db_session, make_org, "empty")
    _add_element(db_session, holder_org)
    _add_capability(db_session, holder_org)

    assert 'data-testid="guided-setup"' not in _overview(app, holder)
    # The other organisation holds nothing, so it still gets the checklist:
    # neither the element nor the capability above counts towards it.
    assert 'data-testid="guided-setup"' in _overview(app, empty)


def test_a_workspace_holding_only_a_capability_is_not_called_empty(app, db_session, make_org):
    """A workspace whose only content is one capability (a capability always has
    its own ArchiMate element) is not told it is empty: no application, no
    mapping, nothing else."""
    holder, holder_org = _make_user(db_session, make_org, "cap-holder")
    empty, _empty_org = _make_user(db_session, make_org, "cap-empty")
    _add_capability(db_session, holder_org)

    assert 'data-testid="guided-setup"' not in _overview(app, holder)
    assert 'data-testid="guided-setup"' in _overview(app, empty)


def test_checklist_links_to_the_real_import_and_create_actions(app, db_session, make_org):
    editor, _org = _make_user(db_session, make_org, "editor")
    html = _overview(app, editor)

    assert 'data-testid="guided-setup"' in html
    assert 'href="/applications/?import=1"' in html
    assert 'href="/enterprise/capability-map/capabilities?new=1"' in html
    assert "Add a capability" in html


def test_role_that_cannot_author_capabilities_is_not_sent_to_a_create_action(
    app, db_session, make_org
):
    viewer, _org = _make_user(
        db_session, make_org, "viewer", enterprise_role="application_manager"
    )
    html = _overview(app, viewer)

    assert 'data-testid="guided-setup"' in html
    assert "?new=1" not in html
    assert 'href="/enterprise/capability-map/capabilities"' in html
    assert "An architect or administrator adds them" in html


def test_import_link_opens_the_import_dialog_and_new_link_opens_the_create_dialog(
    app, db_session, make_org
):
    """The deep links the checklist uses are honoured by the pages they target."""
    editor, _org = _make_user(db_session, make_org, "deeplink")
    client = app.test_client()
    _login(client, editor.id)

    applications = client.get("/applications/?import=1").get_data(as_text=True)
    assert "get('import') !== '1'" in applications
    assert 'data-testid="btn-import"' in applications

    capabilities = client.get("/enterprise/capability-map/capabilities?new=1").get_data(as_text=True)
    assert "get('new') === '1'" in capabilities
    assert 'id="cap-dialog"' in capabilities

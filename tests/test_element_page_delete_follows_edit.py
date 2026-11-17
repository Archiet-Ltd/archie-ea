"""An element's page offers Delete only to a person who is offered Edit."""
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _viewer(db_session, org):
    from app.models.user import Role, User

    Role.insert_roles()
    viewer = User(
        email="viewer-%s@example.com" % uuid.uuid4().hex[:8], first_name="View", last_name="Er",
        organization_id=org.id, confirmed=True, enterprise_role="viewer",
    )
    viewer.role = Role.query.filter_by(name="Viewer").one()
    db_session.add(viewer)
    db_session.flush()
    return viewer


def _goal(db_session, org):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(organization_id=org.id, name="Goal " + uuid.uuid4().hex[:6], type="Goal", layer="Motivation")
    db_session.add(element)
    db_session.flush()
    return element


def test_a_read_only_account_is_shown_neither_edit_nor_delete_and_an_editor_is_shown_both(
        db_session, make_org, client, login_as):
    from tests.test_work_package_writer import _user

    org, other = make_org("delete-follows"), make_org("delete-follows-other")
    element = _goal(db_session, org)
    _goal(db_session, other)
    url = "/architecture/motivation/Goal/%d?ae=1" % element.id

    login_as(client, _viewer(db_session, org))
    page = client.get(url).get_data(as_text=True)
    assert element.name in page
    assert 'data-testid="delete-element"' not in page
    assert 'id="edit-element-reason"' in page

    login_as(client, _user(db_session, org, "deletefollows", role="enterprise_architect"))
    page = client.get(url).get_data(as_text=True)
    assert element.name in page
    assert 'data-testid="delete-element"' in page
    assert 'id="edit-element-reason"' not in page

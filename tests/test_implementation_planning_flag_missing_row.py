"""A database with no implementation planning flag row has the module on.

Gap analysis and the work packages list link straight to /implementation/...
pages. The module guard used to answer 404 for every one of them when the
``architecture_implementation_planning`` row was missing (a fresh database, or
production, which has seven flag rows and none for this module). A missing row
now means enabled, as for every other feature flag; a row that an administrator
has switched off still answers 404.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

FLAG = "architecture_implementation_planning"


def _architect(db_session, org):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"wpflag-{uuid.uuid4().hex[:8]}@example.com", first_name="Flag", last_name="Tester",
        organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect",
        role=Role.query.filter_by(name="Architect").first(),
    )
    user.password = "Test!12345"
    db_session.add(user)
    db_session.flush()
    return user


def _set_flag(db_session, row):
    """No row at all (None), or a row switched on or off."""
    from app.models.feature_flags import FeatureFlag, FeatureState

    FeatureFlag.query.filter_by(key=FLAG).delete()
    if row is not None:
        db_session.add(FeatureFlag(
            key=FLAG, name="Implementation planning", enabled=row, state=FeatureState.STABLE))
    db_session.flush()
    FeatureFlag.clear_cache(FLAG)


@pytest.fixture
def world(db_session, make_org):
    from app.services import work_package_service

    org = make_org("wpflag")
    user = _architect(db_session, org)
    wp = work_package_service.create_work_package(
        organization_id=org.id, name=f"WP {uuid.uuid4().hex[:6]}")
    db_session.flush()
    yield {"user": user, "wp": wp}
    from app.models.feature_flags import FeatureFlag

    FeatureFlag.clear_cache(FLAG)


@pytest.mark.parametrize("path", [
    "/implementation/",
    "/implementation/work-packages",
    "/implementation/work-packages/{wp}",
    "/implementation/gaps",
    "/implementation/deliverables",
    "/implementation/plateaus",
])
def test_pages_open_when_the_flag_row_is_missing(client, login_as, db_session, world, path):
    _set_flag(db_session, None)
    login_as(client, world["user"])
    response = client.get(path.format(wp=world["wp"].id), follow_redirects=True)
    assert response.status_code == 200, (path, response.status_code)


def test_a_work_package_page_shows_its_name_when_the_flag_row_is_missing(
        client, login_as, db_session, world):
    _set_flag(db_session, None)
    login_as(client, world["user"])
    response = client.get(f"/implementation/work-packages/{world['wp'].id}")
    assert response.status_code == 200
    assert world["wp"].name in response.get_data(as_text=True)


def test_a_row_switched_off_still_answers_404(client, login_as, db_session, world):
    _set_flag(db_session, False)
    login_as(client, world["user"])
    assert client.get(f"/implementation/work-packages/{world['wp'].id}").status_code == 404
    assert client.get("/implementation/").status_code == 404


def test_a_row_switched_on_opens_the_page(client, login_as, db_session, world):
    _set_flag(db_session, True)
    login_as(client, world["user"])
    assert client.get(f"/implementation/work-packages/{world['wp'].id}").status_code == 200

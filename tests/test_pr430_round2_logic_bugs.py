"""Round 2 of the PR 430 fix: the two logic bugs the lead review named
alongside the missing-decorator findings (these are not fixed by the
``platform_admin_required``/structural-guard additions on their own).
"""

from __future__ import annotations

import uuid

import pytest
from werkzeug.exceptions import Forbidden


def _user_with_role(db_session, make_org, role_name, *, platform=False):
    from app.models import Role
    from app.models.user import User

    org = make_org("logic-bug")
    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        pytest.skip(f"no {role_name!r} role seeded in this database")
    user = User(
        email=f"logic-bug-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Logic",
        last_name="Bug",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = uuid.uuid4().hex
    user.is_org_admin = role_name == "Administrator"
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    db_session.commit()
    return user


def test_require_admin_now_calls_is_admin_instead_of_reading_the_bound_method(
    app, db_session, make_org
):
    """``User.is_admin`` is a plain method, not a @property (app/models/user.py).

    ``_require_admin()`` used to test ``current_user.is_admin`` -- the bound
    method object, always truthy -- instead of calling it. That made the
    function a complete no-op for every signed-in user, admin or not. Fixed
    by calling it: ``current_user.is_admin()``.
    """
    from app.modules.ai_chat.routes.chat_admin_routes import _require_admin

    # "Architect" is the default self-registration role: Permission.GENERAL
    # only, not Permission.ADMINISTER (app/models/user.py's own
    # insert_roles() comment), and its Role.name is "Architect", so neither
    # half of _require_admin's check (is_admin() nor the role-name fallback)
    # can pass for this user.
    non_admin = _user_with_role(db_session, make_org, "Architect", platform=False)
    assert non_admin.is_admin() is False, (
        "fixture setup: this user must genuinely fail is_admin() for the "
        "assertion below to mean anything"
    )

    with app.test_request_context("/"):
        from flask_login import login_user

        login_user(non_admin)
        with pytest.raises(Forbidden):
            _require_admin()


def test_require_admin_still_admits_a_real_admin(app, db_session, make_org):
    from app.modules.ai_chat.routes.chat_admin_routes import _require_admin
    from app.models import Permission

    admin = _user_with_role(db_session, make_org, "Administrator", platform=True)
    assert admin.can(Permission.ADMINISTER)

    with app.test_request_context("/"):
        from flask_login import login_user

        login_user(admin)
        _require_admin()  # must not raise


def test_confidence_threshold_created_by_id_comes_from_current_user_not_the_body(
    app, db_session, make_org, client, login_as
):
    """A POST body claiming ``user_id`` as someone else must not be trusted.

    Before the fix, ``api_save_thresholds`` read ``created_by_id=data.get
    ("user_id")`` straight from the request body -- any caller could claim
    the row was created by any user id, including one in a different
    organisation.
    """
    from app.models.confidence_review import ConfidenceThreshold
    from app.models.user import User

    platform_admin = _user_with_role(db_session, make_org, "Administrator", platform=True)
    impersonated_id = platform_admin.id + 999999  # does not exist

    login_as(client, platform_admin)
    unique_name = f"probe-threshold-{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/reviews/api/thresholds",
        json={
            "threshold_name": unique_name,
            "user_id": impersonated_id,
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)

    db_session.expunge_all()
    row = ConfidenceThreshold.query.filter_by(threshold_name=unique_name).first()
    assert row is not None
    assert row.created_by_id == platform_admin.id
    assert row.created_by_id != impersonated_id

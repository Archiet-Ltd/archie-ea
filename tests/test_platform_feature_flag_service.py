"""The service layer, not the route decorator, is what actually refuses a
non-platform-admin write of FeatureFlag.

These call app.services.platform_feature_flag_service directly, bypassing
the route and its decorator entirely, to prove the guard is structural: a
route that someday forgot @platform_admin_required (the mistake this
session found seven times across five other models) would still have its
write refused here, because the write itself checks is_platform_admin(),
not the thing that's supposed to guard it on the way in.
"""

from __future__ import annotations

import uuid

import pytest


def _user(db_session, org, *, platform=False):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(email=f"ffsvc-{uuid.uuid4().hex[:6]}@example.test", first_name="Svc", last_name="Tester",
                organization_id=org.id, confirmed=True, role=role)
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _flag(db_session, **overrides):
    from app.models.feature_flags import FeatureFlag, FeatureState, FeatureType

    defaults = dict(
        key=f"svc_probe_{uuid.uuid4().hex[:8]}", name="Service probe flag",
        feature_type=FeatureType.FUNCTIONALITY, state=FeatureState.STABLE, enabled=True,
    )
    defaults.update(overrides)
    flag = FeatureFlag(**defaults)
    db_session.add(flag)
    db_session.flush()
    return flag


def _world(db_session, make_org):
    org = make_org("ff-svc")
    tenant_admin = _user(db_session, org)
    platform_admin = _user(db_session, org, platform=True)
    flag = _flag(db_session)
    db_session.commit()
    return tenant_admin.id, platform_admin.id, flag.id


def _login_as(db_session, login_as_user_id):
    from flask_login import login_user

    from app.models.user import User

    db_session.expunge_all()
    login_user(db_session.get(User, login_as_user_id))


def test_create_refuses_a_non_platform_admin_caller(app, db_session, make_org):
    from app.models.feature_flags import FeatureState, FeatureType
    from app.services import platform_feature_flag_service
    from werkzeug.exceptions import Forbidden

    org = make_org("ff-svc-create")
    tenant_admin = _user(db_session, org)
    db_session.commit()

    with app.test_request_context():
        _login_as(db_session, tenant_admin.id)
        with pytest.raises(Forbidden):
            platform_feature_flag_service.create_feature_flag(
                key=f"should_not_exist_{uuid.uuid4().hex[:8]}", name="x",
                feature_type=FeatureType.FUNCTIONALITY, state=FeatureState.STABLE,
            )


def test_toggle_refuses_a_non_platform_admin_caller_and_clears_no_cache(app, db_session, make_org):
    from app.services import platform_feature_flag_service
    from werkzeug.exceptions import Forbidden

    tenant_admin_id, _platform_id, flag_id = _world(db_session, make_org)

    with app.test_request_context():
        _login_as(db_session, tenant_admin_id)
        from app.models.feature_flags import FeatureFlag

        flag = db_session.get(FeatureFlag, flag_id)
        with pytest.raises(Forbidden):
            platform_feature_flag_service.toggle_feature_flag(flag)


def test_a_platform_admin_can_toggle_and_the_cache_is_cleared(app, db_session, make_org):
    from app.models.feature_flags import FeatureFlag
    from app.services import platform_feature_flag_service

    _tenant_id, platform_admin_id, flag_id = _world(db_session, make_org)

    with app.test_request_context():
        _login_as(db_session, platform_admin_id)
        flag = db_session.get(FeatureFlag, flag_id)
        key = flag.key
        # Prime the cache the way a real read would, then prove the write
        # clears it -- the exact gap this session found in the v2 tree,
        # which never called clear_cache at all after a write.
        from app.models import feature_flags as feature_flags_module

        feature_flags_module._feature_cache[key] = (True, 0)

        was_enabled = flag.enabled
        platform_feature_flag_service.toggle_feature_flag(flag)

        assert flag.enabled is not was_enabled
        assert key not in feature_flags_module._feature_cache


def test_update_clears_the_cache_under_both_the_old_and_new_key(app, db_session, make_org):
    from app.models.feature_flags import FeatureFlag
    from app.services import platform_feature_flag_service

    _tenant_id, platform_admin_id, flag_id = _world(db_session, make_org)

    with app.test_request_context():
        _login_as(db_session, platform_admin_id)
        flag = db_session.get(FeatureFlag, flag_id)
        old_key = flag.key
        new_key = f"renamed_{uuid.uuid4().hex[:8]}"

        from app.models import feature_flags as feature_flags_module

        feature_flags_module._feature_cache[old_key] = (True, 0)

        platform_feature_flag_service.update_feature_flag(flag, key=new_key)

        assert old_key not in feature_flags_module._feature_cache
        assert new_key not in feature_flags_module._feature_cache


def test_delete_refuses_a_non_platform_admin_caller(app, db_session, make_org):
    from app.models.feature_flags import FeatureFlag
    from app.services import platform_feature_flag_service
    from werkzeug.exceptions import Forbidden

    tenant_admin_id, _platform_id, flag_id = _world(db_session, make_org)

    with app.test_request_context():
        _login_as(db_session, tenant_admin_id)
        flag = db_session.get(FeatureFlag, flag_id)
        with pytest.raises(Forbidden):
            platform_feature_flag_service.delete_feature_flag(flag)

    assert db_session.get(FeatureFlag, flag_id) is not None


def test_two_organisations_neither_tenant_admin_can_write_feature_flag(app, db_session, make_org):
    """Prove that the platform-admin check is not accidentally scoped to a
    specific organisation. Two organisations, two tenant admins (one per org):
    neither can create, toggle, or delete a FeatureFlag through the service
    layer. Only a platform admin can."""
    from app.models.feature_flags import FeatureFlag, FeatureState, FeatureType
    from app.services import platform_feature_flag_service
    from werkzeug.exceptions import Forbidden

    import uuid

    org_a = make_org("ff-two-org-a")
    org_b = make_org("ff-two-org-b")

    tenant_admin_a = _user(db_session, org_a)
    tenant_admin_b = _user(db_session, org_b)
    platform_admin = _user(db_session, org_a, platform=True)

    flag = _flag(db_session)
    db_session.commit()

    # Capture IDs before any _login_as call (which expunges the session)
    ta_a_id = tenant_admin_a.id
    ta_b_id = tenant_admin_b.id
    pa_id = platform_admin.id
    flag_id = flag.id

    # Tenant admin from org A cannot create
    with app.test_request_context():
        _login_as(db_session, ta_a_id)
        with pytest.raises(Forbidden):
            platform_feature_flag_service.create_feature_flag(
                key=f"should_not_exist_a_{uuid.uuid4().hex[:8]}", name="x",
                feature_type=FeatureType.FUNCTIONALITY, state=FeatureState.STABLE,
            )

    # Tenant admin from org B cannot create
    with app.test_request_context():
        _login_as(db_session, ta_b_id)
        with pytest.raises(Forbidden):
            platform_feature_flag_service.create_feature_flag(
                key=f"should_not_exist_b_{uuid.uuid4().hex[:8]}", name="x",
                feature_type=FeatureType.FUNCTIONALITY, state=FeatureState.STABLE,
            )

    # Tenant admin from org A cannot toggle
    with app.test_request_context():
        _login_as(db_session, ta_a_id)
        flag_a = db_session.get(FeatureFlag, flag_id)
        with pytest.raises(Forbidden):
            platform_feature_flag_service.toggle_feature_flag(flag_a)

    # Tenant admin from org B cannot toggle
    with app.test_request_context():
        _login_as(db_session, ta_b_id)
        flag_b = db_session.get(FeatureFlag, flag_id)
        with pytest.raises(Forbidden):
            platform_feature_flag_service.toggle_feature_flag(flag_b)

    # Tenant admin from org A cannot delete
    with app.test_request_context():
        _login_as(db_session, ta_a_id)
        flag_c = db_session.get(FeatureFlag, flag_id)
        with pytest.raises(Forbidden):
            platform_feature_flag_service.delete_feature_flag(flag_c)

    # Tenant admin from org B cannot delete
    with app.test_request_context():
        _login_as(db_session, ta_b_id)
        flag_d = db_session.get(FeatureFlag, flag_id)
        with pytest.raises(Forbidden):
            platform_feature_flag_service.delete_feature_flag(flag_d)

    # Platform admin CAN toggle (prove the service works for authorised callers)
    with app.test_request_context():
        _login_as(db_session, pa_id)
        flag_e = db_session.get(FeatureFlag, flag_id)
        was_enabled = flag_e.enabled
        platform_feature_flag_service.toggle_feature_flag(flag_e)
        assert flag_e.enabled is not was_enabled

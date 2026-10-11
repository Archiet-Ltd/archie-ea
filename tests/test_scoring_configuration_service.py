"""The service layer, not the route decorator, is what actually refuses a
non-platform-admin write of ScoringConfiguration.

These call app.services.scoring_configuration_service directly, bypassing
the route and its decorator entirely, to prove the guard is structural: any
one of the three parallel route-tree copies that someday forgot
@platform_admin_required would still have its write refused here.
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
    user = User(email=f"scsvc-{uuid.uuid4().hex[:6]}@example.test", first_name="Svc", last_name="Tester",
                organization_id=org.id, confirmed=True, role=role)
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _config(db_session, **overrides):
    from app.models.application_rationalization import ScoringConfiguration

    defaults = dict(
        name="Service probe config", technical_health_weight=30, business_value_weight=35,
        cost_efficiency_weight=25, vendor_risk_weight=10, is_default=False, is_active=True,
        configuration_version=1,
    )
    defaults.update(overrides)
    config = ScoringConfiguration(**defaults)
    db_session.add(config)
    db_session.flush()
    return config


def _world(db_session, make_org):
    # Two organisations: the platform admin sits in org A (the configuration's
    # creator), the refused tenant admin sits in org B -- so a pass here cannot
    # be explained by same-org membership, only by the is_platform_admin gate.
    org_a = make_org("sc-svc-a")
    org_b = make_org("sc-svc-b")
    tenant_admin = _user(db_session, org_b)
    platform_admin = _user(db_session, org_a, platform=True)
    config = _config(db_session)
    db_session.commit()
    return tenant_admin.id, platform_admin.id, config.id


def _login_as(db_session, user_id):
    from flask_login import login_user

    from app.models.user import User

    db_session.expunge_all()
    login_user(db_session.get(User, user_id))


_VALID_DATA = {
    "name": "New config", "technical_health_weight": 30, "business_value_weight": 35,
    "cost_efficiency_weight": 25, "vendor_risk_weight": 10,
}


def test_create_refuses_a_non_platform_admin_caller(app, db_session, make_org):
    from app.services import scoring_configuration_service
    from werkzeug.exceptions import Forbidden

    org = make_org("sc-svc-create")
    tenant_admin = _user(db_session, org)
    db_session.commit()

    with app.test_request_context():
        _login_as(db_session, tenant_admin.id)
        with pytest.raises(Forbidden):
            scoring_configuration_service.create_scoring_configuration(_VALID_DATA)


def test_create_rejects_weights_that_do_not_sum_to_100(app, db_session, make_org):
    from app.services import scoring_configuration_service

    _tenant_id, platform_admin_id, _config_id = _world(db_session, make_org)

    with app.test_request_context():
        _login_as(db_session, platform_admin_id)
        with pytest.raises(scoring_configuration_service.ScoringConfigurationError) as exc_info:
            scoring_configuration_service.create_scoring_configuration(
                {**_VALID_DATA, "technical_health_weight": 50}
            )
        assert exc_info.value.status_code == 400


def test_create_as_default_unsets_the_prior_default(app, db_session, make_org):
    from app.models.application_rationalization import ScoringConfiguration
    from app.services import scoring_configuration_service

    _tenant_id, platform_admin_id, _config_id = _world(db_session, make_org)

    with app.test_request_context():
        _login_as(db_session, platform_admin_id)
        first = scoring_configuration_service.create_scoring_configuration(
            {**_VALID_DATA, "is_default": True}
        )
        second = scoring_configuration_service.create_scoring_configuration(
            {**_VALID_DATA, "is_default": True}
        )
        first_id, second_id = first.id, second.id

        db_session.expunge_all()
        assert db_session.get(ScoringConfiguration, first_id).is_default is False
        assert db_session.get(ScoringConfiguration, second_id).is_default is True


def test_update_refuses_a_non_platform_admin_caller(app, db_session, make_org):
    from app.services import scoring_configuration_service
    from werkzeug.exceptions import Forbidden

    tenant_admin_id, _platform_id, config_id = _world(db_session, make_org)

    with app.test_request_context():
        _login_as(db_session, tenant_admin_id)
        with pytest.raises(Forbidden):
            scoring_configuration_service.update_scoring_configuration(config_id, {"name": "x"})


def test_update_on_a_missing_config_raises_404(app, db_session, make_org):
    from app.services import scoring_configuration_service

    _tenant_id, platform_admin_id, _config_id = _world(db_session, make_org)

    with app.test_request_context():
        _login_as(db_session, platform_admin_id)
        with pytest.raises(scoring_configuration_service.ScoringConfigurationError) as exc_info:
            scoring_configuration_service.update_scoring_configuration(999999, {"name": "x"})
        assert exc_info.value.status_code == 404


def test_delete_refuses_a_non_platform_admin_caller(app, db_session, make_org):
    from app.models.application_rationalization import ScoringConfiguration
    from app.services import scoring_configuration_service
    from werkzeug.exceptions import Forbidden

    tenant_admin_id, _platform_id, config_id = _world(db_session, make_org)

    with app.test_request_context():
        _login_as(db_session, tenant_admin_id)
        with pytest.raises(Forbidden):
            scoring_configuration_service.delete_scoring_configuration(config_id)

    assert db_session.get(ScoringConfiguration, config_id).is_active is True


def test_delete_refuses_to_remove_the_default_configuration(app, db_session, make_org):
    from app.services import scoring_configuration_service

    _tenant_id, platform_admin_id, _config_id = _world(db_session, make_org)

    with app.test_request_context():
        _login_as(db_session, platform_admin_id)
        default_config = _config(db_session, name="Default", is_default=True)
        db_session.commit()

        with pytest.raises(scoring_configuration_service.ScoringConfigurationError) as exc_info:
            scoring_configuration_service.delete_scoring_configuration(default_config.id)
        assert exc_info.value.status_code == 400

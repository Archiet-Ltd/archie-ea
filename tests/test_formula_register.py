"""The rationalisation formula register.

Every rationalisation score records the scoring configuration and the formula
version that produced it. The configuration is the one governed store of the
weights: an organisation's own configuration is private to it, a shared row
(``organization_id IS NULL``) is readable by every organisation and writable by
none of them, and when no configuration resolves there is no overall score
rather than a default weighting.
"""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _config(session, **overrides):
    from app.models.application_rationalization import ScoringConfiguration

    values = dict(
        name=f"Formula {uuid.uuid4().hex[:8]}",
        scope_type="global",
        technical_health_weight=40,
        business_value_weight=30,
        cost_efficiency_weight=20,
        vendor_risk_weight=10,
        is_active=True,
    )
    values.update(overrides)
    config = ScoringConfiguration(**values)
    session.add(config)
    session.flush()
    return config


def _application(session, org):
    from app.models.application_layer import ApplicationComponent

    app_row = ApplicationComponent(
        organization_id=org.id,
        name=f"Scored application {uuid.uuid4().hex[:8]}",
        total_cost_of_ownership=120000.0,
        lifecycle_status="active",
        business_criticality="high",
    )
    session.add(app_row)
    session.flush()
    return app_row


def test_score_records_configuration_and_formula_version(db_session, make_org, tenant_ctx):
    from app.services.rationalization_scoring_service import RationalizationScoringService

    org = make_org("formula")
    with tenant_ctx(org.id):
        config = _config(db_session, is_default=True)
        assert config.organization_id == org.id
        assert config.formula_version == 1
        app_row = _application(db_session, org)

        score = RationalizationScoringService.calculate_app_score(app_row.id, app_row)

        assert score is not None
        assert score.scoring_configuration_id == config.id
        assert score.formula_version == 1


def test_organisation_configuration_is_invisible_to_another_organisation(
    db_session, make_org, tenant_ctx
):
    from app.models.application_rationalization import ScoringConfiguration
    from app.services.rationalization_scoring_service import RationalizationScoringService

    org_a = make_org("owner")
    org_b = make_org("other")
    with tenant_ctx(org_a.id):
        own = _config(db_session, is_default=True)
        own_id = own.id
    db_session.expunge_all()

    with tenant_ctx(org_b.id):
        assert ScoringConfiguration.query.filter_by(id=own_id).first() is None
        resolved = RationalizationScoringService.get_scoring_configuration()
        assert resolved is None or resolved.id != own_id


def test_shared_configuration_is_readable_by_all_and_writable_by_none(
    db_session, make_org, tenant_ctx
):
    from app.models.application_rationalization import ScoringConfiguration

    shared = _config(db_session)
    assert shared.organization_id is None
    shared_id = shared.id
    org_a = make_org("reader-a")
    org_b = make_org("reader-b")
    db_session.expunge_all()

    for org in (org_a, org_b):
        with tenant_ctx(org.id):
            assert ScoringConfiguration.query.filter_by(id=shared_id).first() is not None
        db_session.expunge_all()

    with tenant_ctx(org_a.id):
        changed = ScoringConfiguration.query.filter(
            ScoringConfiguration.id == shared_id
        ).update({"technical_health_weight": 70}, synchronize_session=False)
        assert changed == 0

        row = ScoringConfiguration.query.filter_by(id=shared_id).first()
        row.technical_health_weight = 50
        row.business_value_weight = 20
        with pytest.raises(PermissionError):
            db_session.flush()
    db_session.rollback()


def test_no_resolvable_configuration_gives_no_overall_score(monkeypatch):
    from app.models.application_rationalization import ApplicationRationalizationScore
    from app.services.rationalization_scoring_service import RationalizationScoringService

    score = ApplicationRationalizationScore(
        technical_health_score=60,
        business_value_score=70,
        cost_efficiency_score=50,
        vendor_risk_score=80,
    )
    assert score.calculate_overall_score() is None

    monkeypatch.setattr(
        RationalizationScoringService,
        "get_scoring_configuration",
        staticmethod(lambda *args, **kwargs: None),
    )
    metadata = RationalizationScoringService.get_model_metadata()
    assert metadata["weights"] is None
    assert metadata["formula"] is None


def test_weight_change_creates_next_version_and_old_score_keeps_its_version(
    db_session, make_org, tenant_ctx
):
    from app.services.rationalization_scoring_service import RationalizationScoringService

    org = make_org("versioned")
    with tenant_ctx(org.id):
        config = _config(db_session, is_default=True)
        app_row = _application(db_session, org)
        score = RationalizationScoringService.calculate_app_score(app_row.id, app_row)
        assert score is not None and score.formula_version == 1

        config.technical_health_weight = 25
        config.business_value_weight = 45
        db_session.flush()

        assert config.formula_version == 2
        db_session.refresh(score)
        assert score.formula_version == 1
        assert score.scoring_configuration_id == config.id

        # The overall score re-derives from the resolved configuration's weights.
        expected = round(
            score.technical_health_score * 0.25
            + score.business_value_score * 0.45
            + score.cost_efficiency_score * 0.20
            + score.vendor_risk_score * 0.10
        )
        assert score.calculate_overall_score() == expected


def test_scoring_outside_a_request_uses_the_application_organisation(
    db_session, make_org, tenant_ctx
):
    from app.services.rationalization_scoring_service import RationalizationScoringService

    # A shared default exists too; the organisation's own default must win.
    _config(db_session, is_default=True)
    org = make_org("batch")
    with tenant_ctx(org.id):
        own = _config(db_session, is_default=True)
        app_row = _application(db_session, org)
        own_id, own_version = own.id, own.formula_version
    assert own.organization_id == org.id

    # No request context here, as in a CLI or scheduled scoring run.
    score = RationalizationScoringService.calculate_app_score(app_row.id, app_row)

    assert score is not None
    assert score.scoring_configuration_id == own_id
    assert score.formula_version == own_version


def test_scoring_route_answers_409_when_no_formula_is_registered(
    db_session, make_org, client, login_as
):
    from app.models.application_rationalization import (
        ApplicationRationalizationScore,
        ScoringConfiguration,
    )
    from app.models.user import Permission, Role, User

    # Withdraw every shared formula inside this rolled-back transaction.
    ScoringConfiguration.query.filter(ScoringConfiguration.organization_id.is_(None)).update(
        {"is_active": False}, synchronize_session=False
    )
    org = make_org("unformulated")
    app_row = _application(db_session, org)
    role = Role.query.filter_by(name="Formula Register Writer").first()
    if role is None:
        role = Role(
            name="Formula Register Writer",
            permissions=Permission.GENERAL,
            index="main",
            default=False,
        )
        db_session.add(role)
        db_session.flush()
    user = User(
        role=role,
        email=f"formula-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Formula",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="portfolio_manager",
    )
    db_session.add(user)
    db_session.flush()

    login_as(client, user)
    response = client.post(f"/applications/rationalization/api/score/{app_row.id}")

    assert response.status_code == 409, response.get_data(as_text=True)[:300]
    assert response.get_json()["error"] == (
        "No scoring formula is registered for this organisation"
    )
    assert (
        ApplicationRationalizationScore.query.filter_by(
            application_component_id=app_row.id
        ).first()
        is None
    )

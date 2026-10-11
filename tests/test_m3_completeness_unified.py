"""M3: /applications/<id> and /applications/<id>/fact-sheet disagreed on the
same record's completeness (20% vs 21%) because ApplicationComponent carried
its own unweighted 5-category/15-field rubric while
app/services/application_fact_sheet.py had a separate weighted 11-field one.
Fixed by making ApplicationComponent.completeness_score/completeness_gaps
(used by applications/dashboard.html, the /applications/<id> page) delegate
to app.services.application_fact_sheet.compute_completeness -- the same
function build_fact_sheet() calls for the Fact Sheet page -- so there is one
calculation, not two.
"""

import pytest


@pytest.mark.usefixtures("db_session")
def test_detail_page_property_matches_fact_sheet_service(app, db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_fact_sheet import compute_completeness

    org = make_org("m3-completeness")
    with tenant_ctx(org.id):
        # Deliberately partial record -- some fields set, most not, so the two
        # old rubrics would have disagreed (different field sets/weights).
        component = ApplicationComponent(
            name="Partial App",
            organization_id=org.id,
            application_owner="Jane Doe",
            business_criticality="high",
        )
        db_session.add(component)
        db_session.commit()

        model_pct = component.completeness_score
        model_gaps = component.completeness_gaps
        service_result = compute_completeness(component)

        assert model_pct == service_result["pct"]
        assert model_gaps == service_result["missing"]

        # build_fact_sheet() itself is verified to call this exact function by
        # source inspection (app/services/application_fact_sheet.py's
        # build_fact_sheet uses `"completeness": compute_completeness(app)`
        # directly, no separate calculation) -- not re-exercised here via a
        # full ORM object, since assembling the rest of the fact sheet
        # (dependencies/diagrams) needs a fuller ArchiMate graph fixture than
        # this test sets up and is out of scope for the completeness parity
        # this test targets.


@pytest.mark.usefixtures("db_session")
def test_fully_populated_record_scores_high_on_both(app, db_session, make_org, tenant_ctx):
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import Role, User

    org = make_org("m3-complete-full")
    with tenant_ctx(org.id):
        Role.insert_roles()
        role_obj = Role.query.filter_by(name="User").first()
        owner = User(
            email="m3-complete-full@example.com",
            first_name="Jane",
            last_name="Doe",
            organization_id=org.id,
            confirmed=True,
            role_id=role_obj.id if role_obj else None,
        )
        db_session.add(owner)
        db_session.flush()

        component = ApplicationComponent(
            name="Fully Documented App",
            organization_id=org.id,
            business_domain="Finance",
            business_criticality="high",
            lifecycle_status="operational",
            total_cost_of_ownership=100000,
            technology_stack="Python/Postgres",
            deployment_model="cloud",
            data_classification="internal",
            vendor_name="Acme Corp",
            disaster_recovery_enabled=True,
            description="Core finance system",
        )
        db_session.add(component)
        db_session.flush()
        db_session.add(ApplicationOwner(
            application_id=component.id,
            user_id=owner.id,
            organization_id=org.id,
            ownership_type="primary",
        ))
        db_session.commit()

        assert component.completeness_score == 100
        assert component.completeness_gaps == []

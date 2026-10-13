"""Onboarding writes tools as application components with support links to real capabilities."""
import pytest

from app.modules.onboarding.services import capabilities as caps
from app.modules.onboarding.services import tools

pytestmark = pytest.mark.usefixtures("db_session")

STAGE, BAND = "early_revenue", "micro"


def _with_capability(key="customer_acquisition"):
    caps.save([{"key": key, "maturity": 2}], stage=STAGE, size_band=BAND)


def _mappings(org_id):
    """Support links for this organisation's tools. The mapping table has no
    tenant column and the browser journeys commit real rows to the shared test
    database, so a bare Mapping.query would count other runs' rows."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as Mapping

    ids = [a.id for a in ApplicationComponent.query.filter_by(organization_id=org_id).all()]
    return Mapping.query.filter(Mapping.application_component_id.in_(ids)).all() if ids else []


def _tool(name="Stripe", **overrides):
    entry = {"name": name, "deployment": "saas", "supports": ["customer_acquisition"]}
    entry.update(overrides)
    return entry


def test_a_tool_becomes_an_application_component_with_an_archimate_element(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    org = make_org("tool")
    with tenant_ctx(org.id):
        _with_capability()
        result = tools.save([_tool()], stage=STAGE, size_band=BAND)
        app = ApplicationComponent.query.filter_by(name="Stripe").one()
        element = db_session.get(ArchiMateElement, app.archimate_element_id)

    assert result == {"tools": 1, "links": 1}
    assert app.organization_id == org.id and app.deployment_model == "saas"
    assert element.type == "ApplicationComponent" and element.organization_id == org.id


def test_the_support_link_is_a_real_mapping_row_and_claims_no_coverage(db_session, make_org, tenant_ctx):
    from app.models.unified_capability import UnifiedCapability

    org = make_org("link")
    with tenant_ctx(org.id):
        _with_capability()
        tools.save([_tool()], stage=STAGE, size_band=BAND)
        (row,) = _mappings(org.id)
        capability = db_session.get(UnifiedCapability, row.unified_capability_id)

    assert capability.name == "Customer acquisition" and capability.organization_id == org.id
    assert row.relationship_type == "supports"
    assert row.coverage_percentage in (0, None) and row.gap_status == "unknown", "onboarding does not assert how well it is covered"


def test_saving_twice_updates_and_an_unticked_capability_removes_only_the_link(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("twice")
    with tenant_ctx(org.id):
        _with_capability()
        tools.save([_tool()], stage=STAGE, size_band=BAND)
        tools.save([_tool(supports=[])], stage=STAGE, size_band=BAND)
        assert ApplicationComponent.query.filter_by(name="Stripe").count() == 1
        assert _mappings(org.id) == []


def test_a_capability_not_recorded_yet_is_not_linked(db_session, make_org, tenant_ctx):

    org = make_org("nocap")
    with tenant_ctx(org.id):
        result = tools.save([_tool()], stage=STAGE, size_band=BAND)
        assert _mappings(org.id) == []

    assert result == {"tools": 1, "links": 0}


def test_unnamed_entries_and_bad_deployments_are_ignored(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("bad")
    with tenant_ctx(org.id):
        result = tools.save([{"name": "  "}, _tool("Sheets", deployment="bogus")], stage=STAGE, size_band=BAND)
        assert ApplicationComponent.query.filter_by(name="Sheets").one().deployment_model is None

    assert result["tools"] == 1


def test_two_organisations_never_share_a_tool_of_the_same_name(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent

    a, b = make_org("a"), make_org("b")
    with tenant_ctx(a.id):
        _with_capability()
        tools.save([_tool()], stage=STAGE, size_band=BAND)
        a_id = ApplicationComponent.query.filter_by(name="Stripe").one().id
    with tenant_ctx(b.id):
        _with_capability()
        tools.save([_tool(deployment="cloud")], stage=STAGE, size_band=BAND)
        view = tools.read(STAGE, BAND, b.id)
        b_id = ApplicationComponent.query.filter_by(name="Stripe").one().id

    assert a_id != b_id
    assert [t["deployment"] for t in view["tools"]] == ["cloud"]
    assert view["tools"][0]["supports"] == ["customer_acquisition"]


def test_read_offers_stage_suggestions_and_only_recorded_capabilities(db_session, make_org, tenant_ctx):
    org = make_org("read")
    with tenant_ctx(org.id):
        _with_capability()
        view = tools.read(STAGE, BAND, org.id)

    assert [c["key"] for c in view["capabilities"]] == ["customer_acquisition"]
    assert "Billing or invoicing tool" in view["suggestions"] or view["suggestions"], "expected systems come from the stage baseline"

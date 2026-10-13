"""Onboarding writes capabilities to the real capability table, and only what was answered.

The point of these tests is the wiring: an answer given at onboarding must be
visible to everything else that reads capabilities (the unified table and its
ArchiMate element mirror), scoped to the organisation, without any copy kept by
onboarding itself.
"""
import pytest

from app.modules.onboarding.services import capabilities as capture

pytestmark = pytest.mark.usefixtures("db_session")


# --- the catalogue depends on stage and size -----------------------------------


def _keys(stage, band):
    return {c["key"] for c in capture.catalogue_for(stage, band)}


def test_a_tiny_pre_revenue_company_is_offered_only_a_few_capabilities():
    offered = _keys("pre_revenue", "micro")
    assert offered == {"customer_acquisition", "product_development", "finance_and_billing"}


def test_a_large_established_company_is_offered_the_full_set():
    assert len(_keys("established", "large")) > len(_keys("pre_revenue", "micro")) * 4
    assert {"procurement", "data_governance", "business_continuity"} <= _keys("established", "large")


def test_size_alone_gates_governance_capabilities():
    # Same stage, different size: a small company is not asked about procurement.
    assert "procurement" not in _keys("established", "micro")
    assert "procurement" in _keys("established", "mid")


def test_stage_alone_gates_capabilities_too():
    assert "customer_support" not in _keys("pre_revenue", "large")
    assert "customer_support" in _keys("early_revenue", "large")


def test_expected_level_rises_with_the_stage():
    early = {c["key"]: c["expected"] for c in capture.catalogue_for("early_revenue", "micro")}
    established = {c["key"]: c["expected"] for c in capture.catalogue_for("established", "micro")}
    assert early["customer_acquisition"] == 2 and established["customer_acquisition"] == 4


@pytest.mark.parametrize(
    "text,band",
    [("just me", "micro"), ("8 people", "micro"), ("40 staff", "small"), ("120", "mid"), ("5,000 employees", "large"), (None, "micro")],
)
def test_size_band_is_inferred_from_free_text(text, band):
    assert capture.band_from_text(text) == band


# --- writing to the real tables -----------------------------------------------


def test_an_answer_creates_a_real_capability_row(db_session, make_org, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability

    org = make_org("cap")
    with tenant_ctx(org.id):
        result = capture.save(
            [{"key": "customer_acquisition", "owner": "Sam", "maturity": 2}],
            stage="early_revenue",
            size_band="micro",
        )
        row = BusinessCapability.query.filter_by(name="Customer acquisition").one()

    assert result == {"created": 1, "updated": 0}
    assert row.organization_id == org.id
    assert row.business_owner == "Sam"
    assert row.current_maturity_level == 2
    assert row.maturity_assessment_date is not None, "a stored rating must be stamped as a real assessment"


def test_the_answer_reaches_the_unified_table_with_its_element_and_maturity(db_session, make_org, tenant_ctx):
    """What the intelligence lenses read: UnifiedCapability keyed by organisation,
    carrying the ArchiMate element and the maturity, with no extra step."""
    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    org = make_org("unified")
    with tenant_ctx(org.id):
        capture.save(
            [{"key": "product_development", "owner": "Priya", "maturity": 3}],
            stage="pre_revenue",
            size_band="micro",
        )
        source = BusinessCapability.query.filter_by(name="Product development").one()
        unified = UnifiedCapability.query.filter_by(
            source_table="business_capability", source_id=str(source.id)
        ).one()
        element = db.session.get(ArchiMateElement, unified.archimate_element_id)

    assert unified.organization_id == org.id
    assert unified.current_maturity_level == 3
    assert unified.archimate_element_id is not None and unified.archimate_element_id == source.archimate_element_id
    assert element.type == "Capability" and element.organization_id == org.id


def test_an_unrated_capability_stays_unassessed(db_session, make_org, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability

    org = make_org("unrated")
    with tenant_ctx(org.id):
        capture.save([{"key": "finance_and_billing", "owner": "Outsourced"}], stage="pre_revenue", size_band="micro")
        row = BusinessCapability.query.filter_by(name="Finance and billing").one()

    assert row.current_maturity_level is None, "an unrated capability must not carry a made-up level"
    assert row.maturity_assessment_date is None
    assert row.target_maturity_level is None, "the stage expectation is a comparison, never the company's own target"


def test_nothing_is_created_for_capabilities_that_were_not_answered(db_session, make_org, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability

    org = make_org("sparse")
    with tenant_ctx(org.id):
        capture.save([{"key": "customer_acquisition", "maturity": 1}], stage="early_revenue", size_band="micro")
        assert BusinessCapability.query.count() == 1


def test_saving_twice_updates_the_same_row(db_session, make_org, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability

    org = make_org("twice")
    with tenant_ctx(org.id):
        capture.save([{"key": "marketing", "maturity": 1}], stage="early_revenue", size_band="micro")
        again = capture.save([{"key": "marketing", "maturity": 3}], stage="early_revenue", size_band="micro")
        rows = BusinessCapability.query.filter_by(name="Marketing").all()

    assert again == {"created": 0, "updated": 1}
    assert len(rows) == 1 and rows[0].current_maturity_level == 3


def test_a_rating_can_be_cleared_back_to_unassessed(db_session, make_org, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability

    org = make_org("clear")
    with tenant_ctx(org.id):
        capture.save([{"key": "marketing", "maturity": 2}], stage="early_revenue", size_band="micro")
        capture.save([{"key": "marketing", "maturity": None}], stage="early_revenue", size_band="micro")
        row = BusinessCapability.query.filter_by(name="Marketing").one()

    assert row.current_maturity_level is None and row.maturity_assessment_date is None


def test_a_capability_not_offered_to_this_company_is_ignored(db_session, make_org, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability

    org = make_org("ignored")
    with tenant_ctx(org.id):
        result = capture.save([{"key": "procurement", "maturity": 2}], stage="pre_revenue", size_band="micro")
        assert BusinessCapability.query.count() == 0

    assert result == {"created": 0, "updated": 0}


def test_an_out_of_range_level_is_not_stored(db_session, make_org, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability

    org = make_org("range")
    with tenant_ctx(org.id):
        capture.save([{"key": "marketing", "maturity": 9}], stage="early_revenue", size_band="micro")
        row = BusinessCapability.query.filter_by(name="Marketing").one()

    assert row.current_maturity_level is None


# --- tenant isolation -----------------------------------------------------------


def test_two_organisations_get_separate_rows_and_never_see_each_others(db_session, make_org, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability

    org_a, org_b = make_org("a"), make_org("b")
    with tenant_ctx(org_a.id):
        capture.save([{"key": "marketing", "owner": "Org A person", "maturity": 4}], stage="early_revenue", size_band="micro")
    with tenant_ctx(org_b.id):
        capture.save([{"key": "marketing", "owner": "Org B person", "maturity": 1}], stage="early_revenue", size_band="micro")
        b_rows = BusinessCapability.query.filter_by(name="Marketing").all()
        b_view = capture.read("early_revenue", "micro")
    with tenant_ctx(org_a.id):
        a_view = capture.read("early_revenue", "micro")

    assert len(b_rows) == 1 and b_rows[0].business_owner == "Org B person"
    assert {r["key"]: r["maturity"] for r in a_view}["marketing"] == 4
    assert {r["key"]: r["maturity"] for r in b_view}["marketing"] == 1


def test_read_shows_what_is_already_recorded(db_session, make_org, tenant_ctx):
    org = make_org("read")
    with tenant_ctx(org.id):
        capture.save([{"key": "customer_acquisition", "owner": "Sam", "maturity": 2}], stage="early_revenue", size_band="micro")
        rows = {r["key"]: r for r in capture.read("early_revenue", "micro")}

    assert rows["customer_acquisition"]["present"] and rows["customer_acquisition"]["owner"] == "Sam"
    assert not rows["marketing"]["present"] and rows["marketing"]["maturity"] is None

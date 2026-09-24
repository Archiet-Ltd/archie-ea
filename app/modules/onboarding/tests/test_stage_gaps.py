"""Stage-gap comparison logic — pure functions, no app context needed."""
from app.modules.onboarding.services import stage_gaps


def test_pre_revenue_expects_only_its_own_baseline():
    expected = stage_gaps.expected_for_stage("pre_revenue")
    assert "founder_ceo" in expected["roles"]
    assert "head_of_sales" not in expected["roles"], "growing-stage roles must not leak into pre_revenue"


def test_growing_is_additive_over_earlier_stages():
    expected = stage_gaps.expected_for_stage("growing")
    assert "founder_ceo" in expected["roles"], "pre_revenue baseline must still be present"
    assert "first_sales_owner" in expected["roles"], "early_revenue baseline must still be present"
    assert "head_of_sales" in expected["roles"], "growing's own baseline must be present"


def test_unknown_stage_falls_back_to_pre_revenue():
    assert stage_gaps.expected_for_stage("not_a_real_stage") == stage_gaps.expected_for_stage("pre_revenue")


def test_compute_gaps_lists_everything_missing():
    gaps = stage_gaps.compute_gaps("pre_revenue", recorded={})
    keys = {g["key"] for g in gaps}
    assert "founder_ceo" in keys
    assert "code_repository" in keys


def test_compute_gaps_excludes_what_is_recorded():
    gaps = stage_gaps.compute_gaps("pre_revenue", recorded={"roles": ["founder_ceo"]})
    keys = {g["key"] for g in gaps}
    assert "founder_ceo" not in keys
    assert "product_eng_lead" in keys, "only the recorded item should be excluded, not the whole category"


def test_conditional_control_absent_without_its_trigger():
    gaps = stage_gaps.compute_gaps(
        "early_revenue", recorded={},
        region_europe_or_eu_customers=False, handles_card_data_directly=False,
    )
    keys = {g["key"] for g in gaps}
    assert "gdpr_data_protection" not in keys, "the GDPR control must not appear without its trigger"
    assert "payment_security" not in keys


def test_conditional_control_present_with_its_trigger():
    gaps = stage_gaps.compute_gaps(
        "early_revenue", recorded={},
        region_europe_or_eu_customers=True, handles_card_data_directly=False,
    )
    keys = {g["key"] for g in gaps}
    assert "gdpr_data_protection" in keys
    assert "payment_security" not in keys


def test_label_for_falls_back_for_unknown_key():
    assert stage_gaps.label_for("some_unmapped_key") == "Some unmapped key"


def test_label_for_known_key_uses_the_plain_label_map():
    assert stage_gaps.label_for("founder_ceo") == "Founder / CEO"

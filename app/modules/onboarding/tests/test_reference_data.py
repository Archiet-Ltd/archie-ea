"""Archiet's lifted reference lists: counts, and the recommended / common /
other grouping algorithm copied from organization-data-loader.ts."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def test_flat_industries_covers_every_category():
    from app.modules.onboarding.services import reference_data

    industries = reference_data.flat_industries()
    keys = {i["key"] for i in industries}

    # 19 categories in organization_metadata.json, over 100 industries in all.
    assert len(industries) > 100
    assert "software-development" in keys
    assert "hospitals-health-systems" in keys


def test_industry_value_for_label_matches_case_insensitively():
    from app.modules.onboarding.services import reference_data

    assert reference_data.industry_value_for_label("Software Development") == "software-development"
    assert reference_data.industry_value_for_label("software development") == "software-development"
    assert reference_data.industry_value_for_label("Not a real industry") is None
    assert reference_data.industry_value_for_label("") is None


def test_company_sizes_sectors_and_regions_are_lifted():
    from app.modules.onboarding.services import reference_data

    assert [s["key"] for s in reference_data.company_sizes()] == [
        "startup", "small", "medium", "large", "enterprise",
    ]
    assert {s["key"] for s in reference_data.sectors()} == {"private", "public", "nonprofit"}
    assert "europe" in {r["key"] for r in reference_data.regions()}


def test_governance_maturity_levels_is_the_five_step_scale():
    from app.modules.onboarding.services import reference_data

    levels = reference_data.governance_maturity_levels()
    assert [level["key"] for level in levels] == ["none", "ad-hoc", "developing", "managed", "optimized"]


def test_maturity_status_options_is_archiets_five_step_scale():
    from app.modules.onboarding.services import reference_data

    keys = [o["key"] for o in reference_data.MATURITY_STATUS_OPTIONS]
    assert keys == ["none", "planning", "partial", "substantial", "full"]
    assert reference_data.maturity_status_label("partial") == "Partially Implemented"
    assert reference_data.maturity_status_label("not_a_real_key") == "Not a real key"


def test_compliance_standards_has_every_lifted_standard():
    from app.modules.onboarding.services import reference_data

    keys = set(reference_data.compliance_standard_keys())
    assert len(keys) == 21
    for expected in ("gdpr", "hipaa", "soc2", "iso27001", "pci-dss", "sox"):
        assert expected in keys
    assert reference_data.compliance_standard("gdpr")["name"] == "GDPR"


def test_grouped_compliance_standards_recommends_by_region():
    from app.modules.onboarding.services import reference_data

    groups = {
        row["key"]: row["group"] for row in reference_data.grouped_compliance_standards("europe", None)
    }
    assert groups["gdpr"] == "recommended"
    assert groups["mifid-ii"] == "recommended"
    # Region-recommended for north-america, not for europe: falls to common/other.
    assert groups.get("hipaa") in ("common", "other")


def test_grouped_compliance_standards_recommends_by_industry():
    from app.modules.onboarding.services import reference_data

    groups = {
        row["key"]: row["group"]
        for row in reference_data.grouped_compliance_standards(None, "hospitals-health-systems")
    }
    assert groups["hipaa"] == "recommended"


def test_grouped_compliance_standards_falls_back_to_common_and_other_with_no_signal():
    from app.modules.onboarding.services import reference_data

    groups = reference_data.grouped_compliance_standards(None, None)
    group_names = {row["group"] for row in groups}
    # No industry/region means nothing can be "recommended" honestly.
    assert "recommended" not in group_names
    assert group_names == {"common", "other"}
    # Every standard is accounted for exactly once.
    assert len(groups) == len(reference_data.compliance_standards())


def test_frameworks_are_lifted_with_archiets_own_ids():
    from app.modules.onboarding.services import reference_data

    keys = {f["key"] for f in reference_data.frameworks()}
    assert keys == {
        "cobit2019", "itil-v4", "iso27001", "nist-csf", "togaf-enterprise-architecture",
        "safe-scaled-agile", "agile-methodology", "devops-practices", "lean-six-sigma",
        "okr-objectives-key-results",
    }


def test_transformation_templates_carry_their_own_phases():
    from app.modules.onboarding.services import reference_data

    keys = {t["key"] for t in reference_data.transformation_templates()}
    assert keys == {"crm", "erp", "data-platform", "cloud-migration", "digital-workplace", "e-commerce"}

    crm = reference_data.transformation_template("crm")
    assert crm["phases"] == ["Planning", "Configuration", "Migration", "Launch"]
    assert crm["category"] == "Customer Experience"


def test_implementation_types_are_lifted():
    from app.modules.onboarding.services import reference_data

    keys = {t["key"] for t in reference_data.implementation_types()}
    assert keys == {"enterprise-platform", "custom-development", "system-integrations", "governance-only"}
    assert reference_data.implementation_type("custom-development")["title"] == "Custom Application Development"

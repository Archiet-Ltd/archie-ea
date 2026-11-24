"""Requirement-quality / EARS check (report mode) — founder's 20 Nov 2026
ruling that EARS is the mandatory statement form, plus the agreed
ISO/IEC/IEEE 29148 + INCOSE attributes.

Report mode only: this check must never write to a Requirement row or its
mirrored ArchiMate element. One seeded EARS-compliant requirement (with
verification, a trace and a kind) must count as compliant; one seeded
non-EARS behavioural requirement with none of those must be the single
non-compliant row the report names.
"""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_requirement_with_archimate_element(db_session, org, **kwargs):
    from app.models.archimate_core import ArchiMateElement
    from app.models.models import Requirement

    name = kwargs.pop("title")
    element = ArchiMateElement(
        name=name, type="Requirement", layer="motivation", organization_id=org.id
    )
    db_session.add(element)
    db_session.flush()

    requirement = Requirement(
        title=name,
        archimate_element_id=element.id,
        organization_id=org.id,
        **kwargs,
    )
    db_session.add(requirement)
    db_session.flush()
    return requirement, element


def test_ears_requirement_with_full_attributes_is_compliant(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.services.requirement_quality_report_service import compute_requirement_quality_report

    org = make_org("reqquality")
    with tenant_ctx(org.id):
        suffix = uuid.uuid4().hex[:6]

        stakeholder = ArchiMateElement(
            name=f"Stakeholder {suffix}", type="Stakeholder", layer="motivation",
            organization_id=org.id,
        )
        db_session.add(stakeholder)
        db_session.flush()

        compliant, compliant_element = _make_requirement_with_archimate_element(
            db_session,
            org,
            title=f"Compliant EARS requirement {suffix}",
            description="When the user submits the form, the system shall validate every field.",
            type="functional",
            verification_method="test",
        )

        relationship = ArchiMateRelationship(
            type="influence",
            source_id=stakeholder.id,
            target_id=compliant_element.id,
            organization_id=org.id,
        )
        db_session.add(relationship)
        db_session.flush()

        non_compliant_name = f"Non-EARS behavioural requirement {suffix}"
        _make_requirement_with_archimate_element(
            db_session,
            org,
            title=non_compliant_name,
            description="Users should be able to log in quickly and reliably.",
            type=None,
            verification_method=None,
        )

        report = compute_requirement_quality_report(organization_id=org.id)

    assert report["total"] == 2
    assert report["non_compliant_count"] == 1
    assert report["non_compliant_names"] == [non_compliant_name]
    assert report["not_ears_count"] == 1
    assert report["not_ears_names"] == [non_compliant_name]
    assert report["missing_verification_count"] == 1
    assert report["missing_verification_names"] == [non_compliant_name]
    assert report["missing_trace_count"] == 1
    assert report["missing_trace_names"] == [non_compliant_name]
    assert report["missing_kind_count"] == 1
    assert report["missing_kind_names"] == [non_compliant_name]


@pytest.mark.parametrize(
    "statement,expected",
    [
        ("When the sensor detects motion, the system shall trigger an alarm.", True),
        ("While the engine is running, the system shall log telemetry every second.", True),
        ("If the payment fails, then the system shall retry up to 3 times.", True),
        ("Where biometric login is enabled, the system shall require a fingerprint.", True),
        ("The system shall encrypt data at rest.", True),
        ("Users should be able to log in quickly.", False),
        ("", False),
        (None, False),
    ],
)
def test_ears_pattern_matching(statement, expected):
    from app.services.requirement_quality_report_service import is_ears_compliant

    assert is_ears_compliant(statement) is expected


def test_empty_organization_reports_real_zero_not_fabricated(db_session, make_org, tenant_ctx):
    """An organisation with no requirements measures a real 0, not a crash
    and not a fabricated non-zero count."""
    from app.services.requirement_quality_report_service import compute_requirement_quality_report

    org = make_org("reqquality_empty")
    with tenant_ctx(org.id):
        report = compute_requirement_quality_report(organization_id=org.id)

    assert report["total"] == 0
    assert report["non_compliant_count"] == 0
    assert report["non_compliant_names"] == []

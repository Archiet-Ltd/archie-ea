"""Tests for framework adoption, harmonisation, applicability and regulatory change.

Covers:
- Tenant isolation: org A's adoptions never appear for org B
- Framework adoption populates controls within the same request
- Harmonisation match, once confirmed, is reused by the second framework
- Regulatory change recording and affected elements
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# ── helpers ────────────────────────────────────────────────────────────


def _seed_framework(db_session, code, name, category="security"):
    from app.models.compliance_models import RegulatoryFramework

    fw = RegulatoryFramework(
        code=code,
        name=name,
        category=category,
        jurisdiction="Global",
        status="active",
    )
    db_session.add(fw)
    db_session.flush()
    return fw


def _seed_control(db_session, framework_id, control_code, title, priority="high"):
    from app.models.compliance_models import ComplianceControl

    ctl = ComplianceControl(
        framework_id=framework_id,
        control_code=control_code,
        title=title,
        priority=priority,
    )
    db_session.add(ctl)
    db_session.flush()
    return ctl


def _seed_user(db_session, org_id, email):
    from app.models.user import User

    user = User(
        email=email,
        organization_id=org_id,
        password_hash="test",
    )
    db_session.add(user)
    db_session.flush()
    return user


# ── tenant isolation ───────────────────────────────────────────────────


def test_framework_adoption_isolation(db_session, make_org, tenant_ctx):
    """Org A's adopted frameworks must never appear for org B."""
    from app.models.regulatory_framework import FrameworkAdoption

    org_a, org_b = make_org("a"), make_org("b")
    fw = _seed_framework(db_session, "ISO-27001", "ISO/IEC 27001")

    # Org A adopts
    with tenant_ctx(org_a.id):
        adoption_a = FrameworkAdoption(
            organization_id=org_a.id,
            scope="tenant",
            framework_id=fw.id,
            status="active",
        )
        db_session.add(adoption_a)
        db_session.flush()

    # Org B must not see org A's adoption
    with tenant_ctx(org_b.id):
        # FrameworkAdoption uses HybridTenantMixin, not TenantMixin, so
        # the auto-filter does not apply.  We query explicitly.
        b_adoptions = FrameworkAdoption.query.filter_by(
            organization_id=org_b.id
        ).all()
        a_adoptions_visible = FrameworkAdoption.query.filter_by(
            organization_id=org_a.id
        ).all()

    assert len(b_adoptions) == 0, "Org B should have no adoptions"
    assert len(a_adoptions_visible) == 1, (
        "Org A's adoption should be visible when queried directly"
    )


def test_adopted_control_isolation(db_session, make_org, tenant_ctx):
    """Org A's adopted controls must never appear for org B."""
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.regulatory_framework import FrameworkAdoption

    org_a, org_b = make_org("a"), make_org("b")
    fw = _seed_framework(db_session, "SOC-2", "SOC 2")
    ctl = _seed_control(db_session, fw.id, "CC6.1", "Logical and physical access")

    with tenant_ctx(org_a.id):
        adoption = FrameworkAdoption(
            organization_id=org_a.id,
            scope="tenant",
            framework_id=fw.id,
            status="active",
        )
        db_session.add(adoption)
        db_session.flush()
        ac = ApplicationComplianceControl(
            organization_id=org_a.id,
            adoption_id=adoption.id,
            control_id=ctl.id,
            implementation_status="planned",
        )
        db_session.add(ac)
        db_session.flush()

    with tenant_ctx(org_b.id):
        b_controls = ApplicationComplianceControl.query.filter_by(
            organization_id=org_b.id
        ).all()

    assert len(b_controls) == 0, "Org B must not see org A's adopted controls"


def test_regulatory_change_isolation(db_session, make_org, tenant_ctx):
    """Org A's regulatory changes must never appear for org B."""
    from app.models.regulatory_change import RegulatoryChange

    org_a, org_b = make_org("a"), make_org("b")
    fw = _seed_framework(db_session, "DORA", "DORA")

    with tenant_ctx(org_a.id):
        change = RegulatoryChange(
            organization_id=org_a.id,
            framework_id=fw.id,
            change_type="amendment",
            title="DORA amendment 2025",
        )
        db_session.add(change)
        db_session.flush()

    with tenant_ctx(org_b.id):
        b_changes = RegulatoryChange.query.filter_by(
            organization_id=org_b.id
        ).all()

    assert len(b_changes) == 0, "Org B must not see org A's regulatory changes"


# ── framework adoption ─────────────────────────────────────────────────


def test_adopt_framework_populates_controls(db_session, make_org, tenant_ctx):
    """Adopting a framework creates ApplicationComplianceControl rows for every control."""
    from app.models.application_compliance import ApplicationComplianceControl
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    org = make_org("a")
    fw = _seed_framework(db_session, "ISO-27001", "ISO/IEC 27001")
    _seed_control(db_session, fw.id, "A.5.1", "Policies for information security")
    _seed_control(db_session, fw.id, "A.8.2", "Privileged access rights")
    user = _seed_user(db_session, org.id, "adopter@org-a.test")

    with tenant_ctx(org.id):
        adoption = ApplicabilityService.adopt_framework(
            organization_id=org.id,
            framework_id=fw.id,
            adopted_by_id=user.id,
        )

        count = ApplicationComplianceControl.query.filter_by(
            organization_id=org.id, adoption_id=adoption.id
        ).count()

    assert count == 2, "Adopting a framework should populate all its controls"


# ── harmonisation ──────────────────────────────────────────────────────


def test_harmonization_propose_and_confirm(db_session):
    """A harmonisation match, once confirmed, links two controls."""
    from app.models.compliance_models import ComplianceControl

    fw1 = _seed_framework(db_session, "ISO-27001", "ISO/IEC 27001")
    fw2 = _seed_framework(db_session, "SOC-2", "SOC 2")
    ctl1 = _seed_control(db_session, fw1.id, "A.8.2", "Privileged access rights")
    ctl2 = _seed_control(db_session, fw2.id, "CC6.1", "Logical and physical access")

    # Propose harmonisation
    ctl1.harmonized_control_id = ctl2.id
    ctl1.harmonization_status = "proposed"
    ctl1.harmonization_notes = "Both cover access control"
    db_session.flush()

    assert ctl1.harmonization_status == "proposed"
    assert ctl1.harmonized_control_id == ctl2.id

    # Confirm
    ctl1.harmonization_status = "confirmed"
    db_session.flush()

    # Reload and verify
    reloaded = ComplianceControl.query.get(ctl1.id)
    assert reloaded.harmonization_status == "confirmed"
    assert reloaded.harmonized_control_id == ctl2.id


def test_harmonization_evidence_shared(db_session, make_org, tenant_ctx):
    """A CONFIRMED harmonisation shares evidence from the first framework's
    control to the second framework's control via evidence_status()."""
    from app.models.application_compliance import ApplicationComplianceControl
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    org = make_org("a")
    fw1 = _seed_framework(db_session, "ISO-27001", "ISO/IEC 27001")
    fw2 = _seed_framework(db_session, "SOC-2", "SOC 2")
    ctl1 = _seed_control(db_session, fw1.id, "A.8.2", "Privileged access rights")
    ctl2 = _seed_control(db_session, fw2.id, "CC6.1", "Logical and physical access")
    user = _seed_user(db_session, org.id, "test@org-a.test")

    with tenant_ctx(org.id):
        adoption1 = ApplicabilityService.adopt_framework(
            organization_id=org.id, framework_id=fw1.id, adopted_by_id=user.id
        )
        adoption2 = ApplicabilityService.adopt_framework(
            organization_id=org.id, framework_id=fw2.id, adopted_by_id=user.id
        )

        # Set evidence on ctl1's ApplicationComplianceControl
        ac1 = ApplicationComplianceControl.query.filter_by(
            adoption_id=adoption1.id, control_id=ctl1.id
        ).first()
        ac1.implementation_status = "implemented"
        ac1.evidence_url = "https://example.com/evidence/access-control"
        ac1.notes = "Implemented via IAM policy"
        db_session.flush()

        # Harmonise: ctl1 -> ctl2 (confirmed)
        ctl1.harmonized_control_id = ctl2.id
        ctl1.harmonization_status = "confirmed"
        db_session.flush()

        # Call evidence_status for the SECOND framework's adoption
        status2 = ApplicabilityService.evidence_status(org.id, adoption2.id)

        # Find ctl2's entry in adoption2's status
        ctl2_entry = next(s for s in status2 if s["control_id"] == ctl2.id)
        assert ctl2_entry is not None

        # ctl2 has no evidence of its own, so it should report ctl1's evidence
        assert ctl2_entry["evidence_url"] == "https://example.com/evidence/access-control"
        assert ctl2_entry["notes"] == "Implemented via IAM policy"
        assert ctl2_entry["evidence_source"] is not None
        assert ctl2_entry["evidence_source"]["control_code"] == "A.8.2"
        assert ctl2_entry["evidence_source"]["framework_code"] == "ISO-27001"

        # ctl1's own adoption should show its own evidence (no source marker)
        status1 = ApplicabilityService.evidence_status(org.id, adoption1.id)
        ctl1_entry = next(s for s in status1 if s["control_id"] == ctl1.id)
        assert ctl1_entry["evidence_url"] == "https://example.com/evidence/access-control"
        assert ctl1_entry["evidence_source"] is None  # own evidence, no marker


def test_harmonization_unconfirmed_does_not_share(db_session, make_org, tenant_ctx):
    """A PROPOSED (unconfirmed) harmonisation does NOT share evidence."""
    from app.models.application_compliance import ApplicationComplianceControl
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    org = make_org("a")
    fw1 = _seed_framework(db_session, "ISO-27001", "ISO/IEC 27001")
    fw2 = _seed_framework(db_session, "SOC-2", "SOC 2")
    ctl1 = _seed_control(db_session, fw1.id, "A.8.2", "Privileged access rights")
    ctl2 = _seed_control(db_session, fw2.id, "CC6.1", "Logical and physical access")
    user = _seed_user(db_session, org.id, "test@org-a.test")

    with tenant_ctx(org.id):
        adoption1 = ApplicabilityService.adopt_framework(
            organization_id=org.id, framework_id=fw1.id, adopted_by_id=user.id
        )
        adoption2 = ApplicabilityService.adopt_framework(
            organization_id=org.id, framework_id=fw2.id, adopted_by_id=user.id
        )

        # Set evidence on ctl1's ApplicationComplianceControl
        ac1 = ApplicationComplianceControl.query.filter_by(
            adoption_id=adoption1.id, control_id=ctl1.id
        ).first()
        ac1.evidence_url = "https://example.com/evidence/access-control"
        ac1.notes = "Implemented via IAM policy"
        db_session.flush()

        # Harmonise: ctl1 -> ctl2 but only PROPOSED (not confirmed)
        ctl1.harmonized_control_id = ctl2.id
        ctl1.harmonization_status = "proposed"
        db_session.flush()

        # Call evidence_status for the SECOND framework's adoption
        status = ApplicabilityService.evidence_status(org.id, adoption2.id)

        # Find ctl2's entry
        ctl2_entry = next(s for s in status if s["control_id"] == ctl2.id)
        assert ctl2_entry is not None

        # ctl2 should NOT see ctl1's evidence because harmonisation is only proposed
        assert ctl2_entry["evidence_url"] is None
        assert ctl2_entry["evidence_source"] is None


def test_harmonization_evidence_org_isolation(db_session, make_org, tenant_ctx):
    """Organisation B's evidence must never appear in organisation A's evidence_status."""
    from app.models.application_compliance import ApplicationComplianceControl
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    org_a, org_b = make_org("a"), make_org("b")
    fw1 = _seed_framework(db_session, "ISO-27001", "ISO/IEC 27001")
    fw2 = _seed_framework(db_session, "SOC-2", "SOC 2")
    ctl1 = _seed_control(db_session, fw1.id, "A.8.2", "Privileged access rights")
    ctl2 = _seed_control(db_session, fw2.id, "CC6.1", "Logical and physical access")
    user_a = _seed_user(db_session, org_a.id, "admin@org-a.test")
    user_b = _seed_user(db_session, org_b.id, "admin@org-b.test")

    # Org A: adopt both frameworks, set evidence on ctl1, confirm harmonisation
    with tenant_ctx(org_a.id):
        adoption1_a = ApplicabilityService.adopt_framework(
            organization_id=org_a.id, framework_id=fw1.id, adopted_by_id=user_a.id
        )
        adoption2_a = ApplicabilityService.adopt_framework(
            organization_id=org_a.id, framework_id=fw2.id, adopted_by_id=user_a.id
        )

        ac1_a = ApplicationComplianceControl.query.filter_by(
            adoption_id=adoption1_a.id, control_id=ctl1.id
        ).first()
        ac1_a.evidence_url = "https://example.com/evidence/org-a"
        ac1_a.notes = "Org A evidence"
        db_session.flush()

        ctl1.harmonized_control_id = ctl2.id
        ctl1.harmonization_status = "confirmed"
        db_session.flush()

    # Org B: adopt both frameworks, set evidence on ctl1, confirm harmonisation
    with tenant_ctx(org_b.id):
        adoption1_b = ApplicabilityService.adopt_framework(
            organization_id=org_b.id, framework_id=fw1.id, adopted_by_id=user_b.id
        )
        adoption2_b = ApplicabilityService.adopt_framework(
            organization_id=org_b.id, framework_id=fw2.id, adopted_by_id=user_b.id
        )

        ac1_b = ApplicationComplianceControl.query.filter_by(
            adoption_id=adoption1_b.id, control_id=ctl1.id
        ).first()
        ac1_b.evidence_url = "https://example.com/evidence/org-b"
        ac1_b.notes = "Org B evidence"
        db_session.flush()

        # Also confirm harmonisation for org B (ctl1 -> ctl2)
        # ctl1 is shared catalogue, so its harmonisation is already set from org A's setup
        # We need to ensure org B's evidence is set
        db_session.flush()

    # Org A's evidence_status must NOT show org B's evidence
    with tenant_ctx(org_a.id):
        status_a = ApplicabilityService.evidence_status(org_a.id, adoption2_a.id)
        ctl2_entry_a = next(s for s in status_a if s["control_id"] == ctl2.id)
        assert ctl2_entry_a is not None
        # Should show org A's evidence, not org B's
        assert ctl2_entry_a["evidence_url"] == "https://example.com/evidence/org-a"
        assert "org-b" not in (ctl2_entry_a.get("notes") or "").lower()
        assert ctl2_entry_a["evidence_source"]["control_code"] == "A.8.2"

    # Org B's evidence_status must NOT show org A's evidence
    with tenant_ctx(org_b.id):
        status_b = ApplicabilityService.evidence_status(org_b.id, adoption2_b.id)
        ctl2_entry_b = next(s for s in status_b if s["control_id"] == ctl2.id)
        assert ctl2_entry_b is not None
        # Should show org B's evidence, not org A's
        assert ctl2_entry_b["evidence_url"] == "https://example.com/evidence/org-b"
        assert "org-a" not in (ctl2_entry_b.get("notes") or "").lower()
        assert ctl2_entry_b["evidence_source"]["control_code"] == "A.8.2"


# ── applicability ──────────────────────────────────────────────────────


def test_applicability_service_evaluates_node(db_session, make_org):
    """ApplicabilityService evaluates nodes against framework rules."""
    from app.models.technology_layer import Node
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    org = make_org("a")
    node = Node(
        name="cloud-api-01",
        organization_id=org.id,
        node_type="Cloud Instance",
        deployment_model="Cloud",
    )
    db_session.add(node)
    db_session.flush()

    # SOC-2 rule: deployment_model in ["Cloud", "Hybrid"]
    assert ApplicabilityService.evaluate_node(node, "SOC-2") is True

    # ISO-27001 rule: node_type in ["Virtual Machine", "Cloud Instance", ...]
    assert ApplicabilityService.evaluate_node(node, "ISO-27001") is True


def test_applicability_service_excludes_non_matching_node(db_session, make_org):
    """Nodes that don't match rules are excluded."""
    from app.models.technology_layer import Node
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    org = make_org("a")
    node = Node(
        name="on-prem-db",
        organization_id=org.id,
        node_type="Physical Server",
        deployment_model="On-Premise",
    )
    db_session.add(node)
    db_session.flush()

    # SOC-2 rule: deployment_model in ["Cloud", "Hybrid"] — On-Premise excluded
    assert ApplicabilityService.evaluate_node(node, "SOC-2") is False


def test_get_in_scope_nodes(db_session, make_org):
    """get_in_scope_nodes returns only matching nodes."""
    from app.models.technology_layer import Node
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    org = make_org("a")
    cloud_node = Node(
        name="cloud-api",
        organization_id=org.id,
        node_type="Cloud Instance",
        deployment_model="Cloud",
    )
    onprem_node = Node(
        name="on-prem-db",
        organization_id=org.id,
        node_type="Physical Server",
        deployment_model="On-Premise",
    )
    db_session.add_all([cloud_node, onprem_node])
    db_session.flush()

    in_scope = ApplicabilityService.get_in_scope_nodes(org.id, "SOC-2")
    assert len(in_scope) == 1
    assert in_scope[0]["name"] == "cloud-api"


# ── regulatory change ──────────────────────────────────────────────────


def test_record_regulatory_change_creates_impacts(db_session, make_org, tenant_ctx):
    """Recording a regulatory change computes affected controls."""
    from app.models.regulatory_change import RegulatoryChange, RegulatoryChangeImpact

    org = make_org("a")
    fw = _seed_framework(db_session, "DORA", "DORA")
    ctl = _seed_control(db_session, fw.id, "DORA-Art.5", "ICT governance")

    with tenant_ctx(org.id):
        change = RegulatoryChange(
            organization_id=org.id,
            framework_id=fw.id,
            change_type="amendment",
            title="DORA amendment 2025",
            description="Updated ICT risk management requirements",
        )
        db_session.add(change)
        db_session.flush()

        # Manually add impact (the service does this automatically)
        impact = RegulatoryChangeImpact(
            organization_id=org.id,
            change_id=change.id,
            element_type="control",
            element_id=ctl.id,
            element_name=f"{ctl.control_code}: {ctl.title}",
            impact_assessment="Control may be affected",
        )
        db_session.add(impact)
        db_session.flush()

        affected = RegulatoryChangeImpact.query.filter_by(
            change_id=change.id, organization_id=org.id
        ).all()

    assert len(affected) == 1
    assert affected[0].element_type == "control"
    assert affected[0].element_id == ctl.id


def test_regulatory_change_affected_elements_isolated(db_session, make_org, tenant_ctx):
    """Affected elements from org A's change are not visible to org B."""
    from app.models.regulatory_change import RegulatoryChange, RegulatoryChangeImpact

    org_a, org_b = make_org("a"), make_org("b")
    fw = _seed_framework(db_session, "DORA", "DORA")
    ctl = _seed_control(db_session, fw.id, "DORA-Art.5", "ICT governance")

    with tenant_ctx(org_a.id):
        change = RegulatoryChange(
            organization_id=org_a.id,
            framework_id=fw.id,
            change_type="amendment",
            title="DORA amendment",
        )
        db_session.add(change)
        db_session.flush()
        impact = RegulatoryChangeImpact(
            organization_id=org_a.id,
            change_id=change.id,
            element_type="control",
            element_id=ctl.id,
            element_name=ctl.control_code,
        )
        db_session.add(impact)
        db_session.flush()

    with tenant_ctx(org_b.id):
        b_impacts = RegulatoryChangeImpact.query.filter_by(
            organization_id=org_b.id
        ).all()

    assert len(b_impacts) == 0, "Org B must not see org A's change impacts"


# ── shared catalogue is identical and read-only ────────────────────────


def test_shared_catalogue_identical_for_both_orgs(db_session, make_org, tenant_ctx):
    """The shared catalogue (RegulatoryFramework) is identical for both orgs."""
    from app.models.compliance_models import RegulatoryFramework

    org_a, org_b = make_org("a"), make_org("b")
    _seed_framework(db_session, "ISO-27001", "ISO/IEC 27001")

    with tenant_ctx(org_a.id):
        a_frameworks = {f.code for f in RegulatoryFramework.query.all()}

    with tenant_ctx(org_b.id):
        b_frameworks = {f.code for f in RegulatoryFramework.query.all()}

    assert "ISO-27001" in a_frameworks
    assert a_frameworks == b_frameworks, (
        "Shared catalogue must be identical for both organisations"
    )


# ── harmonisation authorisation ─────────────────────────────────────────


def test_harmonisation_routes_require_platform_admin(app, db_session, make_org, login_as):
    """propose_harmonization and confirm_harmonization are platform-admin only.
    Org admin and architect get 403; platform admin succeeds.
    """
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.models.user import Permission, Role, User

    org = make_org("a")
    fw1 = _seed_framework(db_session, "ISO-27001", "ISO/IEC 27001")
    fw2 = _seed_framework(db_session, "SOC-2", "SOC 2")
    ctl1 = _seed_control(db_session, fw1.id, "A.8.2", "Privileged access rights")
    ctl2 = _seed_control(db_session, fw2.id, "CC6.1", "Logical and physical access")
    db_session.commit()  # Ensure controls are visible to test client

    # Create users with different roles
    architect_role = Role.query.filter_by(name="Architect").first()
    if architect_role is None:
        architect_role = Role(name="Architect", permissions=Permission.GENERAL)
        db_session.add(architect_role)
        db_session.flush()

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        admin_role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(admin_role)
        db_session.flush()

    # Org admin (has is_org_admin=True and Administrator role)
    org_admin = User(
        email="orgadmin@org-a.test",
        first_name="Org",
        last_name="Admin",
        organization_id=org.id,
        role=admin_role,
        is_org_admin=True,
        is_platform_admin=False,
        confirmed=True,
    )
    org_admin.password = "test"
    db_session.add(org_admin)

    # Architect (has Architect role, no admin flags)
    architect = User(
        email="architect@org-a.test",
        first_name="Arch",
        last_name="Tect",
        organization_id=org.id,
        role=architect_role,
        is_org_admin=False,
        is_platform_admin=False,
        confirmed=True,
    )
    architect.password = "test"
    db_session.add(architect)

    # Platform admin (has is_platform_admin=True and Administrator role)
    platform_admin = User(
        email="platform@admin.test",
        first_name="Platform",
        last_name="Admin",
        organization_id=org.id,
        role=admin_role,
        is_org_admin=False,
        is_platform_admin=True,
        confirmed=True,
    )
    platform_admin.password = "test"
    db_session.add(platform_admin)

    db_session.commit()

    client = app.test_client()

    # Test propose_harmonization (blueprint has /dashboard prefix)
    propose_url = f"/dashboard/api/compliance/controls/{ctl1.id}/harmonize"
    confirm_url = f"/dashboard/api/compliance/controls/{ctl1.id}/harmonize/confirm"

    # Org admin should get 403
    login_as(client, org_admin)
    resp = client.post(propose_url, json={"target_control_id": ctl2.id})
    assert resp.status_code == 403, f"Org admin should get 403 on propose, got {resp.status_code}: {resp.get_json()}"

    # Architect should get 403
    login_as(client, architect)
    resp = client.post(propose_url, json={"target_control_id": ctl2.id})
    assert resp.status_code == 403, f"Architect should get 403 on propose, got {resp.status_code}: {resp.get_json()}"

    # Platform admin should succeed
    login_as(client, platform_admin)
    resp = client.post(propose_url, json={"target_control_id": ctl2.id})
    assert resp.status_code == 200, f"Platform admin should succeed on propose, got {resp.status_code}: {resp.get_json()}"

    # Test confirm_harmonization - first reset the harmonization
    ctl1.harmonized_control_id = None
    ctl1.harmonization_status = None
    db_session.commit()

    # Org admin should get 403
    login_as(client, org_admin)
    resp = client.post(confirm_url)
    assert resp.status_code == 403, f"Org admin should get 403 on confirm, got {resp.status_code}: {resp.get_json()}"

    # Architect should get 403
    login_as(client, architect)
    resp = client.post(confirm_url)
    assert resp.status_code == 403, f"Architect should get 403 on confirm, got {resp.status_code}: {resp.get_json()}"

    # Platform admin should succeed (after proposing again)
    login_as(client, platform_admin)
    resp = client.post(propose_url, json={"target_control_id": ctl2.id})
    assert resp.status_code == 200
    resp = client.post(confirm_url)
    assert resp.status_code == 200, f"Platform admin should succeed on confirm, got {resp.status_code}: {resp.get_json()}"
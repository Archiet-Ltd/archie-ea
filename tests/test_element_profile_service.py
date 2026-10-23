"""Tests for the element profile service — kind, WSJF, registry, isolation."""
from __future__ import annotations

import pytest
from app.services.element_profile_service import (
    allowed_kinds,
    kind_allowed,
    kind_properties,
    list_frameworks,
    list_kinds,
    read_profile,
    set_kind,
    set_wsjf_component,
    set_wsjf_components,
)


class TestProfileRegistry:
    """The SAFe profile registry defines kinds and their allowed types."""

    def test_list_frameworks_includes_safe(self):
        frameworks = list_frameworks()
        ids = [fw["id"] for fw in frameworks]
        assert "safe" in ids

    def test_list_kinds_includes_epic(self):
        kinds = list_kinds("safe")
        kind_names = [k["kind"] for k in kinds]
        assert "Epic" in kind_names
        assert "Feature" in kind_names
        assert "Enabler" in kind_names

    def test_allowed_kinds_for_work_package(self):
        kinds = allowed_kinds("WorkPackage", "safe")
        assert "Epic" in kinds
        assert "Feature" in kinds
        assert "Capability (SAFe)" in kinds
        assert "Enabler" in kinds

    def test_allowed_kinds_for_business_collaboration(self):
        kinds = allowed_kinds("BusinessCollaboration", "safe")
        assert "Agile Release Train" in kinds
        assert "Solution Train" in kinds

    def test_allowed_kinds_for_goal(self):
        kinds = allowed_kinds("Goal", "safe")
        assert "Strategic Theme" in kinds

    def test_safe_capability_refused_on_archimate_capability(self):
        """A SAFe 'Capability' is a WorkPackage kind; refused on an ArchiMate Capability."""
        assert not kind_allowed("Capability (SAFe)", "Capability", "safe")

    def test_epic_refused_on_application_component(self):
        assert not kind_allowed("Epic", "ApplicationComponent", "safe")

    def test_kind_properties_include_wsjf(self):
        props = kind_properties("Epic", "safe")
        assert "business_value" in props
        assert "time_criticality" in props
        assert "risk_reduction" in props
        assert "job_size" in props


class TestProfileReadWrite:
    """Reading and writing profile kind and WSJF on elements."""

    def test_set_and_read_kind(self, db_session, make_org, tenant_ctx):
        org = make_org("profile-test")
        from app.models import ArchiMateElement

        element = ArchiMateElement(
            name="Test Work Package",
            type="WorkPackage",
            organization_id=org.id,
        )
        db_session.add(element)
        db_session.flush()

        with tenant_ctx(org.id):
            set_kind(element, "Epic", "safe")
            db_session.flush()

        profile = read_profile(element)
        assert profile["kind"] == "Epic"
        assert profile["framework_id"] == "safe"

    def test_set_kind_refused_for_wrong_type(self, db_session, make_org, tenant_ctx):
        org = make_org("profile-test")
        from app.models import ArchiMateElement

        element = ArchiMateElement(
            name="Test Capability",
            type="Capability",
            organization_id=org.id,
        )
        db_session.add(element)
        db_session.flush()

        with tenant_ctx(org.id):
            with pytest.raises(ValueError, match="not allowed"):
                set_kind(element, "Epic", "safe")

    def test_set_and_read_wsjf_components(self, db_session, make_org, tenant_ctx):
        org = make_org("profile-test")
        from app.models import ArchiMateElement

        element = ArchiMateElement(
            name="Test Epic",
            type="WorkPackage",
            organization_id=org.id,
        )
        db_session.add(element)
        db_session.flush()

        with tenant_ctx(org.id):
            set_kind(element, "Epic", "safe")
            result = set_wsjf_components(element, 10, 5, 3, 2)
            db_session.flush()

        assert result["wsjf_score"] == 9.0
        assert result["properties"]["business_value"]["value"] == 10
        assert result["properties"]["time_criticality"]["value"] == 5
        assert result["properties"]["risk_reduction"]["value"] == 3
        assert result["properties"]["job_size"]["value"] == 2

    def test_wsjf_score_on_element_page(self, db_session, make_org, tenant_ctx):
        """The WSJF score equals (BV + TC + RR) / JS from the shared function."""
        org = make_org("profile-test")
        from app.models import ArchiMateElement

        element = ArchiMateElement(
            name="Test Epic",
            type="WorkPackage",
            organization_id=org.id,
        )
        db_session.add(element)
        db_session.flush()

        with tenant_ctx(org.id):
            set_kind(element, "Epic", "safe")
            set_wsjf_component(element, "business_value", 20)
            set_wsjf_component(element, "time_criticality", 10)
            set_wsjf_component(element, "risk_reduction", 5)
            set_wsjf_component(element, "job_size", 5)
            db_session.flush()

        profile = read_profile(element)
        # (20 + 10 + 5) / 5 = 7.0
        assert profile["wsjf_score"] == 7.0

    def test_remove_kind(self, db_session, make_org, tenant_ctx):
        org = make_org("profile-test")
        from app.models import ArchiMateElement

        element = ArchiMateElement(
            name="Test Work Package",
            type="WorkPackage",
            organization_id=org.id,
        )
        db_session.add(element)
        db_session.flush()

        with tenant_ctx(org.id):
            set_kind(element, "Epic", "safe")
            db_session.flush()
            set_kind(element, None, "safe")
            db_session.flush()

        profile = read_profile(element)
        assert profile["kind"] is None

    def test_property_shows_setter_and_time(self, db_session, make_org, tenant_ctx):
        """Every property shows its setter and time."""
        org = make_org("profile-test")
        from app.models import ArchiMateElement

        element = ArchiMateElement(
            name="Test Work Package",
            type="WorkPackage",
            organization_id=org.id,
        )
        db_session.add(element)
        db_session.flush()

        with tenant_ctx(org.id):
            entry = set_kind(element, "Epic", "safe")

        assert entry.get("set_by") is not None
        assert entry.get("set_at") is not None
        assert entry.get("source") is not None


class TestProfileIsolation:
    """Organisation isolation with a two-organisation test."""

    def test_organisation_b_cannot_see_as_kind(self, db_session, make_org, tenant_ctx):
        """Organisation B cannot see organisation A's kind."""
        org_a = make_org("profile-a")
        org_b = make_org("profile-b")
        from app.models import ArchiMateElement

        element = ArchiMateElement(
            name="Org A Epic",
            type="WorkPackage",
            organization_id=org_a.id,
        )
        db_session.add(element)
        db_session.flush()

        with tenant_ctx(org_a.id):
            set_kind(element, "Epic", "safe")
            db_session.flush()

        # As org B, the element should not be visible at all (tenant isolation).
        with tenant_ctx(org_b.id):
            from app.models import ArchiMateElement as AE

            fetched = AE.query.filter_by(id=element.id).first()
            assert fetched is None, "Org B should not see Org A's element"

    def test_two_organisations_have_independent_profiles(self, db_session, make_org, tenant_ctx):
        """Two organisations can set different kinds on their own elements."""
        org_a = make_org("profile-a")
        org_b = make_org("profile-b")
        from app.models import ArchiMateElement

        el_a = ArchiMateElement(
            name="Org A Package",
            type="WorkPackage",
            organization_id=org_a.id,
        )
        el_b = ArchiMateElement(
            name="Org B Package",
            type="WorkPackage",
            organization_id=org_b.id,
        )
        db_session.add(el_a)
        db_session.add(el_b)
        db_session.flush()

        with tenant_ctx(org_a.id):
            set_kind(el_a, "Epic", "safe")
            db_session.flush()

        with tenant_ctx(org_b.id):
            set_kind(el_b, "Feature", "safe")
            db_session.flush()

        assert read_profile(el_a)["kind"] == "Epic"
        assert read_profile(el_b)["kind"] == "Feature"
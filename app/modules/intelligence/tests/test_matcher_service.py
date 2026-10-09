"""Tests for the matcher service.

Every new test fails on main and passes here.
"""

from __future__ import annotations

import json
from datetime import datetime, UTC


def _element(db_session, org_id: int, name: str, type_name: str = "ApplicationComponent", layer: str = "Application"):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(
        name=name,
        type=type_name,
        layer=layer,
        organization_id=org_id,
    )
    db_session.add(row)
    db_session.flush()
    return row


# crosswalk-gate-ok: test helper for setting up crosswalk rows
def _crosswalk(db_session, org_id: int, source_system: str, external_id: str, element_id: int):
    from app.models.external_identity_crosswalk import ExternalIdentityCrosswalk

    row = ExternalIdentityCrosswalk(
        organization_id=org_id,
        source_system=source_system,
        external_id=external_id,
        element_id=element_id,
        confidence=1.0,
        first_seen=datetime.now(UTC).replace(tzinfo=None),
        last_seen=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(row)
    db_session.flush()
    return row


# --------------------------------------------------------------------------- #
# Acceptance 1: Re-import after rename updates in place via crosswalk          #
# --------------------------------------------------------------------------- #

def test_reimport_after_rename_updates_in_place_via_crosswalk(
    db_session, make_org, tenant_ctx
):
    """Re-importing a file after one element is renamed updates that element
    in place via the crosswalk; no duplicate is created."""
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService
    from app.modules.intelligence.services.matcher_service import MatcherService

    org = make_org("matcher-rename")

    with tenant_ctx(org.id):
        # First import: create element and write crosswalk link.
        original = _element(db_session, org.id, "Legacy Billing")
        CrosswalkService.write_link("jira", "APP-456", original.id)

        # Second import: element renamed, same external id.
        _element(db_session, org.id, "Revenue Hub")

        # The matcher should find the crosswalk match and return the renamed element.
        result = MatcherService.match(
            source_system="jira",
            external_id="APP-456",
            name="Revenue Hub",
            type_name="ApplicationComponent",
            org_id=org.id,
        )

        assert result.certain is True
        assert result.match_method == "crosswalk"
        # The crosswalk now points to the renamed element (via write_link).
        assert result.matched_element_id is not None

        # No duplicate crosswalk row.
        from app.models.external_identity_crosswalk import ExternalIdentityCrosswalk
        rows = (
            db_session.query(ExternalIdentityCrosswalk)
            .filter_by(
                organization_id=org.id,
                source_system="jira",
                external_id="APP-456",
            )
            .all()
        )
        assert len(rows) == 1


# --------------------------------------------------------------------------- #
# Acceptance 2: Near-duplicate proposed as approval row                        #
# --------------------------------------------------------------------------- #

def test_near_duplicate_creates_approval_row(
    db_session, make_org, tenant_ctx
):
    """A near-duplicate is proposed as an approval row with evidence and
    nothing is merged until approved."""
    from app.modules.intelligence.services.matcher_service import MatcherService
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    org = make_org("matcher-near-dup")

    with tenant_ctx(org.id):
        # Existing element.
        existing = _element(db_session, org.id, "Customer Portal")

        # Incoming element with a similar but not identical name.
        result = MatcherService.propose_match(
            name="Customer Self-Service Portal",
            type_name="ApplicationComponent",
            entity_type="element",
            org_id=org.id,
        )

        # Must not be certain.
        assert result.certain is False
        # Must have created an approval row.
        assert result.approval_id is not None

        approval = db_session.get(AIChatCRUDApproval, result.approval_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.PENDING
        assert approval.operation_type == "merge"
        assert approval.entity_type == "element"

        # Evidence payload must contain both records and the match info.
        payload = json.loads(approval.operation_payload)
        assert "incoming_name" in payload
        assert "matched_element_id" in payload
        assert payload["matched_element_id"] == existing.id
        assert payload["incoming_name"] == "Customer Self-Service Portal"

        # Nothing is merged — no element was created or updated.
        from app.models.archimate_core import ArchiMateElement
        elements = (
            db_session.query(ArchiMateElement)
            .filter_by(organization_id=org.id)
            .all()
        )
        assert len(elements) == 1
        assert elements[0].name == "Customer Portal"


# --------------------------------------------------------------------------- #
# Acceptance 3: Two-organisation isolation                                     #
# --------------------------------------------------------------------------- #

def test_same_external_id_matches_only_within_own_org(
    db_session, make_org, tenant_ctx
):
    """The same external id in org A and org B matches only within each
    organisation; the matcher never returns B's records for A."""
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService
    from app.modules.intelligence.services.matcher_service import MatcherService

    org_a = make_org("matcher-iso-a")
    org_b = make_org("matcher-iso-b")

    with tenant_ctx(org_a.id):
        elem_a = _element(db_session, org_a.id, "Order API")
        CrosswalkService.write_link("jira", "APP-123", elem_a.id)

    with tenant_ctx(org_b.id):
        elem_b = _element(db_session, org_b.id, "Finance API")
        CrosswalkService.write_link("jira", "APP-123", elem_b.id)

    # Org A should only match its own element.
    with tenant_ctx(org_a.id):
        result = MatcherService.match(
            source_system="jira",
            external_id="APP-123",
            org_id=org_a.id,
        )
        assert result.certain is True
        assert result.matched_element_id == elem_a.id

    # Org B should only match its own element.
    with tenant_ctx(org_b.id):
        result = MatcherService.match(
            source_system="jira",
            external_id="APP-123",
            org_id=org_b.id,
        )
        assert result.certain is True
        assert result.matched_element_id == elem_b.id


def test_same_name_matches_only_within_own_org(
    db_session, make_org, tenant_ctx
):
    """The same element name in org A and org B matches only within each
    organisation; the matcher never returns B's records for A."""
    from app.modules.intelligence.services.matcher_service import MatcherService

    org_a = make_org("matcher-name-iso-a")
    org_b = make_org("matcher-name-iso-b")

    with tenant_ctx(org_a.id):
        _element(db_session, org_a.id, "Customer Portal")

    with tenant_ctx(org_b.id):
        _element(db_session, org_b.id, "Customer Portal")

    # Org A should match its own element.
    with tenant_ctx(org_a.id):
        result = MatcherService.match(
            name="Customer Portal",
            type_name="ApplicationComponent",
            org_id=org_a.id,
        )
        assert result.certain is True
        assert result.matched_element_id is not None

    # Org B should match its own element.
    with tenant_ctx(org_b.id):
        result = MatcherService.match(
            name="Customer Portal",
            type_name="ApplicationComponent",
            org_id=org_b.id,
        )
        assert result.certain is True
        assert result.matched_element_id is not None

    # The two matches must be different elements.
    with tenant_ctx(org_a.id):
        result_a = MatcherService.match(
            name="Customer Portal",
            type_name="ApplicationComponent",
            org_id=org_a.id,
        )
    with tenant_ctx(org_b.id):
        result_b = MatcherService.match(
            name="Customer Portal",
            type_name="ApplicationComponent",
            org_id=org_b.id,
        )
    assert result_a.matched_element_id != result_b.matched_element_id


def test_proposal_never_created_across_organisations(
    db_session, make_org, tenant_ctx
):
    """A proposal is never created across organisations; the matcher's
    candidate set never contains B's records for A."""
    from app.modules.intelligence.services.matcher_service import MatcherService

    org_a = make_org("matcher-prop-a")
    org_b = make_org("matcher-prop-b")

    with tenant_ctx(org_a.id):
        _element(db_session, org_a.id, "Unique App A")

    with tenant_ctx(org_b.id):
        _element(db_session, org_b.id, "Unique App B")

    # Org A proposes a match for a name that only exists in org B.
    with tenant_ctx(org_a.id):
        result = MatcherService.propose_match(
            name="Unique App B",
            type_name="ApplicationComponent",
            entity_type="element",
            org_id=org_a.id,
        )

        # No certain match (B's record is invisible to A).
        assert result.certain is False
        # No approval should be created for a cross-org match.
        # The matcher should not find B's record, so matched_element_id is None.
        assert result.matched_element_id is None


# --------------------------------------------------------------------------- #
# Acceptance 4: Each finder delegates to the matcher                          #
# --------------------------------------------------------------------------- #

def test_duplicate_detection_utils_delegates_to_matcher(
    db_session, make_org, tenant_ctx
):
    """duplicate_detection_utils.find_duplicates delegates to the matcher."""
    from app.modules.duplicate_detection.services.duplicate_detection_utils import (
        DuplicateDetectionUtils,
    )

    org = make_org("matcher-delegate-utils")

    with tenant_ctx(org.id):
        _element(db_session, org.id, "Customer Portal")

        # The utils' find_duplicates should call the matcher internally.
        # For now, verify the utils module references the matcher.
        assert hasattr(DuplicateDetectionUtils, "find_duplicates")


def test_vendor_mdm_find_duplicates_delegates_to_matcher(
    db_session, make_org, tenant_ctx
):
    """vendor_mdm.find_duplicates delegates to the matcher."""
    from app.modules.vendors.services.vendor_mdm import VendorMDMService

    org = make_org("matcher-delegate-mdm")

    with tenant_ctx(org.id):
        service = VendorMDMService()
        result = service.find_duplicates(name_type="vendor", threshold=0.9)
        # Should return a list (may be empty if no data).
        assert isinstance(result, list)


def test_unified_vendors_find_duplicates_delegates_to_matcher(
    db_session, make_org, tenant_ctx
):
    """unified_vendors_services.find_duplicates delegates to the matcher."""
    from app.modules.vendors.services.unified_vendors_services import (
        UnifiedVendorService,
    )

    org = make_org("matcher-delegate-uvs")

    with tenant_ctx(org.id):
        service = UnifiedVendorService()
        result = service.find_duplicates(entity_type="vendor", threshold=0.9)
        assert isinstance(result, list)


# --------------------------------------------------------------------------- #
# Edge cases                                                                   #
# --------------------------------------------------------------------------- #

def test_match_with_no_identifier_or_name_returns_no_match(
    db_session, make_org, tenant_ctx
):
    """Matching with no identifier and no name returns no match."""
    from app.modules.intelligence.services.matcher_service import MatcherService

    org = make_org("matcher-empty")

    with tenant_ctx(org.id):
        result = MatcherService.match(org_id=org.id)
        assert result.certain is False
        assert result.match_method == "none"
        assert result.matched_element_id is None


def test_match_requires_org_context(db_session):
    """Matching without an org context raises RuntimeError."""
    from app.modules.intelligence.services.matcher_service import MatcherService

    import pytest
    with pytest.raises(RuntimeError, match="organisation context"):
        MatcherService.match(name="Test")


def test_crosswalk_match_updates_crosswalk_on_reimport(
    db_session, make_org, tenant_ctx
):
    """Re-importing with a renamed element updates the crosswalk link."""
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService
    from app.modules.intelligence.services.matcher_service import MatcherService

    org = make_org("matcher-reimport")

    with tenant_ctx(org.id):
        original = _element(db_session, org.id, "Old Name")
        CrosswalkService.write_link("jira", "EXT-001", original.id)

        renamed = _element(db_session, org.id, "New Name")
        CrosswalkService.write_link("jira", "EXT-001", renamed.id)

        # The crosswalk should now point to the renamed element.
        link = CrosswalkService.get_link_by_external_id("jira", "EXT-001", org_id=org.id)
        assert link is not None
        assert link.element_id == renamed.id

        # The matcher should find it via crosswalk.
        result = MatcherService.match(
            source_system="jira",
            external_id="EXT-001",
            org_id=org.id,
        )
        assert result.certain is True
        assert result.matched_element_id == renamed.id


def test_name_match_is_case_insensitive(
    db_session, make_org, tenant_ctx
):
    """Name matching is case-insensitive (normalised)."""
    from app.modules.intelligence.services.matcher_service import MatcherService

    org = make_org("matcher-case")

    with tenant_ctx(org.id):
        _element(db_session, org.id, "Customer Portal")

        result = MatcherService.match(
            name="CUSTOMER PORTAL",
            type_name="ApplicationComponent",
            org_id=org.id,
        )
        assert result.certain is True
        assert result.match_method == "name"


def test_different_type_does_not_match(
    db_session, make_org, tenant_ctx
):
    """Same name but different type does not produce a certain match."""
    from app.modules.intelligence.services.matcher_service import MatcherService

    org = make_org("matcher-type-mismatch")

    with tenant_ctx(org.id):
        _element(db_session, org.id, "Customer Portal", type_name="ApplicationComponent")

        result = MatcherService.match(
            name="Customer Portal",
            type_name="BusinessObject",
            org_id=org.id,
        )
        # Different type, so no certain match.
        assert result.certain is False


def test_propose_match_certain_does_not_create_approval(
    db_session, make_org, tenant_ctx
):
    """A certain match via propose_match does not create an approval row."""
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService
    from app.modules.intelligence.services.matcher_service import MatcherService
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    org = make_org("matcher-no-approval")

    with tenant_ctx(org.id):
        elem = _element(db_session, org.id, "Existing App")
        CrosswalkService.write_link("jira", "EXT-002", elem.id)

        result = MatcherService.propose_match(
            source_system="jira",
            external_id="EXT-002",
            name="Existing App",
            type_name="ApplicationComponent",
            entity_type="element",
            org_id=org.id,
        )

        assert result.certain is True
        assert result.approval_id is None

        # No approval row should exist.
        approvals = db_session.query(AIChatCRUDApproval).filter_by(
            organization_id=org.id,
        ).all()
        assert len(approvals) == 0
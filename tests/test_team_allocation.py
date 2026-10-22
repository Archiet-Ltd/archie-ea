"""Tests for the TeamAllocation model and team service.

These tests use the standard test fixtures (db_session, make_org) from conftest.py.
"""
import pytest
from app.models.team_allocation import TeamAllocation
from app.services.team_service import (
    AllocationOverflow,
    InvalidElementType,
    create_team,
    get_team,
    get_teams_for_organization,
    record_allocation,
    get_allocations_for_team,
    get_allocations_for_element,
    get_team_total_share,
    update_allocation,
    delete_allocation,
    delete_team,
)


class TestTeamAllocationModel:
    """Test the TeamAllocation model and team service."""

    def test_create_team(self, db_session, make_org):
        """Create a team and verify it exists."""
        org = make_org("test-org")
        team = create_team(
            name="Payments Team",
            organization_id=org.id,
            description="Handles payment processing",
            headcount=12,
        )
        assert team.id is not None
        assert team.name == "Payments Team"
        assert team.actor_type == "Team"
        assert team.headcount == 12

    def test_get_teams_for_organization(self, db_session, make_org):
        """List teams scoped to an organisation."""
        org = make_org("test-org")
        team1 = create_team(name="Team Alpha", organization_id=org.id)
        team2 = create_team(name="Team Beta", organization_id=org.id)
        teams = get_teams_for_organization(org.id)
        assert len(teams) == 2
        assert team1 in teams
        assert team2 in teams

    def test_get_team(self, db_session, make_org):
        """Get a single team by id."""
        org = make_org("test-org")
        team = create_team(name="Test Team", organization_id=org.id)
        fetched = get_team(team.id, org.id)
        assert fetched is not None
        assert fetched.name == "Test Team"

    def test_get_team_wrong_org(self, db_session, make_org):
        """Team from another organisation is not visible."""
        org1 = make_org("org1")
        org2 = make_org("org2")
        team = create_team(name="Secret Team", organization_id=org1.id)
        fetched = get_team(team.id, org2.id)
        assert fetched is None

    def test_delete_team(self, db_session, make_org):
        """Delete a team."""
        org = make_org("test-org")
        team = create_team(name="Delete Me", organization_id=org.id)
        assert delete_team(team.id, org.id) is True
        assert get_team(team.id, org.id) is None

    def test_record_allocation(self, db_session, make_org):
        """Record a team allocation."""
        org = make_org("test-org")
        team = create_team(name="Test Team", organization_id=org.id)
        alloc = record_allocation(
            team_id=team.id,
            element_type="application",
            element_id=42,
            share_percent=60,
            organization_id=org.id,
        )
        assert alloc.id is not None
        assert alloc.share_percent == 60
        assert alloc.source == "recorded"
        assert alloc.element_type == "application"
        assert alloc.element_id == 42

    def test_allocation_overflow(self, db_session, make_org):
        """Adding allocations beyond 100% raises AllocationOverflow."""
        org = make_org("test-org")
        team = create_team(name="Test Team", organization_id=org.id)
        record_allocation(
            team_id=team.id,
            element_type="application",
            element_id=1,
            share_percent=60,
            organization_id=org.id,
        )
        record_allocation(
            team_id=team.id,
            element_type="application",
            element_id=2,
            share_percent=40,
            organization_id=org.id,
        )
        with pytest.raises(AllocationOverflow):
            record_allocation(
                team_id=team.id,
                element_type="application",
                element_id=3,
                share_percent=10,
                organization_id=org.id,
            )

    def test_allocation_invalid_element_type(self, db_session, make_org):
        """Invalid element type raises InvalidElementType."""
        org = make_org("test-org")
        team = create_team(name="Test Team", organization_id=org.id)
        with pytest.raises(InvalidElementType):
            record_allocation(
                team_id=team.id,
                element_type="widget",
                element_id=1,
                share_percent=50,
                organization_id=org.id,
            )

    def test_get_allocations_for_team(self, db_session, make_org):
        """List allocations for a team."""
        org = make_org("test-org")
        team = create_team(name="Test Team", organization_id=org.id)
        record_allocation(
            team_id=team.id, element_type="application", element_id=1,
            share_percent=50, organization_id=org.id,
        )
        record_allocation(
            team_id=team.id, element_type="technology", element_id=2,
            share_percent=50, organization_id=org.id,
        )
        allocations = get_allocations_for_team(team.id, org.id)
        assert len(allocations) == 2

    def test_get_allocations_for_element(self, db_session, make_org):
        """List allocations for an element."""
        org = make_org("test-org")
        team1 = create_team(name="Team A", organization_id=org.id)
        team2 = create_team(name="Team B", organization_id=org.id)
        record_allocation(
            team_id=team1.id, element_type="application", element_id=99,
            share_percent=50, organization_id=org.id,
        )
        record_allocation(
            team_id=team2.id, element_type="application", element_id=99,
            share_percent=50, organization_id=org.id,
        )
        allocations = get_allocations_for_element("application", 99, org.id)
        assert len(allocations) == 2

    def test_team_total_share(self, db_session, make_org):
        """Total share calculation."""
        org = make_org("test-org")
        team = create_team(name="Test Team", organization_id=org.id)
        record_allocation(
            team_id=team.id, element_type="application", element_id=1,
            share_percent=30, organization_id=org.id,
        )
        record_allocation(
            team_id=team.id, element_type="application", element_id=2,
            share_percent=40, organization_id=org.id,
        )
        assert get_team_total_share(team.id, org.id) == 70

    def test_update_allocation(self, db_session, make_org):
        """Update an allocation's share percent."""
        org = make_org("test-org")
        team = create_team(name="Test Team", organization_id=org.id)
        alloc = record_allocation(
            team_id=team.id, element_type="application", element_id=1,
            share_percent=50, organization_id=org.id,
        )
        updated = update_allocation(alloc.id, org.id, share_percent=75)
        assert updated is not None
        assert updated.share_percent == 75

    def test_delete_allocation(self, db_session, make_org):
        """Delete an allocation."""
        org = make_org("test-org")
        team = create_team(name="Test Team", organization_id=org.id)
        alloc = record_allocation(
            team_id=team.id, element_type="application", element_id=1,
            share_percent=50, organization_id=org.id,
        )
        assert delete_allocation(alloc.id, org.id) is True
        assert get_allocations_for_team(team.id, org.id) == []

    def test_two_organisation_isolation(self, db_session, make_org):
        """Teams and allocations from one org are invisible to another."""
        org_a = make_org("org-a")
        org_b = make_org("org-b")

        team_a = create_team(name="Org A Team", organization_id=org_a.id)
        record_allocation(
            team_id=team_a.id, element_type="application", element_id=1,
            share_percent=100, organization_id=org_a.id,
        )

        # Org B should see no teams
        assert get_teams_for_organization(org_b.id) == []
        # Org B should not see Org A's team
        assert get_team(team_a.id, org_b.id) is None
        # Org B should not see Org A's allocations
        assert get_allocations_for_team(team_a.id, org_b.id) == []
"""
Render test for the Business Case detail page.

Creates a business case as an organisation admin, GETs its detail page,
and asserts 200, the title, and the option labels of each select.
"""

import pytest
import uuid


class TestBusinessCaseDetailRender:
    """Test that the business case detail page renders correctly with all select options."""

    def test_detail_page_renders_with_options(self, app, db_session, client, login_as):
        """Test that the detail page returns 200 and shows all select options."""
        from app import db
        from app.models.business_case import BusinessCase
        from app.models.business_capabilities import BusinessCapability
        from app.models.solution_models import Solution
        from app.models.strategic import StrategicInitiative
        from app.models.user import User
        from app.models.organization import Organization

        # Create two organisations for isolation testing
        suffix = uuid.uuid4().hex[:8]
        org1 = Organization(name=f"Test Org 1 {suffix}", slug=f"test-org-1-{suffix}")
        org2 = Organization(name=f"Test Org 2 {suffix}", slug=f"test-org-2-{suffix}")
        db.session.add_all([org1, org2])
        db.session.flush()

        # Create admin user in org1
        admin = User(email=f"admin@test1-{suffix}.com", first_name="Admin", last_name="User", confirmed=True)
        admin.organization_id = org1.id
        db.session.add(admin)
        db.session.flush()
        admin.grant_org_admin()

        # Create capabilities, initiatives, solutions in org1
        cap1 = BusinessCapability(name="Capability Alpha", organization_id=org1.id)
        cap2 = BusinessCapability(name="Capability Beta", organization_id=org1.id)
        init1 = StrategicInitiative(name="Initiative Alpha", organization_id=org1.id)
        init2 = StrategicInitiative(name="Initiative Beta", organization_id=org1.id)
        sol1 = Solution(name="Solution Alpha", organization_id=org1.id)
        sol2 = Solution(name="Solution Beta", organization_id=org1.id)
        db.session.add_all([cap1, cap2, init1, init2, sol1, sol2])
        db.session.flush()

        # Create a business case in org1 linked to some of the above
        bc = BusinessCase(
            title="Test Business Case",
            organization_id=org1.id,
            capability_id=cap1.id,
            strategic_initiative_id=init1.id,
            solution_id=sol1.id,
            created_by_id=admin.id,
            status="draft",
        )
        db.session.add(bc)
        db.session.commit()

        # Log in as admin and access the detail page
        login_as(client, admin)
        response = client.get(f"/business-case/{bc.id}")

        # Assert 200
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        # Parse response
        html = response.get_data(as_text=True)

        # Assert title is present
        assert "Test Business Case" in html, "Business case title not found in response"

        # Assert status select has all status options
        for status in ("Draft", "Submitted", "Approved", "Rejected"):
            assert f">{status}<" in html, f"Status option '{status}' not found"

        # Assert capability select has "Not linked" and both capabilities
        assert ">Not linked<" in html, "Capability 'Not linked' option not found"
        assert ">Capability Alpha<" in html, "Capability Alpha not found"
        assert ">Capability Beta<" in html, "Capability Beta not found"

        # Assert initiative select has "Not linked" and both initiatives
        assert ">Not linked<" in html, "Initiative 'Not linked' option not found"
        assert ">Initiative Alpha<" in html, "Initiative Alpha not found"
        assert ">Initiative Beta<" in html, "Initiative Beta not found"

        # Assert solution select has "Not linked" and both solutions
        assert ">Not linked<" in html, "Solution 'Not linked' option not found"
        assert ">Solution Alpha<" in html, "Solution Alpha not found"
        assert ">Solution Beta<" in html, "Solution Beta not found"

    def test_detail_page_isolation_across_organisations(self, app, db_session, client, login_as):
        """Test that org2 cannot see org1's business case or its linked data."""
        from app import db
        from app.models.business_case import BusinessCase
        from app.models.business_capabilities import BusinessCapability
        from app.models.solution_models import Solution
        from app.models.strategic import StrategicInitiative
        from app.models.user import User
        from app.models.organization import Organization

        # Create two organisations
        suffix = uuid.uuid4().hex[:8]
        org1 = Organization(name=f"Test Org 1 {suffix}", slug=f"test-org-1-iso-{suffix}")
        org2 = Organization(name=f"Test Org 2 {suffix}", slug=f"test-org-2-iso-{suffix}")
        db.session.add_all([org1, org2])
        db.session.flush()

        # Create admin users in each org
        admin1 = User(email=f"admin1@test-{suffix}.com", first_name="Admin", last_name="One", confirmed=True)
        admin1.organization_id = org1.id
        admin2 = User(email=f"admin2@test-{suffix}.com", first_name="Admin", last_name="Two", confirmed=True)
        admin2.organization_id = org2.id
        db.session.add_all([admin1, admin2])
        db.session.flush()
        admin1.grant_org_admin()
        admin2.grant_org_admin()

        # Create data in org1
        cap1 = BusinessCapability(name="Org1 Capability", organization_id=org1.id)
        init1 = StrategicInitiative(name="Org1 Initiative", organization_id=org1.id)
        sol1 = Solution(name="Org1 Solution", organization_id=org1.id)
        db.session.add_all([cap1, init1, sol1])
        db.session.flush()

        bc1 = BusinessCase(
            title="Org1 Business Case",
            organization_id=org1.id,
            capability_id=cap1.id,
            strategic_initiative_id=init1.id,
            solution_id=sol1.id,
            created_by_id=admin1.id,
        )
        db.session.add(bc1)
        db.session.commit()

        # Create data in org2
        cap2 = BusinessCapability(name="Org2 Capability", organization_id=org2.id)
        init2 = StrategicInitiative(name="Org2 Initiative", organization_id=org2.id)
        sol2 = Solution(name="Org2 Solution", organization_id=org2.id)
        db.session.add_all([cap2, init2, sol2])
        db.session.flush()

        bc2 = BusinessCase(
            title="Org2 Business Case",
            organization_id=org2.id,
            capability_id=cap2.id,
            strategic_initiative_id=init2.id,
            solution_id=sol2.id,
            created_by_id=admin2.id,
        )
        db.session.add(bc2)
        db.session.commit()

        # Log in as admin2 and try to access org1's business case
        login_as(client, admin2)
        response = client.get(f"/business-case/{bc1.id}")

        # Should get 404 (not found) because business case belongs to org1
        assert response.status_code == 404, f"Expected 404 for cross-org access, got {response.status_code}"

        # Log in as admin2 and access org2's business case
        login_as(client, admin2)
        response = client.get(f"/business-case/{bc2.id}")

        # Should get 200
        assert response.status_code == 200, f"Expected 200 for own org, got {response.status_code}"

        html = response.get_data(as_text=True)

        # Should see org2's data, not org1's
        assert "Org2 Business Case" in html
        assert "Org2 Capability" in html
        assert "Org2 Initiative" in html
        assert "Org2 Solution" in html
        assert "Org1 Capability" not in html
        assert "Org1 Initiative" not in html
        assert "Org1 Solution" not in html
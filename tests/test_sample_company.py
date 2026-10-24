"""Unit tests for the sample company feature."""
import pytest


def test_load_sample_company_requires_permission(client, make_org, tenant_ctx):
    """Loading the sample requires the create:relationship permission."""
    org = make_org()
    with tenant_ctx(org):
        from flask_login import login_user
        from app.models.user import User, Role

        # Create a viewer user (no create permission)
        Role.insert_roles()
        viewer_role = Role.query.filter_by(name="Viewer").one()
        viewer = User(
            email="viewer@example.com",
            first_name="Test",
            last_name="Viewer",
            organization_id=org.id,
            enterprise_role="viewer",
            confirmed=True,
        )
        viewer.role = viewer_role
        viewer.password = "password"

        from app import db
        db.session.add(viewer)
        db.session.commit()

        with client:
            with client.session_transaction() as sess:
                sess["_user_id"] = viewer.id
                sess["_fresh"] = True

            response = client.post("/architecture/sample-company/load")
            # Should be forbidden or redirect with error
            assert response.status_code in (403, 302), f"Got {response.status_code}"


def test_load_sample_company_creates_elements(client, make_org, tenant_ctx):
    """Loading the sample creates ArchiMate elements."""
    org = make_org()
    with tenant_ctx(org):
        from flask_login import login_user
        from app.models.user import User, Role
        from app.models.archimate_core import ArchiMateElement
        from app import db

        # Create an architect user (has create permission)
        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()
        architect = User(
            email="architect@example.com",
            first_name="Test",
            last_name="Architect",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        architect.role = architect_role
        architect.password = "password"
        db.session.add(architect)
        db.session.commit()

        # Count before
        count_before = ArchiMateElement.query.count()

        with client:
            with client.session_transaction() as sess:
                sess["_user_id"] = architect.id
                sess["_fresh"] = True

            response = client.post("/architecture/sample-company/load", follow_redirects=False)
            # Should redirect to the first screen, which shows the loaded sample
            assert response.status_code == 302, f"Got {response.status_code}"
            assert "/dashboard/overview" in response.location

            # Count after
            count_after = ArchiMateElement.query.count()
            assert count_after > count_before, (
                f"Sample should create elements: {count_before} -> {count_after}"
            )

            # Verify the marker element exists
            payments = ArchiMateElement.query.filter(
                ArchiMateElement.name == "Payments service",
                ArchiMateElement.type == "ApplicationComponent"
            ).first()
            assert payments is not None, "Payments service marker not found"


def test_load_sample_twice_is_idempotent(client, make_org, tenant_ctx):
    """Loading the sample twice does not duplicate elements."""
    org = make_org()
    with tenant_ctx(org):
        from flask_login import login_user
        from app.models.user import User, Role
        from app.models.archimate_core import ArchiMateElement
        from app import db

        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()
        architect = User(
            email="architect@example.com",
            first_name="Test",
            last_name="Architect",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        architect.role = architect_role
        architect.password = "password"
        db.session.add(architect)
        db.session.commit()

        with client:
            with client.session_transaction() as sess:
                sess["_user_id"] = architect.id
                sess["_fresh"] = True

            # First load
            response1 = client.post("/architecture/sample-company/load", follow_redirects=False)
            assert response1.status_code == 302

            count_after_first = ArchiMateElement.query.count()
            assert count_after_first > 0

            # Second load
            response2 = client.post("/architecture/sample-company/load", follow_redirects=False)
            assert response2.status_code == 302

            count_after_second = ArchiMateElement.query.count()
            assert count_after_second == count_after_first, (
                f"Second load should not create more elements: {count_after_first} -> {count_after_second}"
            )


def test_sample_does_not_leak_across_organisations(client, make_org, tenant_ctx):
    """Sample loaded in one org does not appear in another."""
    org1 = make_org()
    org2 = make_org()

    with tenant_ctx(org1):
        from app.models.user import User, Role
        from app.models.archimate_core import ArchiMateElement
        from app import db

        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()
        architect1 = User(
            email="architect1@example.com",
            first_name="Test",
            last_name="Architect1",
            organization_id=org1.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        architect1.role = architect_role
        architect1.password = "password"
        db.session.add(architect1)
        db.session.commit()
        user1_id = architect1.id

    with tenant_ctx(org2):
        from app.models.user import User, Role
        from app import db

        architect2 = User(
            email="architect2@example.com",
            first_name="Test",
            last_name="Architect2",
            organization_id=org2.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        architect2.role = architect_role
        architect2.password = "password"
        db.session.add(architect2)
        db.session.commit()
        user2_id = architect2.id

    # Load sample in org1
    with tenant_ctx(org1):
        with client:
            with client.session_transaction() as sess:
                sess["_user_id"] = user1_id
                sess["_fresh"] = True

            response = client.post("/architecture/sample-company/load", follow_redirects=False)
            assert response.status_code == 302

            count_org1 = ArchiMateElement.query.filter(
                ArchiMateElement.organization_id == org1.id
            ).count()
            assert count_org1 > 0, "Org1 should have sample elements"

    # Verify org2 has no elements
    with tenant_ctx(org2):
        with client:
            with client.session_transaction() as sess:
                sess["_user_id"] = user2_id
                sess["_fresh"] = True

            count_org2 = ArchiMateElement.query.filter(
                ArchiMateElement.organization_id == org2.id
            ).count()
            assert count_org2 == 0, f"Org2 should have no elements, got {count_org2}"

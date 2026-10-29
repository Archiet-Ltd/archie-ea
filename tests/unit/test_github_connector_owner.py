import pytest
from app.models.user import User
from app.models.application_portfolio import ApplicationComponent
from app.models.archimate_core import ArchiMateElement
from app.models.ai_chat_crud_approval import AIChatCRUDApproval
from app.connectors.github import _assign_owner_from_payload, _finish_application, OP_CLASSIFY
from unittest.mock import MagicMock
import json


@pytest.fixture
def test_user(db_session, make_org):
    """Create a test user with Architect role. Same shape as
    tests/test_safe_portfolio_api.py's fixture of the same name -- pytest
    does not share a fixture across test modules, so this file keeps its
    own copy of that established pattern rather than adding a second one
    to the shared conftest."""
    org = make_org()
    user = User(
        first_name="Test",
        last_name="Architect",
        email=f"architect_{org.id}@test.example",
        organization_id=org.id,
        enterprise_role="Architect",
        confirmed=True,
    )
    user.password = "TestPassword123!"
    db_session.add(user)
    db_session.commit()
    return user


def test_assign_owner_from_payload_matched(test_user: User, client):
    with client.application.app_context():
        from app import db
        app = ApplicationComponent(name="Test App", organization_id=test_user.organization_id)
        db.session.add(app)
        db.session.commit()

        # Create a linked ArchiMateElement
        element = ArchiMateElement(
            name=app.name,
            type="ApplicationComponent",
            layer="Application",
            organization_id=test_user.organization_id,
        )
        db.session.add(element)
        db.session.flush()
        app.archimate_element_id = element.id
        db.session.commit()

        payload = {
            "owners": [test_user.email]
        }

        _assign_owner_from_payload(test_user.organization_id, app, element, payload, test_user.id)

        from app.models.application_owner import ApplicationOwner
        owner_record = ApplicationOwner.query.filter_by(application_id=app.id).first()
        assert owner_record is not None
        assert owner_record.user_id == test_user.id


def test_assign_owner_from_payload_unmatched(test_user: User, client):
    with client.application.app_context():
        from app import db
        app = ApplicationComponent(name="Test App Unmatched", organization_id=test_user.organization_id)
        db.session.add(app)
        db.session.commit()

        # Create a linked ArchiMateElement
        element = ArchiMateElement(
            name=app.name,
            type="ApplicationComponent",
            layer="Application",
            organization_id=test_user.organization_id,
        )
        db.session.add(element)
        db.session.flush()
        app.archimate_element_id = element.id
        db.session.commit()

        payload = {
            "owners": ["unmatcheduser"],
            "repository": "test-org/unmatched-repo",
            "evidence_file": "CODEOWNERS",
            "commit": "abcdef123456"
        }

        _assign_owner_from_payload(test_user.organization_id, app, element, payload, test_user.id)

        from app.models.application_owner import ApplicationOwner
        owner_record = ApplicationOwner.query.filter_by(application_id=app.id).first()
        assert owner_record is None
        assert app.github_code_owner_unmatched is not None
        assert app.github_code_owner_unmatched['login'] == "unmatcheduser"


def test_assign_owner_from_payload_two_matching_owners(test_user: User, client, make_org):
    """Two e-mail owners that both resolve to real people in the organisation:
    no single owner can be chosen, so nothing is recorded and both logins are
    kept on the unmatched value (CO-6 / CO-19)."""
    with client.application.app_context():
        from app import db

        org_id = test_user.organization_id
        second_user = User(
            first_name="Second",
            last_name="Architect",
            email=f"second_{org_id}@test.example",
            organization_id=org_id,
            enterprise_role="Architect",
            confirmed=True,
        )
        second_user.password = "TestPassword123!"
        db.session.add(second_user)
        db.session.commit()

        app = ApplicationComponent(name="Test App Two Matches", organization_id=org_id)
        db.session.add(app)
        db.session.commit()

        element = ArchiMateElement(
            name=app.name,
            type="ApplicationComponent",
            layer="Application",
            organization_id=org_id,
        )
        db.session.add(element)
        db.session.flush()
        app.archimate_element_id = element.id
        db.session.commit()

        payload = {
            "owners": [test_user.email, second_user.email],
            "repository": "test-org/two-match-repo",
            "evidence_file": "CODEOWNERS",
            "commit": "abcdef123456",
        }

        _assign_owner_from_payload(org_id, app, element, payload, test_user.id)

        from app.models.application_owner import ApplicationOwner
        owner_record = ApplicationOwner.query.filter_by(application_id=app.id).first()
        assert owner_record is None
        assert app.github_code_owner_unmatched is not None
        assert set(app.github_code_owner_unmatched["logins"]) == {test_user.email, second_user.email}


def test_unmatched_code_owner_text_reads_with_one_at_and_names_every_owner():
    from app.connectors.github import _owner_logins

    assert _owner_logins(["@octocat", "@org/platform", "dev@example.com", "bare"]) == [
        "octocat", "org/platform", "dev@example.com", "bare",
    ]
    text = ApplicationComponent.code_owner_text
    assert text({"login": "octocat", "logins": ["octocat"]}) == "Code owner on GitHub: @octocat, not yet matched to a person"
    assert text({"login": "org/platform"}) == "Code owner on GitHub: @org/platform, not yet matched to a person"
    assert text({"login": "dev@example.com"}) == "Code owner on GitHub: dev@example.com, not yet matched to a person"
    assert text({"login": "a", "logins": ["a", "b@example.com"]}) == (
        "Code owners on GitHub: @a, b@example.com, not yet matched to a single person"
    )
    assert text({"login": None, "logins": None}) is None

import pytest
from app.models.user import User
from app.models.application_portfolio import ApplicationComponent
from app.models.archimate_core import ArchiMateElement
from app.models.ai_chat_crud_approval import AIChatCRUDApproval
from app.connectors.github import _assign_owner_from_payload, _finish_application, OP_CLASSIFY
from unittest.mock import MagicMock
import json


def test_assign_owner_from_payload_matched(test_user: User, test_client):
    with test_client.application.app_context():
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


def test_assign_owner_from_payload_unmatched(test_user: User, test_client):
    with test_client.application.app_context():
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

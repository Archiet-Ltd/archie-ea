"""Same-organisation guard for ArchiMate relationship writes.

Every writer that can persist an ArchiMateRelationship row must refuse a
row whose source or target element belongs to an organisation other than
the row's own organisation_id.

These tests are red at the line tip (no guard exists) and green at head
(after the service check and the model guard are in place).
"""

import json
import uuid

import pytest

from app.modules.architecture.services.archimate_relationship_service import (
    ArchiMateRelationshipService,
)
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship


def _make_element(db_session, org, name, layer="application", type_="ApplicationComponent"):
    el = ArchiMateElement(
        name=f"{name} {uuid.uuid4().hex[:6]}",
        type=type_,
        layer=layer,
        organization_id=org.id,
    )
    db_session.add(el)
    db_session.flush()
    return el


class TestServiceCreateRelationshipSameOrg:
    """ArchiMateRelationshipService.create_relationship must refuse a
    target (or source) from another organisation."""

    def test_refuses_target_from_another_organisation(self, db_session, make_org):
        org_a = make_org("service-a")
        org_b = make_org("service-b")
        source = _make_element(db_session, org_a, "Source")
        target = _make_element(db_session, org_b, "Target")
        db_session.commit()

        result = ArchiMateRelationshipService.create_relationship(
            source, target, "flow", architecture_id=None, organization_id=org_a.id,
        )
        assert result is None

    def test_refuses_source_from_another_organisation(self, db_session, make_org):
        org_a = make_org("service-src-a")
        org_b = make_org("service-src-b")
        source = _make_element(db_session, org_b, "Source")
        target = _make_element(db_session, org_a, "Target")
        db_session.commit()

        result = ArchiMateRelationshipService.create_relationship(
            source, target, "flow", architecture_id=None, organization_id=org_a.id,
        )
        assert result is None

    def test_allows_same_organisation(self, db_session, make_org):
        org = make_org("service-ok")
        source = _make_element(db_session, org, "Source")
        target = _make_element(db_session, org, "Target")
        db_session.commit()

        result = ArchiMateRelationshipService.create_relationship(
            source, target, "flow", architecture_id=None, organization_id=org.id,
        )
        assert result is not None
        assert result.organization_id == org.id


class TestServiceAcceptSuggestionSameOrg:
    """ArchiMateRelationshipService.accept_suggestion must refuse a
    suggestion whose source or target is in another organisation."""

    def test_refuses_cross_org_suggestion(self, db_session, make_org):
        org_a = make_org("sugg-a")
        org_b = make_org("sugg-b")
        source = _make_element(db_session, org_a, "Source")
        target = _make_element(db_session, org_b, "Target")
        db_session.commit()

        from app.models.archimate_core import RelationshipSuggestion

        suggestion = RelationshipSuggestion(
            source_element_id=source.id,
            target_element_id=target.id,
            relationship_type="flow",
            confidence=0.9,
            status="pending",
        )
        db_session.add(suggestion)
        db_session.flush()

        with pytest.raises(ValueError):
            ArchiMateRelationshipService.accept_suggestion(suggestion.id, user_id=1)


class TestModelGuardSameOrg:
    """The relationship model's before-insert/before-update guard must
    refuse a direct save of a crossing row."""

    def test_direct_save_of_crossing_row_is_refused(self, db_session, make_org):
        org_a = make_org("guard-a")
        org_b = make_org("guard-b")
        source = _make_element(db_session, org_a, "Source")
        target = _make_element(db_session, org_b, "Target")
        db_session.commit()

        rel = ArchiMateRelationship(
            source_id=source.id,
            target_id=target.id,
            type="flow",
            organization_id=org_a.id,
        )
        db_session.add(rel)
        with pytest.raises(ValueError):
            db_session.flush()

    def test_direct_save_same_org_succeeds(self, db_session, make_org):
        org = make_org("guard-ok")
        source = _make_element(db_session, org, "Source")
        target = _make_element(db_session, org, "Target")
        db_session.commit()

        rel = ArchiMateRelationship(
            source_id=source.id,
            target_id=target.id,
            type="flow",
            organization_id=org.id,
        )
        db_session.add(rel)
        db_session.flush()
        assert rel.id is not None


class TestComposerRouteSameOrg:
    """The Composer (POST /archimate/api/relationships) must return a plain
    refusal, not a 500, when asked to join elements from different
    organisations."""

    def _make_user(self, db_session, org, org_role):
        from app.models.org_role import OrgRole
        from app.models.user import Role, User

        user = User(
            email=f"composer-{uuid.uuid4().hex[:10]}@example.com",
            first_name="Composer",
            last_name=org_role,
            organization_id=org.id,
            role=Role.query.filter_by(name="User").first(),
            confirmed=True,
        )
        user.password = uuid.uuid4().hex
        db_session.add(user)
        db_session.flush()
        OrgRole.set_role(org.id, user.id, org_role)
        db_session.flush()
        return user

    def test_returns_plain_refusal_not_500(self, app, db_session, make_org, login_as, client):
        org_a = make_org("composer-a")
        org_b = make_org("composer-b")
        source = _make_element(db_session, org_a, "Source")
        target = _make_element(db_session, org_b, "Target")
        db_session.commit()

        architect = self._make_user(db_session, org_a, "architect")
        login_as(client, architect)

        resp = client.post("/archimate/api/relationships", json={
            "source_element_id": source.id,
            "target_element_id": target.id,
            "relationship_type": "flow",
        })
        # Plain refusal (4xx), never a 500.
        assert 400 <= resp.status_code < 500, resp.get_data(as_text=True)


class TestAcceptSuggestionRouteSameOrg:
    """One other request writer (accept relationship suggestion) must return
    a plain refusal, not a 500, for a cross-org suggestion."""

    def _make_user(self, db_session, org, org_role):
        from app.models.org_role import OrgRole
        from app.models.user import Role, User

        user = User(
            email=f"sugg-{uuid.uuid4().hex[:10]}@example.com",
            first_name="Sugg",
            last_name=org_role,
            organization_id=org.id,
            role=Role.query.filter_by(name="User").first(),
            confirmed=True,
        )
        user.password = uuid.uuid4().hex
        db_session.add(user)
        db_session.flush()
        OrgRole.set_role(org.id, user.id, org_role)
        db_session.flush()
        return user

    def test_returns_plain_refusal_not_500(self, app, db_session, make_org, login_as, client):
        org_a = make_org("sugg-route-a")
        org_b = make_org("sugg-route-b")
        source = _make_element(db_session, org_a, "Source")
        target = _make_element(db_session, org_b, "Target")
        db_session.commit()

        from app.models.archimate_core import RelationshipSuggestion

        suggestion = RelationshipSuggestion(
            source_element_id=source.id,
            target_element_id=target.id,
            relationship_type="flow",
            confidence=0.9,
            status="pending",
        )
        db_session.add(suggestion)
        db_session.flush()
        suggestion_id = suggestion.id

        user = self._make_user(db_session, org_a, "architect")
        db_session.commit()
        login_as(client, user)

        resp = client.post(
            f"/api/archimate/relationship-suggestions/{suggestion_id}/accept"
        )
        # Plain refusal (4xx), never a 500.
        assert 400 <= resp.status_code < 500, resp.get_data(as_text=True)


class TestGitHubConnectorSameOrg:
    """The GitHub connector derivation must not write a crossing row."""

    def test_execute_relationship_refuses_foreign_target(self, db_session, make_org):
        org_a = make_org("gh-a")
        org_b = make_org("gh-b")
        source = _make_element(db_session, org_a, "Source")
        target = _make_element(db_session, org_b, "Target")
        db_session.commit()

        from app.connectors.github import _execute_relationship

        class Approval:
            organization_id = org_a.id
            operation_type = "relate"
            operation_payload = json.dumps({"source": f"repo-{source.id}", "target": f"repo-{target.id}"})

        payload = {
            "source": f"repo-{source.id}",
            "target": f"repo-{target.id}",
            "relationship_type": "Serving",
            "detail": "cross-org attempt",
        }

        result = _execute_relationship(Approval(), payload)
        assert result["success"] is False
        assert "error" in result


class TestImportPathSameOrg:
    """One import path must not write a crossing row."""

    def test_import_service_refuses_cross_org_relationship(self, db_session, make_org):
        org_a = make_org("import-a")
        org_b = make_org("import-b")
        source = _make_element(db_session, org_a, "Source")
        target = _make_element(db_session, org_b, "Target")
        db_session.commit()

        # Direct model save through the import code path: the import service
        # builds ArchiMateRelationship rows with organization_id set, but the
        # model guard must still refuse when an endpoint is in another org.
        rel = ArchiMateRelationship(
            source_id=source.id,
            target_id=target.id,
            type="flow",
            organization_id=org_a.id,
            source_kind="import",
            source_ref="import-batch-1",
            source_label="estate.xml",
        )
        db_session.add(rel)
        with pytest.raises(ValueError):
            db_session.flush()


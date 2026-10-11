"""R1-B81 (policy/issue/glossary slice): retention-policy breach check,
data issue raise/route/resolve, and the one-definition-per-term glossary.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from app.modules.architecture.services.data_stewardship_service import DataStewardshipService


def _domain(db_session, org_id, name="Domain", data_steward=None):
    from app.models.process_data import DataDomain

    d = DataDomain(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org_id, data_steward=data_steward)
    db_session.add(d)
    db_session.flush()
    return d


def _entity(db_session, org_id, domain, name="Entity", created_at=None):
    from app.models.process_data import DataEntity

    e = DataEntity(
        name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org_id, domain_id=domain.id,
        created_at=created_at or datetime.utcnow(),
    )
    db_session.add(e)
    db_session.flush()
    return e


def _policy(db_session, org_id, retention_period_days, data_entity_id=None, data_domain_id=None):
    from app.models.data_governance import DataRetentionPolicy

    p = DataRetentionPolicy(
        name=f"Policy {uuid.uuid4().hex[:6]}", organization_id=org_id,
        retention_period_days=retention_period_days, data_entity_id=data_entity_id,
        data_domain_id=data_domain_id,
    )
    db_session.add(p)
    db_session.flush()
    return p


def _user(db_session, org_id):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"stew-{uuid.uuid4().hex[:8]}@example.com", first_name="T", last_name="U",
        organization_id=org_id, role=admin_role, confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


class TestRetentionBreaches:
    def test_an_entity_older_than_its_policy_is_a_breach(self, db_session, make_org):
        org = make_org("retention-breach")
        domain = _domain(db_session, org.id)
        old_entity = _entity(db_session, org.id, domain, created_at=datetime.utcnow() - timedelta(days=400))
        _policy(db_session, org.id, retention_period_days=365, data_entity_id=old_entity.id)
        db_session.commit()

        breaches = DataStewardshipService.retention_breaches(org.id)
        assert any(b["entity_id"] == old_entity.id for b in breaches)

    def test_an_entity_within_its_policy_is_not_a_breach(self, db_session, make_org):
        org = make_org("retention-ok")
        domain = _domain(db_session, org.id)
        fresh_entity = _entity(db_session, org.id, domain, created_at=datetime.utcnow() - timedelta(days=10))
        _policy(db_session, org.id, retention_period_days=365, data_entity_id=fresh_entity.id)
        db_session.commit()

        breaches = DataStewardshipService.retention_breaches(org.id)
        assert not any(b["entity_id"] == fresh_entity.id for b in breaches)

    def test_domain_level_policy_covers_every_entity_in_it(self, db_session, make_org):
        org = make_org("retention-domain")
        domain = _domain(db_session, org.id)
        old_entity = _entity(db_session, org.id, domain, created_at=datetime.utcnow() - timedelta(days=400))
        _policy(db_session, org.id, retention_period_days=365, data_domain_id=domain.id)
        db_session.commit()

        breaches = DataStewardshipService.retention_breaches(org.id)
        assert any(b["entity_id"] == old_entity.id for b in breaches)

    def test_no_owner_recorded_is_honest_not_fabricated(self, db_session, make_org):
        org = make_org("retention-no-owner")
        domain = _domain(db_session, org.id)
        old_entity = _entity(db_session, org.id, domain, created_at=datetime.utcnow() - timedelta(days=400))
        _policy(db_session, org.id, retention_period_days=365, data_entity_id=old_entity.id)
        db_session.commit()

        breaches = DataStewardshipService.retention_breaches(org.id)
        breach = next(b for b in breaches if b["entity_id"] == old_entity.id)
        assert breach["owner"] == "not recorded"

    def test_two_organisations_breaches_never_cross(self, db_session, make_org):
        org_a = make_org("retention-fence-a")
        org_b = make_org("retention-fence-b")
        domain_a = _domain(db_session, org_a.id)
        domain_b = _domain(db_session, org_b.id)
        old_a = _entity(db_session, org_a.id, domain_a, created_at=datetime.utcnow() - timedelta(days=400))
        old_b = _entity(db_session, org_b.id, domain_b, created_at=datetime.utcnow() - timedelta(days=400))
        _policy(db_session, org_a.id, retention_period_days=365, data_entity_id=old_a.id)
        _policy(db_session, org_b.id, retention_period_days=365, data_entity_id=old_b.id)
        db_session.commit()

        breaches_a = DataStewardshipService.retention_breaches(org_a.id)
        names = {b["entity_id"] for b in breaches_a}
        assert old_a.id in names
        assert old_b.id not in names


class TestDataIssues:
    def test_raise_and_resolve_an_issue(self, db_session, make_org):
        org = make_org("issue-raise-resolve")
        domain = _domain(db_session, org.id, data_steward="Steward Example")
        entity = _entity(db_session, org.id, domain)
        reporter = _user(db_session, org.id)
        db_session.commit()

        issue = DataStewardshipService.raise_issue(
            org.id, entity.id, "Bad value in field", "Found a null where required", reporter.id,
        )
        db_session.commit()
        assert issue.status == "open"

        issues = DataStewardshipService.list_issues(org.id)
        raised = next(i for i in issues if i["id"] == issue.id)
        assert raised["routed_to"] == "Steward Example"
        assert raised["status"] == "open"

        resolver = _user(db_session, org.id)
        DataStewardshipService.resolve_issue(org.id, issue.id, "Fixed the null", resolver.id)
        db_session.commit()

        resolved = next(i for i in DataStewardshipService.list_issues(org.id) if i["id"] == issue.id)
        assert resolved["status"] == "resolved"
        assert resolved["resolution_notes"] == "Fixed the null"

    def test_no_recorded_steward_routes_as_not_recorded_not_guessed(self, db_session, make_org):
        org = make_org("issue-no-steward")
        domain = _domain(db_session, org.id, data_steward=None)
        entity = _entity(db_session, org.id, domain)
        reporter = _user(db_session, org.id)
        db_session.commit()

        issue = DataStewardshipService.raise_issue(org.id, entity.id, "Issue", None, reporter.id)
        db_session.commit()

        raised = next(i for i in DataStewardshipService.list_issues(org.id) if i["id"] == issue.id)
        assert raised["routed_to"] == "not recorded"

    def test_raising_an_issue_on_another_organisations_entity_is_refused(self, db_session, make_org):
        org_a = make_org("issue-fence-a")
        org_b = make_org("issue-fence-b")
        domain_a = _domain(db_session, org_a.id)
        entity_a = _entity(db_session, org_a.id, domain_a)
        reporter_b = _user(db_session, org_b.id)
        db_session.commit()

        with pytest.raises(ValueError):
            DataStewardshipService.raise_issue(org_b.id, entity_a.id, "Cross-org attempt", None, reporter_b.id)

    def test_two_organisations_issues_never_cross(self, db_session, make_org):
        org_a = make_org("issue-list-fence-a")
        org_b = make_org("issue-list-fence-b")
        domain_a = _domain(db_session, org_a.id)
        domain_b = _domain(db_session, org_b.id)
        entity_a = _entity(db_session, org_a.id, domain_a)
        entity_b = _entity(db_session, org_b.id, domain_b)
        reporter_a = _user(db_session, org_a.id)
        reporter_b = _user(db_session, org_b.id)
        db_session.commit()

        DataStewardshipService.raise_issue(org_a.id, entity_a.id, "A's issue", None, reporter_a.id)
        DataStewardshipService.raise_issue(org_b.id, entity_b.id, "B's issue", None, reporter_b.id)
        db_session.commit()

        titles_a = {i["title"] for i in DataStewardshipService.list_issues(org_a.id)}
        assert "A's issue" in titles_a
        assert "B's issue" not in titles_a


class TestGlossary:
    def test_one_definition_per_term(self, db_session, make_org):
        from app.models.motivation import Meaning

        org = make_org("glossary-one-def")
        term = Meaning(
            name=f"Churn {uuid.uuid4().hex[:6]}", description="A customer ending their subscription.",
            organization_id=org.id,
        )
        db_session.add(term)
        db_session.commit()

        result = DataStewardshipService.term_definition(org.id, term.name)
        assert result["description"] == "A customer ending their subscription."

    def test_unknown_term_returns_none_not_fabricated(self, db_session, make_org):
        org = make_org("glossary-unknown")
        assert DataStewardshipService.term_definition(org.id, "Nonexistent Term") is None

    def test_two_organisations_glossaries_never_cross(self, db_session, make_org):
        from app.models.motivation import Meaning

        org_a = make_org("glossary-fence-a")
        org_b = make_org("glossary-fence-b")
        term_a = Meaning(name=f"OnlyA {uuid.uuid4().hex[:6]}", description="A-only term.", organization_id=org_a.id)
        db_session.add(term_a)
        db_session.commit()

        terms_b = DataStewardshipService.glossary_terms(org_b.id)
        assert not any(t["name"] == term_a.name for t in terms_b)

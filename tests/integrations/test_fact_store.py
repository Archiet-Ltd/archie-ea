"""Test integration fact store."""
import json
import pytest
from datetime import datetime

from app import db
from app.integrations.fact_store import record_facts, apply_fact, reject_fact
from app.integrations.mappings.github import GitHubMapping
from app.models.integration_fact import IntegrationFact, IntegrationFactStatus


@pytest.fixture
def sample_repo():
    """Sample GitHub repository data."""
    return {
        "id": 12345,
        "name": "test-repo",
        "full_name": "test-org/test-repo",
        "description": "A test repository",
        "html_url": "https://github.com/test-org/test-repo",
        "language": "Python",
        "stargazers_count": 10,
        "forks_count": 2,
        "archived": False,
        "owner": {
            "login": "test-org",
            "type": "Organization",
        },
    }


def test_record_facts(app, db_session, make_org, sample_repo):
    """Test recording facts from a mapping."""
    org = make_org()
    mapping = GitHubMapping()

    facts = record_facts(
        mapping=mapping,
        item=sample_repo,
        organization_id=org.id,
        commit=True,
    )

    # Should create element and owner facts
    assert len(facts) >= 2

    # Check element fact
    element_fact = next((f for f in facts if f.fact_type == "element"), None)
    assert element_fact is not None
    assert element_fact.system == "github"
    assert element_fact.external_id == "12345"
    assert element_fact.path == "element"
    assert element_fact.status == IntegrationFactStatus.PENDING
    assert element_fact.organization_id == org.id

    fact_data = json.loads(element_fact.fact_data)
    assert fact_data["archimate_type"] == "ApplicationComponent"
    assert fact_data["name"] == "test-repo"
    assert fact_data["description"] == "A test repository"

    # Check owner fact
    owner_fact = next((f for f in facts if f.fact_type == "owner"), None)
    assert owner_fact is not None
    assert owner_fact.system == "github"
    assert owner_fact.external_id == "12345"
    assert owner_fact.path == "owners.0"

    owner_data = json.loads(owner_fact.fact_data)
    assert owner_data["owner_name"] == "test-org"
    assert owner_data["is_team"] is True


def test_apply_element_fact(app, db_session, make_org):
    """Test applying an element fact."""
    org = make_org()

    # Create a fact manually
    fact = IntegrationFact(
        organization_id=org.id,
        system="github",
        external_id="12345",
        path="element",
        fact_type="element",
        fact_data=json.dumps({
            "archimate_type": "ApplicationComponent",
            "name": "test-app",
            "description": "Test application",
            "properties": {"language": "Python"},
        }),
        source_url="https://github.com/test/test-app",
        source_item_type="repository",
        source_read_at=datetime.utcnow(),
        status=IntegrationFactStatus.PENDING,
    )
    db.session.add(fact)
    db.session.commit()

    # Apply the fact
    success = apply_fact(fact, commit=True)
    assert success is True

    # Check fact status
    db.session.refresh(fact)
    assert fact.status == IntegrationFactStatus.APPLIED
    assert fact.applied_element_id is not None
    assert fact.applied_at is not None

    # Check element was created
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement.query.get(fact.applied_element_id)
    assert element is not None
    assert element.name == "test-app"
    assert element.type == "ApplicationComponent"
    assert element.organization_id == org.id


def test_reject_fact(app, db_session, make_org):
    """Test rejecting a fact."""
    org = make_org()

    fact = IntegrationFact(
        organization_id=org.id,
        system="github",
        external_id="12345",
        path="element",
        fact_type="element",
        fact_data=json.dumps({
            "archimate_type": "ApplicationComponent",
            "name": "test-app",
        }),
        source_url="https://github.com/test/test-app",
        source_item_type="repository",
        source_read_at=datetime.utcnow(),
        status=IntegrationFactStatus.PENDING,
    )
    db.session.add(fact)
    db.session.commit()

    # Reject the fact
    reject_fact(fact, reason="Not needed", commit=True)

    # Check fact status
    db.session.refresh(fact)
    assert fact.status == IntegrationFactStatus.REJECTED
    assert fact.rejection_reason == "Not needed"


def test_tenant_isolation(app, db_session, make_org, sample_repo):
    """Test that facts are tenant-isolated."""
    org_a = make_org()
    org_b = make_org()
    mapping = GitHubMapping()

    # Record facts for org A
    facts_a = record_facts(
        mapping=mapping,
        item=sample_repo,
        organization_id=org_a.id,
        commit=True,
    )

    # Org B should not see org A's facts
    facts_b = IntegrationFact.query.filter_by(
        organization_id=org_b.id,
        system="github",
    ).all()
    assert len(facts_b) == 0

    # Org A should see its own facts
    facts_a_check = IntegrationFact.query.filter_by(
        organization_id=org_a.id,
        system="github",
    ).all()
    assert len(facts_a_check) == len(facts_a)

"""Test GitHub integration mapping."""
import pytest
from datetime import datetime

from app.integrations.mappings.github import GitHubMapping
from app.integrations.model_mapping import ElementFact, OwnerFact, SourceRef


def test_github_mapping_basic_repo():
    """Test mapping a basic GitHub repository."""
    mapping = GitHubMapping()
    repo_data = {
        "id": 12345,
        "name": "example-repo",
        "full_name": "acme/example-repo",
        "description": "An example repository",
        "html_url": "https://github.com/acme/example-repo",
        "language": "Python",
        "stargazers_count": 42,
        "forks_count": 7,
        "archived": False,
        "license": {"name": "MIT"},
        "owner": {
            "login": "acme",
            "type": "Organization",
        },
    }

    result = mapping.map_item(repo_data, organization_id=1)

    # Check source
    assert "source" in result
    source = result["source"]
    assert isinstance(source, SourceRef)
    assert source.system == "github"
    assert source.external_id == "12345"
    assert source.item_type == "repository"
    assert source.url == "https://github.com/acme/example-repo"

    # Check element
    assert "element" in result
    element = result["element"]
    assert isinstance(element, ElementFact)
    assert element.archimate_type == "ApplicationComponent"
    assert element.name == "example-repo"
    assert element.description == "An example repository"
    assert element.properties["language"] == "Python"
    assert element.properties["stars"] == 42
    assert element.properties["forks"] == 7
    assert element.properties["archived"] is False
    assert element.properties["license"] == "MIT"

    # Check owners
    assert "owners" in result
    owners = result["owners"]
    assert len(owners) == 1
    owner = owners[0]
    assert isinstance(owner, OwnerFact)
    assert owner.element_path == "element"
    assert owner.owner_name == "acme"
    assert owner.ownership_type == "primary"
    assert owner.is_team is True


def test_github_mapping_user_owner():
    """Test mapping a repo with a user owner."""
    mapping = GitHubMapping()
    repo_data = {
        "id": 67890,
        "name": "personal-repo",
        "html_url": "https://github.com/jdoe/personal-repo",
        "owner": {
            "login": "jdoe",
            "type": "User",
        },
    }

    result = mapping.map_item(repo_data, organization_id=1)

    # Check owners
    assert "owners" in result
    owners = result["owners"]
    assert len(owners) == 1
    owner = owners[0]
    assert owner.owner_name == "jdoe"
    assert owner.is_team is False


def test_github_mapping_no_owner():
    """Test mapping a repo with no owner."""
    mapping = GitHubMapping()
    repo_data = {
        "id": 99999,
        "name": "orphan-repo",
        "html_url": "https://github.com/orphan-repo",
    }

    result = mapping.map_item(repo_data, organization_id=1)

    # No owners key
    assert "owners" not in result


def test_github_mapping_registry():
    """Test that GitHubMapping is registered."""
    from app.integrations.model_mapping import MAPPINGS

    assert "github" in MAPPINGS
    assert MAPPINGS["github"] == GitHubMapping

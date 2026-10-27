"""GitHub → ArchiMate mapping.

Maps GitHub repositories to ApplicationComponent elements.
"""
from datetime import datetime
from typing import Dict, List, Optional

from app.integrations.model_mapping import (
    ElementFact,
    ModelMapping,
    OwnerFact,
    SourceRef,
    register_mapping,
)


@register_mapping
class GitHubMapping(ModelMapping):
    """Maps GitHub repositories to ArchiMate ApplicationComponent."""

    system_name = "github"
    display_name = "GitHub"

    def map_item(
        self,
        item: Dict,
        organization_id: int,
        *,
        context: Optional[Dict] = None,
    ) -> Dict:
        """Map a GitHub repository to ArchiMate facts.

        Args:
            item: GitHub repository data (from API)
            organization_id: The organization
            context: Optional context (unused)

        Returns:
            Dict with 'source', 'element', and optionally 'owners'
        """
        repo_name = item.get("name") or item.get("full_name", "Unknown")
        repo_id = str(item.get("id", item.get("full_name", "")))
        html_url = item.get("html_url", "")
        description = item.get("description") or f"GitHub repository: {repo_name}"

        # Source reference
        source = SourceRef(
            system="github",
            external_id=repo_id,
            item_type="repository",
            url=html_url,
            read_at=datetime.utcnow(),
        )

        # Primary element: ApplicationComponent
        element = ElementFact(
            archimate_type="ApplicationComponent",
            name=repo_name,
            description=description,
            properties={
                "language": item.get("language"),
                "stars": item.get("stargazers_count", 0),
                "forks": item.get("forks_count", 0),
                "archived": item.get("archived", False),
                "license": item.get("license", {}).get("name") if item.get("license") else None,
            },
        )

        result = {
            "source": source,
            "element": element,
        }

        # Owner (if available)
        owner_data = item.get("owner", {})
        owner_login = owner_data.get("login")
        owner_type = owner_data.get("type")  # "User" or "Organization"

        owners: List[OwnerFact] = []
        if owner_login and owner_type == "Organization":
            # Organization owner → team owner (BusinessActor)
            owners.append(
                OwnerFact(
                    element_path="element",
                    owner_name=owner_login,
                    ownership_type="primary",
                    is_team=True,
                )
            )
        elif owner_login and owner_type == "User":
            # User owner → person owner
            # Use login as name; email would need to be fetched separately
            owners.append(
                OwnerFact(
                    element_path="element",
                    owner_name=owner_login,
                    ownership_type="primary",
                    is_team=False,
                )
            )

        if owners:
            result["owners"] = owners

        return result

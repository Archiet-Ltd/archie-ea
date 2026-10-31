"""
-> app.modules.ai_chat.services

AI Chat Contextual Link Service

Provides intelligent contextual links to relevant dashboards and pages
based on user queries and available data in the AI Chat responses.
"""

import re
from typing import Any, Dict, List
from urllib.parse import urljoin


class AIChatLinkService:
    """Service for generating contextual links in AI Chat responses"""

    def __init__(self, base_url: str = "http://localhost:5000"):
        self.base_url = base_url

        # Mapping of query patterns to relevant pages
        self.query_mappings = {
            # Capability Analysis
            "unmapped_capabilities": {
                "patterns": [
                    r"unmapped.*capabilities?",
                    r"capabilities?.*not.*mapped",
                    r"capabilities?.*without.*applications?",
                    r"orphaned.*capabilities?",
                    r"capabilities?.*missing.*applications?",
                ],
                "url": "/capability-analysis/unmapped",
                "title": "Unmapped Capabilities Analysis",
                "description": "View capabilities that are not mapped to applications",
            },
            # Gap Analysis
            "gap_analysis": {
                "patterns": [
                    r"gap.*analysis",
                    r"identify.*gaps?",
                    r"what.*missing",
                    r"coverage.*gaps?",
                    r"capability.*gaps?",
                ],
                "url": "/auto-dashboard/business-capability",
                "title": "Business Capability Dashboard",
                "description": "Comprehensive business capability analysis",
            },
            # Application Management
            "application_registry": {
                "patterns": [
                    r"application.*registry",
                    r"all.*applications?",
                    r"application.*portfolio",
                    r"application.*inventory",
                    r"list.*applications?",
                ],
                "url": "/applications/",
                "title": "Application Registry",
                "description": "Complete application portfolio and registry",
            },
            # Vendor Analysis
            "vendor_analysis": {
                "patterns": [
                    r"vendor.*analysis",
                    r"vendor.*intelligence",
                    r"vendor.*comparison",
                    r"vendor.*evaluation",
                    r"supplier.*analysis",
                ],
                "url": "/applications/vendors",
                "title": "Vendor Dashboard",
                "description": "Comprehensive vendor intelligence and analysis",
            },
            # Architecture Models
            "architecture_models": {
                "patterns": [
                    r"architecture.*models?",
                    r"archimate.*models?",
                    r"enterprise.*architecture",
                    r"architecture.*repository",
                    r"model.*catalog",
                ],
                "url": "/dashboard/architecture",
                "title": "Architecture Dashboard",
                "description": "ArchiMate architecture models and elements",
            },
            # Technology Stack
            "technology_stack": {
                "patterns": [
                    r"technology.*stack",
                    r"tech.*stack",
                    r"technology.*portfolio",
                    r"infrastructure.*overview",
                    r"system.*architecture",
                ],
                "url": "/dashboard/operations",
                "title": "Technology Dashboard",
                "description": "Technology stack and infrastructure overview",
            },
            # Business Capabilities
            "business_capabilities": {
                "patterns": [
                    r"business.*capabilities?",
                    r"capability.*model",
                    r"capability.*map",
                    r"business.*architecture",
                    r"capability.*framework",
                ],
                "url": "/auto-dashboard/business-capability",
                "title": "Business Capabilities",
                "description": "Business capability model and framework",
            },
            # Dashboard Home
            "dashboard_home": {
                "patterns": [
                    r"dashboard",
                    r"overview",
                    r"summary",
                    r"main.*dashboard",
                    r"home.*page",
                ],
                "url": "/dashboard",
                "title": "Main Dashboard",
                "description": "Main dashboard and analytics overview",
            },
        }

        # Domain-specific default links
        self.domain_defaults = {
            "gap_analysis": "/auto-dashboard/business-capability",
            "business_capability": "/auto-dashboard/business-capability",
            "technology": "/dashboard/operations",
            "architecture": "/dashboard/architecture",
            "vendor_analysis": "/applications/vendors",
            "database": "/dashboard",
            "search": "/dashboard",
        }

    def extract_contextual_links(
        self, message: str, domain: str, context_data: Dict = None
    ) -> List[Dict[str, Any]]:
        """
        Extract relevant links based on message content and domain

        Args:
            message: User's message
            domain: AI chat domain
            context_data: Additional context from domain

        Returns:
            List of relevant links with metadata
        """
        links = []
        message_lower = message.lower()

        # Check each mapping for pattern matches
        for link_key, link_config in self.query_mappings.items():
            for pattern in link_config["patterns"]:
                if re.search(pattern, message_lower):
                    links.append(
                        {
                            "url": urljoin(self.base_url, link_config["url"]),
                            "title": link_config["title"],
                            "description": link_config["description"],
                            "relevance_score": self._calculate_relevance(
                                message_lower, pattern, domain
                            ),
                            "type": "contextual",
                        }
                    )
                    break  # Only add once per mapping

        # Add domain-specific default link if no contextual links found
        if not links and domain in self.domain_defaults:
            default_url = self.domain_defaults[domain]
            links.append(
                {
                    "url": urljoin(self.base_url, default_url),
                    "title": f'{domain.replace("_", " ").title()} Dashboard',
                    "description": f'Main dashboard for {domain.replace("_", " ")} analysis',
                    "relevance_score": 0.5,
                    "type": "domain_default",
                }
            )

        # Sort by relevance score
        links.sort(key=lambda x: x["relevance_score"], reverse=True)

        # Return top 3 most relevant links
        return links[:3]

    def _calculate_relevance(self, message: str, pattern: str, domain: str) -> float:
        """Calculate relevance score for a link based on message and domain"""
        base_score = 0.7

        # Exact match gets higher score
        if re.search(pattern, message):
            base_score += 0.2

        # Domain relevance boost
        if any(domain_keyword in pattern for domain_keyword in domain.split("_")):
            base_score += 0.1

        return min(base_score, 1.0)

    def format_links_for_response(self, links: List[Dict[str, Any]]) -> str:
        """
        Format links for inclusion in AI response

        Args:
            links: List of link dictionaries

        Returns:
            Formatted string with links for AI response
        """
        if not links:
            return ""

        formatted_links = "\n\n**🔗 Relevant Dashboards & Pages:**\n"

        for i, link in enumerate(links, 1):
            if link["type"] == "contextual":
                formatted_links += (
                    f"{i}. [{link['title']}]({link['url']}) - {link['description']}\n"
                )
            else:
                formatted_links += (
                    f"{i}. [{link['title']}]({link['url']}) - {link['description']}\n"
                )

        return formatted_links

    def enhance_ai_response(
        self, message: str, domain: str, ai_response: str, context_data: Dict = None
    ) -> str:
        """
        Enhance AI response with contextual links

        Args:
            message: User's message
            domain: AI chat domain
            ai_response: Generated AI response
            context_data: Domain context data

        Returns:
            Enhanced response with contextual links
        """
        # Extract relevant links
        links = self.extract_contextual_links(message, domain, context_data)

        # Format and append links to response
        links_section = self.format_links_for_response(links)

        if links_section:
            return ai_response + links_section

        return ai_response

    def get_quick_actions(self, domain: str) -> List[Dict[str, Any]]:
        """
        Get quick action links for a specific domain

        Args:
            domain: AI chat domain

        Returns:
            List of quick action links
        """
        quick_actions = {
            "gap_analysis": [
                {
                    "url": urljoin(self.base_url, "/capability-analysis/unmapped"),
                    "title": "View Unmapped Capabilities",
                    "description": "See capabilities not mapped to applications",
                    "icon": "🔍",
                },
                {
                    "url": urljoin(self.base_url, "/auto-dashboard/business-capability"),
                    "title": "Business Capability Dashboard",
                    "description": "Comprehensive business capability analysis",
                    "icon": "📊",
                },
            ],
            "business_capability": [
                {
                    "url": urljoin(self.base_url, "/auto-dashboard/business-capability"),
                    "title": "Business Capability Dashboard",
                    "description": "Business capability overview",
                    "icon": "🏢",
                },
                {
                    "url": urljoin(self.base_url, "/capability-analysis/unmapped"),
                    "title": "Unmapped Capabilities",
                    "description": "Capabilities needing application support",
                    "icon": "⚠️",
                },
            ],
            "technology": [
                {
                    "url": urljoin(self.base_url, "/dashboard/operations"),
                    "title": "Technology Dashboard",
                    "description": "Technology portfolio overview",
                    "icon": "💻",
                },
                {
                    "url": urljoin(self.base_url, "/applications/"),
                    "title": "Application Registry",
                    "description": "Complete application inventory",
                    "icon": "📋",
                },
            ],
            "vendor_analysis": [
                {
                    "url": urljoin(self.base_url, "/applications/vendors"),
                    "title": "Vendor Dashboard",
                    "description": "Vendor analysis and comparison",
                    "icon": "🏭",
                }
            ],
        }

        return quick_actions.get(domain, [])

    def validate_link_exists(self, url: str) -> bool:
        """
        Validate if a link exists in the application by checking Flask's url_map.

        Args:
            url: URL path to validate (e.g. "/dashboard")

        Returns:
            True if a registered route matches the path, False otherwise.
            Returns True when called outside an application context (safe fallback).
        """
        try:
            from flask import current_app
            from urllib.parse import urlparse

            path = urlparse(url).path
            for rule in current_app.url_map.iter_rules():
                # Strip trailing slashes for comparison
                rule_path = rule.rule.rstrip("/") or "/"
                check_path = path.rstrip("/") or "/"
                if rule_path == check_path:
                    return True
            return False
        except RuntimeError:
            # Outside application context — treat as valid (safe fallback)
            return True

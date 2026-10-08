"""
Base Detection Strategy

Abstract base class defining the interface for all detection strategies.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DetectionResult:
    """Result of a duplicate detection operation"""

    groups: List[Dict[str, Any]]
    exact_matches: int = 0
    fuzzy_matches: int = 0
    applications_analyzed: int = 0
    estimated_savings: float = 0.0
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class SimilarityResult:
    """Result of comparing two applications"""

    overall_score: float
    name_similarity: float
    description_similarity: float
    vendor_similarity: float = 0.0
    match_type: str = "fuzzy"
    details: Optional[Dict[str, Any]] = None


class DetectionStrategy(ABC):
    """
    Abstract base class for detection strategies.

    All detection algorithms must implement:
    - detect(): Main detection method
    - calculate_similarity(): Compare two applications
    """

    # Common acronym expansions used across strategies
    ACRONYM_EXPANSIONS = {
        "erp": ["enterprise resource planning", "enterprise system"],
        "crm": ["customer relationship management", "customer management"],
        "hrm": ["human resource management", "hr management"],
        "hris": ["human resource information system", "hr system"],
        "scm": ["supply chain management", "supply management"],
        "wms": ["warehouse management system", "warehouse system"],
        "tms": ["transport management system", "transportation system"],
        "bpm": ["business process management", "process management"],
        "ecm": ["enterprise content management", "content management"],
        "dms": ["document management system", "document system"],
        "bi": ["business intelligence", "analytics"],
        "etl": ["extract transform load", "data integration"],
        "api": ["application programming interface", "interface"],
        "sso": ["single sign on", "authentication"],
        "iam": ["identity access management", "identity management"],
        "pim": ["product information management", "product management"],
        "dam": ["digital asset management", "asset management"],
        "cam": ["computer aided manufacturing", "manufacturing system"],
        "cad": ["computer aided design", "design system"],
        "plm": ["product lifecycle management", "lifecycle management"],
        "mes": ["manufacturing execution system", "manufacturing system"],
        "sap": ["systems applications products", "sap se"],
        "prs": ["plasterboard recycling system", "recycling system"],
        "ims": ["inventory management system", "inventory system"],
        "oms": ["order management system", "order system"],
        "pos": ["point of sale", "sales system"],
        "eam": ["enterprise asset management", "asset management"],
        "cmms": ["computerized maintenance management system", "maintenance system"],
        "ems": ["environmental management system", "environment system"],
        "lms": ["learning management system", "learning system"],
        "cms": ["content management system", "content system"],
        "gis": ["geographic information system", "mapping system"],
        "bms": ["building management system", "building system"],
        "scada": ["supervisory control data acquisition", "control system"],
        "rpa": ["robotic process automation", "automation"],
        "ocr": ["optical character recognition", "text recognition"],
        "edw": ["enterprise data warehouse", "data warehouse"],
        "olap": ["online analytical processing", "analytics"],
        "oltp": ["online transaction processing", "transaction system"],
        "mfa": ["multi factor authentication", "authentication"],
        "vpn": ["virtual private network", "network security"],
        "cdn": ["content delivery network", "content network"],
        "dns": ["domain name system", "name resolution"],
        "tcp": ["transmission control protocol", "network protocol"],
        "http": ["hypertext transfer protocol", "web protocol"],
        "sql": ["structured query language", "database query"],
        "nosql": ["not only sql", "non relational database"],
        "saas": ["software as a service", "cloud software"],
        "paas": ["platform as a service", "cloud platform"],
        "iaas": ["infrastructure as a service", "cloud infrastructure"],
    }

    # Common vendor aliases
    VENDOR_ALIASES = {
        "microsoft": ["ms", "msft", "microsoft corporation"],
        "sap": ["sap se", "sap ag"],
        "oracle": ["oracle corporation", "oracle corp"],
        "salesforce": ["sfdc", "salesforce.com"],
        "ibm": ["international business machines", "ibm corporation"],
        "google": ["alphabet", "google llc", "google cloud"],
        "amazon": ["aws", "amazon web services"],
        "adobe": ["adobe systems", "adobe inc"],
        "servicenow": ["service now", "servicenow inc"],
        "workday": ["workday inc"],
    }

    def __init__(self, threshold: float = 0.55, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the detection strategy.

        Args:
            threshold: Minimum similarity score to consider as duplicate (0 - 1)
            config: Strategy-specific configuration options
        """
        self.threshold = threshold
        self.config = config or {}

    @abstractmethod
    def detect(self, applications: List[Any]) -> DetectionResult:
        """
        Run duplicate detection on a list of applications.

        Args:
            applications: List of ApplicationComponent objects

        Returns:
            DetectionResult with found duplicate groups
        """
        pass

    @abstractmethod
    def calculate_similarity(self, app1: Any, app2: Any) -> SimilarityResult:
        """
        Calculate similarity between two applications.

        Args:
            app1: First ApplicationComponent
            app2: Second ApplicationComponent

        Returns:
            SimilarityResult with similarity scores
        """
        pass

    def _normalize_name(self, name: str) -> str:
        """Normalize application name for comparison"""
        if not name:
            return ""
        # Remove special chars, lowercase, strip
        normalized = "".join(c.lower() if c.isalnum() or c.isspace() else " " for c in name)
        # Collapse multiple spaces
        normalized = " ".join(normalized.split())
        return normalized.strip()

    def _expand_acronyms(self, text: str) -> List[str]:
        """Expand acronyms in text to their full forms"""
        if not text:
            return [text]

        words = text.lower().split()
        expansions = [text.lower()]

        for word in words:
            if word in self.ACRONYM_EXPANSIONS:
                for expansion in self.ACRONYM_EXPANSIONS[word]:
                    expanded = text.lower().replace(word, expansion)
                    if expanded not in expansions:
                        expansions.append(expanded)

        return expansions

    def _normalize_vendor(self, vendor: str) -> str:
        """Normalize vendor name for comparison"""
        if not vendor:
            return ""

        normalized = vendor.lower().strip()

        # Check for known aliases
        for canonical, aliases in self.VENDOR_ALIASES.items():
            if normalized == canonical or normalized in aliases:
                return canonical

        return normalized

    def _extract_acronym_from_parentheses(self, name: str) -> Optional[str]:
        """Extract acronym from parentheses like 'Some System (SS)'"""
        import re

        match = re.search(r"\(([A-Z]{2,})\)", name)
        if match:
            return match.group(1).lower()
        return None

    def _estimate_group_savings(self, applications: List[Any]) -> float:
        """
        Estimate potential savings from consolidating a group of duplicates.

        Args:
            applications: List of applications in the duplicate group

        Returns:
            Estimated annual savings in dollars
        """
        if len(applications) <= 1:
            return 0.0

        total_cost = 0.0
        apps_with_cost = 0

        for app in applications:
            cost = 0.0
            # Try various cost attributes
            # TODO(Release 2 Cost Fact consolidation): Use application_cost_accessor.get_annual_cost()
            # instead of direct model field access.
            if hasattr(app, "annual_cost") and app.annual_cost:
                cost = float(app.annual_cost)
            elif hasattr(app, "license_cost") and app.license_cost:
                cost = float(app.license_cost)
            elif hasattr(app, "total_cost_of_ownership") and app.total_cost_of_ownership:
                cost = float(app.total_cost_of_ownership)

            if cost > 0:
                total_cost += cost
                apps_with_cost += 1

        # If we have cost data, use it
        if apps_with_cost > 1:
            # Assume we keep one app and save on the rest
            # Average cost per app, times (n - 1) apps to retire
            avg_cost = total_cost / apps_with_cost
            return avg_cost * (len(applications) - 1)

        # Default estimate: $35,000 per redundant application
        return 35000.0 * (len(applications) - 1)

    def get_strategy_name(self) -> str:
        """Get the name of this strategy"""
        return self.__class__.__name__.replace("DetectionStrategy", "").lower()

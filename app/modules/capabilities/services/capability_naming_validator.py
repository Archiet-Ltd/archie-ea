"""
DEPRECATED: Import from app.modules.capabilities.services.analysis_service instead.
-> app.modules.capabilities.services.analysis_service

TOGAF & BizBok Aligned Capability Naming Validator Service

This service validates and enforces proper capability naming conventions
according to TOGAF 9.2 and BizBok standards.

TOGAF 9.2 Standard:
- Format: Verb-Noun (e.g., "Manage Customer Relationships")
- Level 1: Strategic capabilities (5 - 7 per enterprise)
- Level 2: Business capabilities (15 - 25 per enterprise)
- Level 3: Detailed capabilities (50 - 100 per enterprise)

BizBok Standard:
- Format: Noun-based (e.g., "Customer Relationship Management")
- Mapping: Business Capability → Value Stream → Business Process
- Structure: Hierarchical with clear parent-child relationships
"""

import re
from dataclasses import dataclass
from typing import Dict, List

from app.models.unified_capability import BusinessDomain, UnifiedCapability

from app import db


@dataclass
class NamingIssue:
    """Represents a naming convention violation"""

    capability_id: int
    capability_name: str
    current_code: str
    issue_type: str
    description: str
    suggested_name: str
    suggested_code: str
    severity: str  # critical, high, medium, low


class CapabilityNamingValidator:
    """Validates and enforces TOGAF & BizBok naming conventions"""

    # TOGAF 9.2 recommended capability counts
    TOGAF_LEVEL1_MAX = 7
    TOGAF_LEVEL2_MAX = 25
    TOGAF_LEVEL3_MAX = 100

    # BizBok naming patterns
    BIZBOK_PATTERN = re.compile(
        r"^[A-Z][a-zA-Z\s]+(?:Management|Support|Services|Systems|Operations|Analytics|Planning|Development|Maintenance|Control|Governance|Oversight|Coordination|Assurance|Compliance|Optimization|Enhancement)$"
    )
    TOGAF_PATTERN = re.compile(r"^[A-Z][a-z]+\s+[A-Z][a-zA-Z\s]+$")

    # Domain codes for proper naming
    DOMAIN_CODES = {
        "CUST": "Customer",
        "PROD": "Product",
        "OPER": "Operations",
        "FIN": "Financial",
        "RISK": "Risk & Compliance",
        "DATA": "Data & Analytics",
        "PART": "Partner & Supplier",
        "WORK": "Workforce",
        "TECH": "Technology",
    }

    def __init__(self):
        self.issues = []
        self.statistics = {
            "total_capabilities": 0,
            "naming_issues": 0,
            "duplicates_found": 0,
            "level_distribution": {1: 0, 2: 0, 3: 0},
            "domain_distribution": {},
        }

    def validate_all_capabilities(self) -> List[NamingIssue]:
        """Validate all capabilities against TOGAF & BizBok standards"""
        self.issues = []

        # Get all capabilities
        capabilities = UnifiedCapability.active().all()
        self.statistics["total_capabilities"] = len(capabilities)

        # Count by level
        for cap in capabilities:
            self.statistics["level_distribution"][cap.level] += 1

        # Count by domain
        for cap in capabilities:
            domain = BusinessDomain.query.get(cap.domain_id)
            if domain:
                domain_code = domain.code
                self.statistics["domain_distribution"][domain_code] = (
                    self.statistics["domain_distribution"].get(domain_code, 0) + 1
                )

        # Validate each capability
        for capability in capabilities:
            self._validate_single_capability(capability)

        # Find duplicates
        self._find_duplicates()

        return self.issues

    def _validate_single_capability(self, capability: UnifiedCapability):
        """Validate a single capability against naming conventions"""
        issues = []

        # Check TOGAF naming convention (Verb-Noun)
        if not self.TOGAF_PATTERN.match(capability.name.strip()):
            issues.append(
                NamingIssue(
                    capability_id=capability.id,
                    capability_name=capability.name,
                    current_code=capability.code or "",
                    issue_type="TOGAF Naming Convention",
                    description='TOGAF 9.2 recommends Verb-Noun format (e.g., "Manage Customer Relationships")',
                    suggested_name=self._suggest_togaf_name(capability),
                    suggested_code=self._suggest_togaf_code(capability),
                    severity="high",
                )
            )

        # Check BizBok naming convention (Noun-based ending with Management/Services/etc)
        if not self.BIZBOK_PATTERN.match(capability.name.strip()):
            issues.append(
                NamingIssue(
                    capability_id=capability.id,
                    capability_name=capability.name,
                    current_code=capability.code or "",
                    issue_type="BizBok Naming Convention",
                    description='BizBok recommends Noun-based format ending with Management/Services/etc (e.g., "Customer Relationship Management")',
                    suggested_name=self._suggest_bizbok_name(capability),
                    suggested_code=self._suggest_bizbok_code(capability),
                    severity="medium",
                )
            )

        # Check code format (should be descriptive, not DOMAIN-FUNCTION-ACTIVITY)
        if capability.code and self._is_old_code_format(capability.code):
            issues.append(
                NamingIssue(
                    capability_id=capability.id,
                    capability_name=capability.name,
                    current_code=capability.code,
                    issue_type="Code Format",
                    description="Code should be descriptive, not DOMAIN-FUNCTION-ACTIVITY format",
                    suggested_name=capability.name,
                    suggested_code=self._suggest_new_code(capability),
                    severity="medium",
                )
            )

        # Check level appropriateness
        level_issues = self._validate_level_distribution(capability)
        issues.extend(level_issues)

        self.issues.extend(issues)

    def _suggest_togaf_name(self, capability: UnifiedCapability) -> str:
        """Suggest TOGAF 9.2 compliant name (Verb-Noun)"""
        name = capability.name.strip()

        # Common TOGAF action verbs
        action_verbs = [
            "Manage",
            "Develop",
            "Support",
            "Enable",
            "Provide",
            "Ensure",
            "Facilitate",
            "Optimize",
            "Coordinate",
            "Control",
        ]

        # Extract key concepts from current name
        words = name.split()
        if len(words) >= 2:
            # Try to make first word an action verb
            first_word = words[0]
            if first_word.lower() not in [verb.lower() for verb in action_verbs]:
                # Suggest appropriate action verb based on capability type
                if capability.category == "core":
                    first_word = "Manage"
                elif capability.category == "supporting":
                    first_word = "Support"
                else:
                    first_word = "Enable"

            # Reconstruct name
            suggested_name = f"{first_word} {' '.join(words[1:])}"
        else:
            suggested_name = f"Manage {name}"

        return suggested_name

    def _suggest_bizbok_name(self, capability: UnifiedCapability) -> str:
        """Suggest BizBok compliant name (Noun-based with Management/Services/etc)"""
        name = capability.name.strip()

        # Common BizBok endings
        bizbok_endings = [
            "Management",
            "Services",
            "Systems",
            "Operations",
            "Analytics",
            "Planning",
            "Development",
            "Maintenance",
            "Control",
            "Governance",
            "Oversight",
            "Coordination",
            "Assurance",
            "Compliance",
            "Optimization",
            "Enhancement",
        ]

        # If already ends with BizBok ending, return as-is
        for ending in bizbok_endings:
            if name.endswith(ending):
                return name

        # Suggest appropriate ending based on capability type
        if capability.category == "core":
            suggested_name = f"{name} Management"
        elif capability.category == "supporting":
            suggested_name = f"{name} Support"
        elif capability.capability_type == "strategic":
            suggested_name = f"{name} Planning"
        else:
            suggested_name = f"{name} Services"

        return suggested_name

    def _suggest_togaf_code(self, capability: UnifiedCapability) -> str:
        """Suggest TOGAF compliant code (descriptive, hierarchical)"""
        domain = BusinessDomain.query.get(capability.domain_id)
        domain_code = domain.code if domain else "UNK"

        # Create descriptive code based on name
        name_words = capability.name.replace(" ", "_").replace("-", "_").split("_")
        name_code = "_".join(
            [word.capitalize() for word in name_words if word][:3]
        )  # Limit to 3 words

        return f"{domain_code}_{name_code}"

    def _suggest_bizbok_code(self, capability: UnifiedCapability) -> str:
        """Suggest BizBok compliant code (descriptive, hierarchical)"""
        domain = BusinessDomain.query.get(capability.domain_id)
        domain_code = domain.code if domain else "UNK"

        # Create descriptive code based on name
        name_words = capability.name.replace(" ", "_").replace("-", "_").split("_")
        name_code = "_".join(
            [word.capitalize() for word in name_words if word][:3]
        )  # Limit to 3 words

        return f"{domain_code}_{name_code}"

    def _suggest_new_code(self, capability: UnifiedCapability) -> str:
        """Suggest descriptive code (not DOMAIN-FUNCTION-ACTIVITY)"""
        domain = BusinessDomain.query.get(capability.domain_id)
        domain_code = domain.code if domain else "UNK"

        # Create descriptive code based on name
        name_words = capability.name.replace(" ", "_").replace("-", "_").split("_")
        name_code = "_".join(
            [word.capitalize() for word in name_words if word][:3]
        )  # Limit to 3 words

        return f"{domain_code}_{name_code}"

    def _is_old_code_format(self, code: str) -> bool:
        """Check if code follows old DOMAIN-FUNCTION-ACTIVITY format"""
        if not code:
            return False

        parts = code.split("-")
        return len(parts) == 3 and parts[0] in self.DOMAIN_CODES

    def _validate_level_distribution(self, capability: UnifiedCapability) -> List[NamingIssue]:
        """Validate if capability level follows TOGAF recommendations"""
        issues = []

        # Check if too many Level 1 capabilities
        if capability.level == 1:
            level1_count = self.statistics["level_distribution"][1]
            if level1_count > self.TOGAF_LEVEL1_MAX:
                issues.append(
                    NamingIssue(
                        capability_id=capability.id,
                        capability_name=capability.name,
                        current_code=capability.code or "",
                        issue_type="TOGAF Level 1 Count",
                        description=f"TOGAF 9.2 recommends max {self.TOGAF_LEVEL1_MAX} Level 1 capabilities, currently have {level1_count}",
                        suggested_name=capability.name,
                        suggested_code=capability.code or "",
                        severity="critical",
                    )
                )

        # Check if too many Level 2 capabilities
        elif capability.level == 2:
            level2_count = self.statistics["level_distribution"][2]
            if level2_count > self.TOGAF_LEVEL2_MAX:
                issues.append(
                    NamingIssue(
                        capability_id=capability.id,
                        capability_name=capability.name,
                        current_code=capability.code or "",
                        issue_type="TOGAF Level 2 Count",
                        description=f"TOGAF 9.2 recommends max {self.TOGAF_LEVEL2_MAX} Level 2 capabilities, currently have {level2_count}",
                        suggested_name=capability.name,
                        suggested_code=capability.code or "",
                        severity="high",
                    )
                )

        # Check if too many Level 3 capabilities
        elif capability.level == 3:
            level3_count = self.statistics["level_distribution"][3]
            if level3_count > self.TOGAF_LEVEL3_MAX:
                issues.append(
                    NamingIssue(
                        capability_id=capability.id,
                        capability_name=capability.name,
                        current_code=capability.code or "",
                        issue_type="TOGAF Level 3 Count",
                        description=f"TOGAF 9.2 recommends max {self.TOGAF_LEVEL3_MAX} Level 3 capabilities, currently have {level3_count}",
                        suggested_name=capability.name,
                        suggested_code=capability.code or "",
                        severity="medium",
                    )
                )

        return issues

    def _find_duplicates(self):
        """Find potential duplicate capabilities based on naming similarity"""
        capabilities = UnifiedCapability.active().all()

        for i, cap1 in enumerate(capabilities):
            for j, cap2 in enumerate(capabilities[i + 1 :], i + 1):
                # Check for similar names (potential duplicates)
                similarity = self._calculate_name_similarity(cap1.name, cap2.name)
                if similarity > 0.8:  # 80% similarity threshold
                    self.issues.append(
                        NamingIssue(
                            capability_id=cap1.id,
                            capability_name=cap1.name,
                            current_code=cap1.code or "",
                            issue_type="Potential Duplicate",
                            description=f'Potential duplicate with "{cap2.name}" (similarity: {similarity:.2f})',
                            suggested_name=cap1.name,
                            suggested_code=cap1.code or "",
                            severity="high",
                        )
                    )
                    self.statistics["duplicates_found"] += 1

    def _calculate_name_similarity(self, name1: str, name2: str) -> float:
        """Calculate similarity between two capability names"""
        # Simple similarity calculation (can be enhanced with more sophisticated algorithms)
        name1_lower = name1.lower().strip()
        name2_lower = name2.lower().strip()

        # Remove common words for comparison
        common_words = {
            "management",
            "services",
            "systems",
            "operations",
            "support",
            "development",
            "planning",
            "control",
        }
        for word in common_words:
            name1_lower = name1_lower.replace(word, "")
            name2_lower = name2_lower.replace(word, "")

        # Calculate Jaccard similarity
        words1 = set(name1_lower.split())
        words2 = set(name2_lower.split())

        intersection = len(words1.intersection(words2))
        union = len(words1.union(words2))

        return intersection / union if union > 0 else 0

    def get_naming_report(self) -> Dict:
        """Generate comprehensive naming convention report"""
        return {
            "statistics": self.statistics,
            "issues": [
                {
                    "capability_id": issue.capability_id,
                    "capability_name": issue.capability_name,
                    "current_code": issue.current_code,
                    "issue_type": issue.issue_type,
                    "description": issue.description,
                    "suggested_name": issue.suggested_name,
                    "suggested_code": issue.suggested_code,
                    "severity": issue.severity,
                }
                for issue in self.issues
            ],
            "summary": {
                "total_issues": len(self.issues),
                "critical_issues": len([i for i in self.issues if i.severity == "critical"]),
                "high_issues": len([i for i in self.issues if i.severity == "high"]),
                "medium_issues": len([i for i in self.issues if i.severity == "medium"]),
                "low_issues": len([i for i in self.issues if i.severity == "low"]),
                "duplicates_found": self.statistics["duplicates_found"],
            },
            "recommendations": self._get_recommendations(),
        }

    def _get_recommendations(self) -> List[str]:
        """Generate recommendations based on naming issues found"""
        recommendations = []

        if self.statistics["duplicates_found"] > 0:
            recommendations.append(
                f"Consolidate {self.statistics['duplicates_found']} potential duplicate capabilities"
            )

        critical_issues = [i for i in self.issues if i.severity == "critical"]
        if critical_issues:
            recommendations.append(
                f"Address {len(critical_issues)} critical naming issues immediately"
            )

        high_issues = [i for i in self.issues if i.severity == "high"]
        if high_issues:
            recommendations.append(f"Fix {len(high_issues)} high-priority naming issues")

        level1_count = self.statistics["level_distribution"][1]
        if level1_count > self.TOGAF_LEVEL1_MAX:
            recommendations.append(
                f"Reduce Level 1 capabilities from {level1_count} to {self.TOGAF_LEVEL1_MAX} (TOGAF 9.2 standard)"
            )

        return recommendations

    def fix_naming_conventions(self) -> Dict:
        """Apply naming convention fixes to all capabilities"""
        fixes_applied = []
        errors_encountered = []

        for issue in self.issues:
            try:
                capability = UnifiedCapability.query.get(issue.capability_id)
                if capability:
                    # Apply suggested name
                    capability.name = issue.suggested_name

                    # Apply suggested code if available
                    if issue.suggested_code:
                        capability.code = issue.suggested_code

                    db.session.commit()
                    fixes_applied.append(
                        {
                            "capability_id": issue.capability_id,
                            "old_name": issue.capability_name,
                            "new_name": issue.suggested_name,
                            "old_code": issue.current_code,
                            "new_code": issue.suggested_code,
                            "issue_type": issue.issue_type,
                        }
                    )
            except Exception as e:
                errors_encountered.append({"capability_id": issue.capability_id, "error": str(e)})

        return {
            "fixes_applied": fixes_applied,
            "errors_encountered": errors_encountered,
            "total_issues": len(self.issues),
            "success_rate": (len(fixes_applied) / len(self.issues) * 100) if self.issues else 100,
        }

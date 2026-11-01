"""
DEPRECATED: Import from app.modules.capabilities.services.analysis_service instead.
-> app.modules.capabilities.services.analysis_service

Capability Naming Standardization Service

This service provides comprehensive capability naming standardization,
duplicate detection, and data cleanup functionality.
"""

import logging
import re
from datetime import datetime
from typing import Dict, List


from app.models.unified_capability import BusinessDomain, UnifiedCapability

from app import db

logger = logging.getLogger(__name__)


class CapabilityNamingService:
    """Service for standardizing capability names and eliminating duplicates."""

    # Standard naming patterns and rules
    NAMING_RULES = {
        "forbidden_prefixes": ["Manage "],  # ONLY remove "Manage " prefix, not "Manage" alone
        "forbidden_suffixes": [],  # Keep "Management" suffixes - they are correct
        "management_keywords": [
            "Financial",
            "Technology",
            "Digital",
            "Operations",
            "Workforce",
            "Organization",
            "Customer",
            "Data",
            "Analytics",
            "Risk",
            "Product",
            "Service",
            "Supply",
            "Chain",
            "Quality",
            "Enterprise",
            "Strategic",
            "Performance",
            "Change",
            "Project",
            "Program",
            "Portfolio",
        ],
    }

    @staticmethod
    def clean_capability_name(name: str) -> str:
        """
        Clean a capability name by removing ONLY the "Manage " prefix.
        Keep "Management" suffixes as they are correct.

        Args:
            name: Original capability name

        Returns:
            Cleaned capability name
        """
        if not name:
            return name

        cleaned_name = name.strip()

        # Remove ONLY the "Manage " prefix (with space)
        if cleaned_name.startswith("Manage "):
            cleaned_name = cleaned_name[7:].strip()  # Remove "Manage " prefix (7 characters)
            logger.info(f"Removed prefix 'Manage ' from '{name}' -> '{cleaned_name}'")

        # Capitalize properly (Title Case)
        cleaned_name = " ".join(word.capitalize() for word in cleaned_name.split())

        return cleaned_name

    @staticmethod
    def detect_duplicate_capabilities() -> List[Dict]:
        """
        Detect potential duplicate capabilities based on cleaned names.

        Returns:
            List of potential duplicate groups
        """
        try:
            # Get all capabilities
            capabilities = UnifiedCapability.active().all()

            # Group by cleaned names
            name_groups = {}
            for cap in capabilities:
                cleaned_name = CapabilityNamingService.clean_capability_name(cap.name)
                if cleaned_name not in name_groups:
                    name_groups[cleaned_name] = []
                name_groups[cleaned_name].append(cap)

            # Find duplicates (groups with more than 1 capability)
            duplicates = []
            for cleaned_name, caps in name_groups.items():
                if len(caps) > 1:
                    duplicates.append(
                        {
                            "cleaned_name": cleaned_name,
                            "capabilities": [
                                {"id": c.id, "name": c.name, "level": getattr(c, "level", None)}
                                for c in caps
                            ],
                            "count": len(caps),
                        }
                    )

            return duplicates

        except Exception as e:
            logger.error(f"Error detecting duplicates: {e}")
            return []

    @staticmethod
    def standardize_all_capability_names(dry_run: bool = True) -> Dict:
        """
        Standardize all capability names by removing forbidden prefixes/suffixes.

        Args:
            dry_run: If True, only report changes without making them

        Returns:
            Dictionary with standardization results
        """
        try:
            capabilities = UnifiedCapability.active().all()
            changes = []
            errors = []

            for cap in capabilities:
                original_name = cap.name
                cleaned_name = CapabilityNamingService.clean_capability_name(original_name)

                if original_name != cleaned_name:
                    change_info = {
                        "id": cap.id,
                        "original_name": original_name,
                        "cleaned_name": cleaned_name,
                        "code": cap.code,
                    }

                    if not dry_run:
                        try:
                            # Start fresh transaction for each update
                            db.session.rollback()  # Clear any previous transaction state
                            cap.name = cleaned_name
                            cap.updated_at = datetime.utcnow()
                            db.session.commit()
                            change_info["status"] = "updated"
                            logger.info(
                                f"Updated capability {cap.id}: '{original_name}' -> '{cleaned_name}'"
                            )
                        except Exception as e:
                            change_info["status"] = "error"
                            change_info["error"] = str(e)
                            errors.append(f"Failed to update capability {cap.id}: {e}")
                            db.session.rollback()
                            # Continue with next capability instead of failing completely
                    else:
                        change_info["status"] = "pending"

                    changes.append(change_info)

            return {
                "total_capabilities": len(capabilities),
                "changes_needed": len(changes),
                "changes_made": len([c for c in changes if c.get("status") == "updated"]),
                "errors": len(errors),
                "changes": changes,
                "error_details": errors,
            }

        except Exception as e:
            logger.error(f"Error in standardization: {e}")
            return {
                "total_capabilities": 0,
                "changes_needed": 0,
                "changes_made": 0,
                "errors": 1,
                "error_details": [str(e)],
            }

    @staticmethod
    def merge_duplicate_capabilities(
        duplicate_group: Dict, keep_capability_id: int, dry_run: bool = True
    ) -> Dict:
        """
        Merge duplicate capabilities, keeping the specified one as primary.

        Args:
            duplicate_group: Group of duplicate capabilities
            keep_capability_id: ID of the capability to keep
            dry_run: If True, only report changes without making them

        Returns:
            Dictionary with merge results
        """
        try:
            capabilities = duplicate_group["capabilities"]
            keep_cap = next((cap for cap in capabilities if cap.id == keep_capability_id), None)

            if not keep_cap:
                return {
                    "status": "error",
                    "message": f"Capability {keep_capability_id} not found in group",
                }

            # Find capabilities to remove
            remove_caps = [cap for cap in capabilities if cap.id != keep_capability_id]

            if dry_run:
                return {
                    "status": "dry_run",
                    "keep_capability": {
                        "id": keep_cap.id,
                        "name": keep_cap.name,
                        "code": keep_cap.code,
                    },
                    "remove_capabilities": [
                        {"id": cap.id, "name": cap.name, "code": cap.code} for cap in remove_caps
                    ],
                    "mappings_to_transfer": len(remove_caps) * 10,  # Estimate
                }

            # Perform actual merge
            errors = []
            mappings_transferred = 0

            for remove_cap in remove_caps:
                try:
                    # Transfer application mappings to the kept capability
                    from app.models.unified_application_capability_mapping import (
                        UnifiedApplicationCapabilityMapping,
                    )

                    mappings = UnifiedApplicationCapabilityMapping.query.filter_by(
                        unified_capability_id=remove_cap.id
                    ).all()

                    for mapping in mappings:
                        mapping.unified_capability_id = keep_capability_id
                        mappings_transferred += 1

                    # Delete the duplicate capability
                    db.session.delete(remove_cap)
                    logger.info(f"Merged capability {remove_cap.id} into {keep_capability_id}")

                except Exception as e:
                    errors.append(f"Error merging capability {remove_cap.id}: {e}")
                    db.session.rollback()

            if not errors:
                db.session.commit()

            return {
                "status": "completed",
                "keep_capability": {
                    "id": keep_cap.id,
                    "name": keep_cap.name,
                    "code": keep_cap.code,
                },
                "removed_capabilities": len(remove_caps),
                "mappings_transferred": mappings_transferred,
                "errors": errors,
            }

        except Exception as e:
            logger.error(f"Error in merge operation: {e}")
            return {"status": "error", "message": str(e)}

    @staticmethod
    def validate_capability_name(name: str) -> Dict:
        """
        Validate a capability name against naming standards.

        Args:
            name: Capability name to validate

        Returns:
            Validation result with issues and suggestions
        """
        issues = []
        suggestions = []

        if not name or not name.strip():
            issues.append("Name cannot be empty")
            return {"valid": False, "issues": issues, "suggestions": suggestions}

        # Check for forbidden "Manage " prefix (the ONLY forbidden pattern)
        if name.startswith("Manage "):
            issues.append("Contains forbidden prefix 'Manage '")
            clean_name = name[7:].strip()  # Remove "Manage " prefix
            if clean_name:
                suggestions.append(f"Consider: '{clean_name}'")

        # Check for double spaces
        if "  " in name:
            issues.append("Contains double spaces")
            suggestions.append(f"Consider: '{' '.join(name.split())}'")

        # Check length
        if len(name) > 100:
            issues.append("Name too long (max 100 characters)")

        # Check for special characters (except allowed ones)
        if not re.match(r"^[a-zA-Z0 - 9\s&\-]+$", name):
            issues.append("Contains invalid special characters")
            suggestions.append("Use only letters, numbers, spaces, &, and -")

        return {"valid": len(issues) == 0, "issues": issues, "suggestions": suggestions}

    @staticmethod
    def get_naming_statistics() -> Dict:
        """
        Get comprehensive statistics about capability naming.

        Returns:
            Dictionary with naming statistics
        """
        try:
            capabilities = UnifiedCapability.active().all()

            total_caps = len(capabilities)
            manage_prefix_count = sum(1 for cap in capabilities if cap.name.startswith("Manage "))
            management_suffix_count = sum(
                1 for cap in capabilities if cap.name.endswith(" Management")
            )

            # Detect duplicates
            duplicates = CapabilityNamingService.detect_duplicate_capabilities()

            # Domain distribution
            domain_stats = {}
            for cap in capabilities:
                domain = BusinessDomain.query.get(cap.domain_id) if cap.domain_id else None
                domain_name = domain.name if domain else "Unknown"
                if domain_name not in domain_stats:
                    domain_stats[domain_name] = 0
                domain_stats[domain_name] += 1

            return {
                "total_capabilities": total_caps,
                "manage_prefix_count": manage_prefix_count,
                "management_suffix_count": management_suffix_count,
                "naming_issues_count": manage_prefix_count + management_suffix_count,
                "duplicate_groups": len(duplicates),
                "duplicate_capabilities": sum(group["count"] for group in duplicates),
                "domain_distribution": domain_stats,
                "naming_quality_score": max(
                    0, 100 - (manage_prefix_count + management_suffix_count) * 2
                ),
            }

        except Exception as e:
            logger.error(f"Error getting statistics: {e}")
            return {
                "total_capabilities": 0,
                "manage_prefix_count": 0,
                "management_suffix_count": 0,
                "naming_issues_count": 0,
                "duplicate_groups": 0,
                "duplicate_capabilities": 0,
                "domain_distribution": {},
                "naming_quality_score": 0,
                "error": str(e),
            }

"""
Enterprise Validation Service

Validates capabilities, compliance policies, and violations.
"""


class EnterpriseValidationService:
    """Service for validating enterprise entities."""

    VALID_CAPABILITY_TYPES = [
        "strategic",
        "operational",
        "foundational",
        "supporting",
        "enabling",
    ]

    VALID_POLICY_TYPES = [
        "NIST",
        "CIS",
        "ISO",
        "SOX",
        "HIPAA",
        "GDPR",
        "PCI-DSS",
        "COBIT",
    ]

    VALID_VIOLATION_SEVERITIES = [
        "Critical",
        "High",
        "Medium",
        "Low",
    ]

    VALID_VIOLATION_STATUSES = [
        "Open",
        "In Progress",
        "Resolved",
    ]

    @staticmethod
    def validate_capability(data):
        """Validate capability data.

        Args:
            data: Dictionary containing capability fields

        Returns:
            Tuple of (is_valid, error_list)
        """
        errors = []

        # Name validation
        if "name" not in data or not data["name"]:
            errors.append("Capability name is required")
        elif not isinstance(data["name"], str):
            errors.append("Capability name must be a string")
        elif len(data["name"]) > 200:
            errors.append("Capability name must be 200 characters or less")
        elif len(data["name"].strip()) == 0:
            errors.append("Capability name cannot be empty")

        # Type validation
        # An empty category means none was chosen; it is not an invalid one.
        if data.get("type") not in (None, ""):
            if data["type"] not in EnterpriseValidationService.VALID_CAPABILITY_TYPES:
                errors.append(
                    f"Invalid capability type. Must be one of: {', '.join(EnterpriseValidationService.VALID_CAPABILITY_TYPES)}"
                )

        # Description validation
        if "description" in data:
            if not isinstance(data["description"], str):
                errors.append("Description must be a string")
            elif len(data["description"]) > 1000:
                errors.append("Description must be 1000 characters or less")

        # Health score validation
        if "health_score" in data:
            try:
                score = int(data["health_score"])
                if score < 0 or score > 100:
                    errors.append("Health score must be between 0 and 100")
            except (ValueError, TypeError):
                errors.append("Health score must be an integer between 0 and 100")

        return len(errors) == 0, errors

    @staticmethod
    def validate_compliance_policy(data):
        """Validate compliance policy data.

        Args:
            data: Dictionary containing policy fields

        Returns:
            Tuple of (is_valid, error_list)
        """
        errors = []

        # Name validation
        if "name" not in data or not data["name"]:
            errors.append("Policy name is required")
        elif not isinstance(data["name"], str):
            errors.append("Policy name must be a string")
        elif len(data["name"]) > 200:
            errors.append("Policy name must be 200 characters or less")

        # Type validation
        if "type" in data:
            if data["type"] not in EnterpriseValidationService.VALID_POLICY_TYPES:
                errors.append(
                    f"Invalid policy type. Must be one of: {', '.join(EnterpriseValidationService.VALID_POLICY_TYPES)}"
                )

        # Description validation
        if "description" in data:
            if not isinstance(data["description"], str):
                errors.append("Description must be a string")
            elif len(data["description"]) > 1000:
                errors.append("Description must be 1000 characters or less")

        return len(errors) == 0, errors

    @staticmethod
    def validate_compliance_violation(data):
        """Validate compliance violation data.

        Args:
            data: Dictionary containing violation fields

        Returns:
            Tuple of (is_valid, error_list)
        """
        errors = []

        # Policy ID validation
        if "policy_id" not in data or not data["policy_id"]:
            errors.append("Policy ID is required")
        else:
            try:
                policy_id = int(data["policy_id"])
                if policy_id <= 0:
                    errors.append("Policy ID must be a positive integer")
            except (ValueError, TypeError):
                errors.append("Policy ID must be an integer")

        # Severity validation
        if "severity" in data:
            if data["severity"] not in EnterpriseValidationService.VALID_VIOLATION_SEVERITIES:
                errors.append(
                    f"Invalid severity. Must be one of: {', '.join(EnterpriseValidationService.VALID_VIOLATION_SEVERITIES)}"
                )

        # Description validation
        if "description" in data:
            if not isinstance(data["description"], str):
                errors.append("Description must be a string")
            elif len(data["description"]) > 1000:
                errors.append("Description must be 1000 characters or less")

        return len(errors) == 0, errors

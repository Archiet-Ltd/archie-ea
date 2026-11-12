"""
Business rule validation for ApplicationComponent imports.
Cross-field dependencies and domain-specific rules.
"""

from datetime import date
from typing import Any, Dict, List, Tuple

from app.services.application_status import is_end_of_life

from .validation_result import RowValidationResult


class BusinessRuleValidator:
    """Validates business rules and cross-field dependencies"""

    @staticmethod
    def validate_row(row_data: Dict[str, Any], row_result: RowValidationResult) -> None:
        """
        Apply all business rules to a row.
        Modifies row_result in place to add issues.
        """
        # Rule 1: retirement_date only valid if status indicates retirement
        BusinessRuleValidator._validate_retirement_date(row_data, row_result)

        # Rule 2: go_live_date should be before retirement_date
        BusinessRuleValidator._validate_date_sequence(row_data, row_result)

        # Rule 3: Critical apps should have disaster recovery info
        BusinessRuleValidator._validate_critical_app_requirements(row_data, row_result)

        # Rule 4: Production apps should have owner information
        BusinessRuleValidator._validate_production_requirements(row_data, row_result)

        # Rule 5: RTO should typically be >= RPO
        BusinessRuleValidator._validate_rpo_rto(row_data, row_result)

        # Rule 6: Apps with PII should have data classification
        BusinessRuleValidator._validate_pii_classification(row_data, row_result)

    @staticmethod
    def _validate_retirement_date(row_data: Dict[str, Any], row_result: RowValidationResult):
        """Validate retirement_date is only set for retiring/retired apps"""
        retirement_date = row_data.get("retirement_date") or row_data.get("end_of_life_date")
        lifecycle_status = row_data.get("lifecycle_status")
        deployment_status = row_data.get("deployment_status")

        if retirement_date:
            valid_lifecycle = {"sunset", "retired", "deprecated", "maintenance"}

            lifecycle_ok = lifecycle_status and lifecycle_status.lower() in valid_lifecycle
            deployment_ok = is_end_of_life(deployment_status)

            if not lifecycle_ok and not deployment_ok:
                row_result.add_warning(
                    "retirement_date",
                    f"retirement_date is set but lifecycle_status is '{lifecycle_status}'. "
                    "Consider setting status to 'sunset' or 'retired'.",
                    original_value=retirement_date,
                )

    @staticmethod
    def _validate_date_sequence(row_data: Dict[str, Any], row_result: RowValidationResult):
        """Validate go_live_date < retirement_date"""
        go_live = row_data.get("go_live_date")
        retirement = row_data.get("retirement_date") or row_data.get("end_of_life_date")

        if go_live and retirement:
            if isinstance(go_live, date) and isinstance(retirement, date):
                if retirement < go_live:
                    row_result.add_error(
                        "retirement_date",
                        f"retirement_date ({retirement}) cannot be before go_live_date ({go_live})",
                        original_value=retirement,
                    )

    @staticmethod
    def _validate_critical_app_requirements(
        row_data: Dict[str, Any], row_result: RowValidationResult
    ):
        """Critical apps should have DR information"""
        criticality = str(row_data.get("business_criticality", "")).lower()

        if criticality in ["critical", "high"]:
            has_rpo = row_data.get("rpo_hours") is not None
            has_rto = row_data.get("rto_hours") is not None
            has_dr = row_data.get("disaster_recovery_enabled")

            if not has_rpo and not has_rto and not has_dr:
                row_result.add_warning(
                    "business_criticality",
                    f"Application marked as '{criticality}' criticality but missing "
                    "disaster recovery information (RPO/RTO/DR enabled)",
                    original_value=criticality,
                )

    @staticmethod
    def _validate_production_requirements(
        row_data: Dict[str, Any], row_result: RowValidationResult
    ):
        """Production apps should have owner information"""
        deployment_status = str(row_data.get("deployment_status", "")).lower()
        lifecycle_status = str(row_data.get("lifecycle_status", "")).lower()

        if deployment_status == "production" or lifecycle_status == "production":
            has_business_owner = row_data.get("business_owner")
            has_technical_owner = row_data.get("technical_owner")
            has_tech_lead = row_data.get("technical_lead")
            has_dev_team = row_data.get("development_team")

            if not any([has_business_owner, has_technical_owner, has_tech_lead, has_dev_team]):
                row_result.add_warning(
                    "deployment_status",
                    "Production application should have owner information "
                    "(business_owner, technical_owner, technical_lead, or development_team)",
                    original_value=deployment_status or lifecycle_status,
                )

    @staticmethod
    def _validate_rpo_rto(row_data: Dict[str, Any], row_result: RowValidationResult):
        """RTO should typically be >= RPO"""
        rpo = row_data.get("rpo_hours")
        rto = row_data.get("rto_hours")

        if rpo is not None and rto is not None:
            try:
                rpo_val = int(rpo) if not isinstance(rpo, int) else rpo
                rto_val = int(rto) if not isinstance(rto, int) else rto

                if rto_val < rpo_val:
                    row_result.add_warning(
                        "rto_hours",
                        f"RTO ({rto_val}h) is less than RPO ({rpo_val}h). Typically RTO >= RPO.",
                        original_value=rto,
                        suggested_value=rpo,
                    )
            except (ValueError, TypeError):
                pass  # Skip if values can't be compared

    @staticmethod
    def _validate_pii_classification(row_data: Dict[str, Any], row_result: RowValidationResult):
        """Apps processing PII should have appropriate data classification"""
        pii_processed = row_data.get("pii_data_processed")
        data_classification = str(row_data.get("data_classification", "")).lower()

        if pii_processed is True:
            if data_classification and data_classification in ["public", "internal"]:
                row_result.add_warning(
                    "data_classification",
                    f"Application processes PII but data_classification is '{data_classification}'. "
                    "Consider setting to 'Confidential' or 'Restricted'.",
                    original_value=data_classification,
                    suggested_value="Confidential",
                )


class CrossRecordValidator:
    """Validates consistency across multiple records"""

    @staticmethod
    def validate_batch(rows: List[Dict[str, Any]]) -> List[Tuple[int, str, str]]:
        """
        Validate cross-record consistency.

        Returns:
            List of (row_number, field_name, message) tuples
        """
        issues = []

        # Track seen names for duplicate detection
        seen_names: Dict[str, int] = {}
        seen_codes: Dict[str, int] = {}

        for idx, row in enumerate(rows, start=2):  # Start at 2 (header is row 1)
            name = str(row.get("name", "")).lower().strip()
            code = row.get("application_code") or row.get("app_id")
            if code:
                code = str(code).lower().strip()

            # Check for duplicate names
            if name:
                if name in seen_names:
                    issues.append(
                        (
                            idx,
                            "name",
                            f"Duplicate application name '{name}' (also in row {seen_names[name]})",
                        )
                    )
                else:
                    seen_names[name] = idx

            # Check for duplicate codes
            if code:
                if code in seen_codes:
                    issues.append(
                        (
                            idx,
                            "application_code",
                            f"Duplicate application code '{code}' (also in row {seen_codes[code]})",
                        )
                    )
                else:
                    seen_codes[code] = idx

        return issues

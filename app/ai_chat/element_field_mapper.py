"""
Element Field Mapper
Maps extracted element data to model-specific field names for proper database creation.

This mapper handles ALL ArchiMate 3.2 element types from MODEL_REGISTRY:
- Motivation Layer: Stakeholder, Driver, Assessment, Goal, Outcome, Principle, Requirement,
                    Constraint, Meaning, Value
- Strategy Layer: Resource, Capability, ValueStream, CourseOfAction
- Business Layer: BusinessActor, BusinessRole, BusinessCollaboration, BusinessInterface,
                  BusinessProcess, BusinessFunction, BusinessInteraction, BusinessEvent,
                  BusinessService, BusinessObject, Contract, Representation, Product
- Application Layer: ApplicationComponent, ApplicationInterface, ApplicationService,
                     ApplicationFunction, ApplicationProcess, ApplicationInteraction,
                     ApplicationEvent, ApplicationCollaboration, DataObject
- Technology Layer: Node, Device, SystemSoftware, TechnologyService, TechnologyInterface,
                    Path, CommunicationNetwork
- Implementation Layer: WorkPackage, Deliverable, Plateau, Gap

Works for ALL document types (general architecture, application-specific, vendor-specific, etc.)
"""

from typing import Any, Dict, Optional
from app.models.archimate_element_types import is_application_kind


def map_element_data_to_model_fields(
    element_type: str, elem: Dict, user_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Map extracted element data to model-specific field names.

    Args:
        element_type: The ArchiMate element type (e.g., "Principle", "Requirement", "Goal")
        elem: Dictionary containing extracted element data with keys like "name", "description", etc.
        user_id: Optional user ID for created_by_id field

    Returns:
        Dictionary with model-specific field names ready for model instantiation
    """
    name = elem.get("name", "").strip()
    description = elem.get("description", "")

    # Base element data - will be customized per model
    element_data: Dict[str, Any] = {}

    # Model-specific field mappings
    if element_type == "Principle":
        # Principle uses 'statement' instead of 'description'
        element_data["name"] = name
        element_data["statement"] = description if description else name  # Use name as fallback
        element_data["rationale"] = elem.get("rationale", "")
        element_data["implications"] = elem.get("implications", "")
        element_data["category"] = elem.get("category", "Architecture")
        element_data["enforcement_level"] = elem.get("enforcement_level", "SHOULD")
        element_data["status"] = elem.get("status", "draft")

    elif element_type == "Requirement":
        # Requirement uses 'title' instead of 'name'
        element_data["title"] = name
        element_data["description"] = description
        element_data["type"] = elem.get("type", "functional")  # functional, non-functional
        element_data["priority"] = elem.get("priority", "medium")
        element_data["category"] = elem.get("category", "")
        element_data["rationale"] = elem.get("rationale", "")
        element_data["verification_method"] = elem.get("verification_method", "")
        element_data["compliance_status"] = elem.get("compliance_status", "draft")

    elif element_type == "Goal":
        element_data["name"] = name
        element_data["description"] = description
        element_data["goal_type"] = elem.get("goal_type", "")
        element_data["category"] = elem.get("category", "")
        element_data["time_horizon"] = elem.get("time_horizon", "")
        element_data["target_value"] = elem.get("target_value", "")
        element_data["achievement_status"] = elem.get("achievement_status", "not_started")

    elif element_type == "Driver":
        element_data["name"] = name
        element_data["description"] = description
        element_data["driver_type"] = elem.get("driver_type", "")
        element_data["source"] = elem.get("source", "")
        element_data["impact"] = elem.get("impact", "")

    elif element_type == "Outcome":
        element_data["name"] = name
        element_data["description"] = description
        element_data["outcome_type"] = elem.get("outcome_type", "")
        element_data["time_horizon"] = elem.get("time_horizon", "")

    elif element_type == "Stakeholder":
        element_data["name"] = name
        element_data["description"] = description
        element_data["stakeholder_type"] = elem.get("stakeholder_type", "")
        element_data["influence"] = elem.get("influence", "")

    elif element_type == "Assessment":
        element_data["name"] = name
        element_data["description"] = description
        element_data["assessment_type"] = elem.get("assessment_type", "")
        element_data["outcome"] = elem.get("outcome", "")

    elif element_type == "Constraint":
        element_data["name"] = name
        element_data["description"] = description
        element_data["constraint_type"] = elem.get("constraint_type", "")
        element_data["severity"] = elem.get("severity", "")

    elif element_type == "BusinessActor":
        element_data["name"] = name
        element_data["description"] = description
        element_data["actor_type"] = elem.get("actor_type", "")
        element_data["location"] = elem.get("location", "")

    elif element_type == "BusinessProcess":
        element_data["name"] = name
        element_data["description"] = description
        element_data["process_type"] = elem.get("process_type", "")
        element_data["business_value"] = elem.get("business_value", "")

    elif element_type == "BusinessService":
        element_data["name"] = name
        element_data["description"] = description
        element_data["service_type"] = elem.get("service_type", "")
        element_data["service_level"] = elem.get("service_level", "")

    elif element_type == "BusinessFunction":
        element_data["name"] = name
        element_data["description"] = description
        element_data["function_type"] = elem.get("function_type", "")

    elif is_application_kind(element_type):
        element_data["name"] = name
        element_data["description"] = description
        element_data["technology_stack"] = elem.get("technology_stack", "")

        # deployment_type is not a direct field on ApplicationComponent
        # Store it in custom_attributes JSON if provided, for potential vendor relationship use
        deployment_type = elem.get("deployment_type")
        if deployment_type:
            import json

            # Initialize custom_attributes with deployment_type if it doesn't exist
            # This preserves the data for vendor product relationships
            custom_attrs = {}
            if elem.get("custom_attributes"):
                try:
                    custom_attrs = (
                        json.loads(elem["custom_attributes"])
                        if isinstance(elem["custom_attributes"], str)
                        else elem["custom_attributes"]
                    )
                except (json.JSONDecodeError, TypeError):
                    custom_attrs = {}
            custom_attrs["deployment_type"] = deployment_type
            element_data["custom_attributes"] = json.dumps(custom_attrs)

    elif element_type == "ApplicationInterface":
        element_data["name"] = name
        element_data["description"] = description
        element_data["interface_type"] = elem.get("interface_type", "")
        element_data["protocol"] = elem.get("protocol", "")

    elif element_type == "ApplicationService":
        element_data["name"] = name
        element_data["description"] = description
        element_data["service_type"] = elem.get("service_type", "")
        element_data["api_version"] = elem.get("api_version", "")

    elif element_type == "DataObject":
        element_data["name"] = name
        element_data["description"] = description
        element_data["data_type"] = elem.get("data_type", "")
        element_data["format"] = elem.get("format", "")

    # Handle remaining element types with standard 'name' and 'description' fields
    # This covers all other ArchiMate element types from MODEL_REGISTRY:
    # - Motivation: Meaning, Value
    # - Strategy: Resource, Capability, ValueStream, CourseOfAction
    # - Business: BusinessRole, BusinessCollaboration, BusinessInterface, BusinessInteraction,
    #             BusinessEvent, BusinessObject, Contract, Representation, Product
    # - Application: ApplicationFunction, ApplicationProcess, ApplicationInteraction,
    #                ApplicationEvent, ApplicationCollaboration
    # - Technology: Node, Device, SystemSoftware, TechnologyService, TechnologyInterface,
    #               Path, CommunicationNetwork
    # - Implementation: WorkPackage, Deliverable, Plateau, Gap
    else:
        # Default mapping for all models that use standard 'name' and 'description'
        element_data["name"] = name
        element_data["description"] = description

        # Add any additional properties from the extracted element
        # This allows custom properties to be passed through for any element type
        properties = elem.get("properties", {})
        if isinstance(properties, dict):
            # Add properties that might be model fields (be careful not to override required fields)
            for key, value in properties.items():
                if key not in element_data:  # Don't override already set fields
                    element_data[key] = value

    # Note: created_by_id is handled in the calling code by checking hasattr(model_class, "created_by_id")
    # This is intentional to avoid errors if the model doesn't support this field

    return element_data


def get_existence_check_field(element_type: str) -> str:
    """
    Get the field name used for checking element existence.

    Args:
        element_type: The ArchiMate element type

    Returns:
        Field name to use for querying (either "name" or "title")
    """
    if element_type == "Requirement":
        return "title"
    else:
        return "name"


def check_element_exists(model_class, element_type: str, name: str):
    """
    Check if an element already exists using the correct field name.

    Args:
        model_class: The SQLAlchemy model class
        element_type: The ArchiMate element type
        name: The element name/title to check

    Returns:
        Existing element instance or None
    """
    field_name = get_existence_check_field(element_type)

    if field_name == "title":
        return model_class.query.filter_by(title=name).first()
    else:
        return model_class.query.filter_by(name=name).first()

"""Seed data for typed ArchiMate element properties."""

PROPERTY_TEMPLATES = [
    {
        "archimate_type": "ApplicationInterface",
        "property_key": "rate_limit",
        "display_name": "Rate limit",
        "property_type": "number",
        "unit": "req/min",
        "default_value": "1000 req/min",
        "required_for_tier": "standard",
        "help_text": "Requests per minute.",
        "sort_order": 10,
    },
    {
        "archimate_type": "ApplicationInterface",
        "property_key": "authentication",
        "display_name": "Authentication",
        "property_type": "enum",
        "enum_options": ["OAuth2", "mTLS", "API key", "None"],
        "default_value": "OAuth2",
        "required_for_tier": "standard",
        "help_text": "How the interface authenticates callers.",
        "sort_order": 20,
    },
    {
        "archimate_type": "ApplicationService",
        "property_key": "availability_target",
        "display_name": "Availability target",
        "property_type": "number",
        "unit": "%",
        "default_value": "99.9%",
        "required_for_tier": "standard",
        "help_text": "Target availability percentage.",
        "sort_order": 10,
    },
    {
        "archimate_type": "ApplicationComponent",
        "property_key": "estimated_users",
        "display_name": "Estimated users",
        "property_type": "number",
        "unit": "users",
        "default_value": "100 users",
        "required_for_tier": "important",
        "help_text": "Expected active users.",
        "sort_order": 30,
    },
]


# The privacy-impact-assessment status of a processing activity. A processing activity is an
# information object of the model; element types are stored both as ArchiMate CamelCase and as
# snake_case, so the template is registered under both spellings.
for _activity_type in (
    "DataObject", "BusinessObject", "Representation", "Contract", "Product",
    "data_object", "business_object", "representation", "contract", "product",
):
    PROPERTY_TEMPLATES.append({
        "archimate_type": _activity_type,
        "property_key": "privacy_assessment_status",
        "display_name": "Privacy assessment status",
        "property_type": "enum",
        "enum_options": ["not_started", "in_progress", "completed", "not_required"],
        "default_value": None,
        "required_for_tier": "important",
        "help_text": "Where the data protection impact assessment for this processing activity stands.",
        "sort_order": 50,
    })

"""Marshmallow request schemas for high-risk JSON API endpoints.

Task: T-031 — Add marshmallow schema validation to high-risk JSON API endpoints.

Covers:
  - ApplicationCreateSchema   → applications create/update
  - BatchImportOptionsSchema  → batch import job creation (JSON body portion)
  - ChatMessageSchema         → AI chat /message endpoint
"""

from marshmallow import Schema, ValidationError, fields, validate, validates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_and_validate(schema: Schema, data: dict) -> tuple:
    """
    Deserialise and validate *data* against *schema*.

    Returns:
        (validated_data, None)  on success
        (None, flask_response)  on failure — caller should return the response
    """
    from flask import jsonify

    try:
        validated = schema.load(data)
        return validated, None
    except ValidationError as exc:
        return None, (
            jsonify({"success": False, "errors": exc.messages}),
            400,
        )


# ---------------------------------------------------------------------------
# Application create / update
# ---------------------------------------------------------------------------

VALID_CRITICALITY = ["critical", "high", "medium", "low", "unknown"]
VALID_DEPLOYMENT_STATUS = [
    "production", "staging", "development", "deprecated",
    "retired", "planned", "unknown",
]
VALID_COMPONENT_TYPES = [
    "application", "service", "platform", "infrastructure",
    "integration", "database", "other",
]


class ApplicationCreateSchema(Schema):
    """Schema for POST /applications/create JSON body."""

    name = fields.Str(
        required=True,
        validate=validate.Length(min=1, max=255),
        metadata={"description": "Application name (required)"},
    )
    description = fields.Str(
        load_default=None,
        validate=validate.Length(max=5000),
        allow_none=True,
    )
    application_code = fields.Str(
        load_default=None,
        validate=validate.Length(max=50),
        allow_none=True,
    )
    application_type = fields.Str(
        load_default=None,
        validate=validate.OneOf(VALID_COMPONENT_TYPES),
        allow_none=True,
    )
    criticality = fields.Str(
        load_default=None,
        validate=validate.OneOf(VALID_CRITICALITY),
        allow_none=True,
    )
    deployment_status = fields.Str(
        load_default=None,
        validate=validate.OneOf(VALID_DEPLOYMENT_STATUS),
        allow_none=True,
    )
    technology_stack = fields.Str(
        load_default=None,
        validate=validate.Length(max=500),
        allow_none=True,
    )
    business_owner = fields.Str(
        load_default=None,
        validate=validate.Length(max=255),
        allow_none=True,
    )
    technical_owner = fields.Str(
        load_default=None,
        validate=validate.Length(max=255),
        allow_none=True,
    )
    vendor_id = fields.Int(
        load_default=None,
        validate=validate.Range(min=1),
        allow_none=True,
    )
    lifecycle_status = fields.Str(
        load_default=None,
        validate=validate.Length(max=50),
        allow_none=True,
    )
    hosting_model = fields.Str(
        load_default=None,
        validate=validate.Length(max=50),
        allow_none=True,
    )
    annual_cost = fields.Float(
        load_default=None,
        validate=validate.Range(min=0),
        allow_none=True,
    )
    user_count = fields.Int(
        load_default=None,
        validate=validate.Range(min=0),
        allow_none=True,
    )
    # ARCH-030 escape hatch. Marshmallow's default `unknown` is RAISE, so the
    # duplicate-guard opt-in has to be a declared field or a JSON body carrying
    # it would 400 before the route ever sees it.
    allow_duplicate = fields.Bool(load_default=False)


class ApplicationUpdateSchema(ApplicationCreateSchema):
    """Schema for PUT/PATCH application update — name is optional."""

    name = fields.Str(
        load_default=None,
        validate=validate.Length(min=1, max=255),
        allow_none=True,
    )


# ---------------------------------------------------------------------------
# Batch import job creation (JSON body options — file is multipart)
# ---------------------------------------------------------------------------

VALID_ARCHIMATE_MODES = ["quick", "standard", "comprehensive"]


class BatchImportOptionsSchema(Schema):
    """Schema for JSON options in batch import job creation.

    Note: The file itself is validated separately via multipart form.
    This schema validates the JSON-encoded options that may accompany it.
    """

    batch_size = fields.Int(
        load_default=20,
        validate=validate.Range(min=1, max=500),
    )
    archimate_mode = fields.Str(
        load_default="standard",
        validate=validate.OneOf(VALID_ARCHIMATE_MODES),
    )
    enable_ai_generation = fields.Bool(load_default=True)
    budget_limit_usd = fields.Float(
        load_default=None,
        validate=validate.Range(min=0, max=100000),
        allow_none=True,
    )
    confidence_threshold = fields.Float(
        load_default=0.85,
        validate=validate.Range(min=0.0, max=1.0),
    )
    auto_approve_high_confidence = fields.Bool(load_default=False)

    @validates("confidence_threshold")
    def validate_threshold(self, value, **kwargs):
        if value < 0.5:
            raise ValidationError(
                "confidence_threshold below 0.5 may cause excessive auto-approvals."
            )


# ---------------------------------------------------------------------------
# AI chat message
# ---------------------------------------------------------------------------

VALID_CHAT_DOMAINS = [
    "general", "capabilities", "applications",
    "vendors", "architecture", "compliance",
    "technology", "business_capability", "gap_analysis", "vendor_intelligence",
    "smart_search", "data_architecture",
]


class ChatMessageSchema(Schema):
    """Schema for POST /api/ai-chat/message JSON body."""

    message = fields.Str(
        required=True,
        validate=validate.Length(min=1, max=10000),
        metadata={"description": "User message content (required)"},
    )
    domain = fields.Str(
        load_default="general",
        validate=validate.OneOf(VALID_CHAT_DOMAINS),
    )
    template_name = fields.Str(
        load_default="General Inquiry",
        validate=validate.Length(max=100),
    )
    element_id = fields.Int(
        load_default=None,
        validate=validate.Range(min=1),
        allow_none=True,
    )
    context_type = fields.Str(
        load_default=None,
        validate=validate.Length(max=50),
        allow_none=True,
    )
    persona = fields.Str(
        load_default=None,
        validate=validate.Length(max=50),
        allow_none=True,
    )
    model = fields.Str(
        load_default=None,
        validate=validate.Length(max=200),
        allow_none=True,
    )
    # ENT-085: Vision/multimodal support — optional base64-encoded image data
    image_data = fields.Str(
        load_default=None,
        allow_none=True,
        metadata={"description": "Base64-encoded image data for vision analysis"},
    )
    image_media_type = fields.Str(
        load_default=None,
        validate=validate.OneOf(
            ["image/png", "image/jpeg", "image/gif", "image/webp"],
        ),
        allow_none=True,
        metadata={"description": "MIME type of the attached image"},
    )
    solution_id = fields.Int(
        load_default=None,
        validate=validate.Range(min=1),
        allow_none=True,
        metadata={"description": "Optional solution ID — grounds chat responses in a specific solution's context"},
    )
    # Allow other optional fields (instance_id, document_context) to pass through
    instance_id = fields.Int(load_default=None, allow_none=True)
    document_context = fields.Dict(load_default=None, allow_none=True)
    # A starter: a question chip with a picked element, answered without a model.
    starter = fields.Dict(load_default=None, allow_none=True)
    # AIC-312: Workbench workspace ID — grounds chat in a workbench session
    workspace_id = fields.Int(
        load_default=None,
        validate=validate.Range(min=1),
        allow_none=True,
        metadata={"description": "Workbench workspace ID — grounds chat in a workbench session"},
    )

    @validates("message")
    def validate_message_content(self, value: str, **kwargs):
        stripped = value.strip()
        if not stripped:
            raise ValidationError("Message cannot be blank or whitespace only.")
        if len(stripped) < 2:
            raise ValidationError("Message must be at least 2 characters.")


class PageGuideContextSchema(Schema):
    """Schema for page guide history/context payloads."""

    page_key = fields.Str(
        required=True,
        validate=validate.Length(min=3, max=100),
    )
    scope_key = fields.Str(
        required=True,
        validate=validate.Length(min=3, max=180),
    )


class PageGuideMessageSchema(PageGuideContextSchema):
    """Schema for POST /ai-chat/guide/message JSON body."""

    message = fields.Str(
        required=True,
        validate=validate.Length(min=2, max=4000),
    )
    page_title = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(max=200),
    )


# ---------------------------------------------------------------------------
# Singleton instances (import these in routes)
# ---------------------------------------------------------------------------

application_create_schema = ApplicationCreateSchema()
application_update_schema = ApplicationUpdateSchema()
batch_import_options_schema = BatchImportOptionsSchema()
chat_message_schema = ChatMessageSchema()

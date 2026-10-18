"""
Admin Forms (migrated).

Migrated from: app/admin/forms.py
All 6 WTForms classes preserved exactly.
"""
from flask_wtf import FlaskForm
from wtforms import ValidationError
from wtforms.fields import (
    BooleanField,
    EmailField,
    FloatField,
    HiddenField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import Email, EqualTo, InputRequired, Length, NumberRange, Optional
from wtforms_sqlalchemy.fields import QuerySelectField

from app.extensions import db
from app.models import Role, User


class FeatureFlagForm(FlaskForm):
    """Form for creating/editing feature flags."""

    key = StringField(
        "Feature Key",
        validators=[InputRequired(), Length(1, 100)],
        description="Unique identifier (e.g., solutions_management)",
    )
    name = StringField(
        "Name", validators=[InputRequired(), Length(1, 200)], description="Human-readable name"
    )
    description = TextAreaField("Description", description="What this feature does")

    feature_type = SelectField(
        "Feature Type",
        choices=[
            ("sidebar_section", "Sidebar Section"),
            ("route", "Route"),
            ("blueprint", "Blueprint"),
            ("functionality", "Functionality"),
        ],
        validators=[InputRequired()],
    )

    state = SelectField(
        "State",
        choices=[
            ("alpha", "Alpha - Early development"),
            ("beta", "Beta - Testing phase"),
            ("stable", "Stable - Production ready"),
            ("deprecated", "Deprecated - Will be removed"),
            ("maintenance_mode", "Maintenance Mode - Temporarily disabled"),
        ],
        validators=[InputRequired()],
    )

    enabled = BooleanField("Enabled")

    sidebar_label = StringField(
        "Sidebar Label",
        validators=[Optional(), Length(0, 100)],
        description="Label shown in sidebar (if sidebar_section type)",
    )
    sidebar_icon = StringField(
        "Sidebar Icon", validators=[Optional(), Length(0, 50)], description="Lucide icon name"
    )
    routes = TextAreaField(
        "Routes (JSON)", description='JSON list of route patterns: ["/solutions/*"]'
    )

    parent_id = IntegerField(
        "Parent Feature ID",
        validators=[Optional()],
        description="ID of parent feature for hierarchical features",
    )
    sort_order = IntegerField("Sort Order", validators=[Optional()], default=0)

    submit = SubmitField("Save Feature Flag")


class ChangeUserEmailForm(FlaskForm):
    email = EmailField("New email", validators=[InputRequired(), Length(1, 64), Email()])
    submit = SubmitField("Update email")

    def validate_email(self, field):
        if User.find_by_email(field.data):
            raise ValidationError("Email already registered.")


class ChangeAccountTypeForm(FlaskForm):
    role = QuerySelectField(
        "New account type",
        validators=[InputRequired()],
        get_label="name",
        query_factory=lambda: db.session.query(Role).order_by("permissions"),
    )
    submit = SubmitField("Update role")


class ChangeBusinessUnitForm(FlaskForm):
    """Assign a person to a unit (or clear the assignment — the empty choice
    means "no unit"). The unit choices are populated by the route from the
    caller's organisation only, so a unit from another organisation can never
    be chosen."""

    business_unit_id = SelectField(
        "Business unit",
        validators=[Optional()],
        choices=[],
        description="The unit this person belongs to. Empty means no unit.",
    )
    submit = SubmitField("Update business unit")


class InviteUserForm(FlaskForm):
    role = QuerySelectField(
        "Account type",
        validators=[InputRequired()],
        get_label="name",
        query_factory=lambda: db.session.query(Role).order_by("permissions"),
    )
    first_name = StringField("First name", validators=[InputRequired(), Length(1, 64)])
    last_name = StringField("Last name", validators=[InputRequired(), Length(1, 64)])
    email = EmailField("Email", validators=[InputRequired(), Length(1, 64), Email()])
    submit = SubmitField("Invite")

    def validate_email(self, field):
        if User.find_by_email(field.data):
            raise ValidationError("Email already registered.")


class NewUserForm(InviteUserForm):
    password = PasswordField(
        "Password", validators=[InputRequired(), EqualTo("password2", "Passwords must match.")]
    ,
        render_kw={"autocomplete": "new-password"},
    )
    password2 = PasswordField("Confirm password", validators=[InputRequired()],
        render_kw={"autocomplete": "new-password"},
    )

    submit = SubmitField("Create")


class APISettingsForm(FlaskForm):
    """Form for managing API settings for LLM providers."""

    provider = SelectField(
        "Provider",
        choices=[
            ("anthropic", "Anthropic (Claude)"),
            ("openai", "OpenAI (GPT)"),
            ("gemini", "Google Gemini"),
            ("azure", "Azure OpenAI"),
            ("huggingface", "Hugging Face"),
            ("deepseek", "DeepSeek"),
            ("openrouter", "OpenRouter (65+ Models)"),
            ("custom", "Custom API"),
            ("jira", "JIRA"),
        ],
        validators=[InputRequired()],
        description="Select the LLM provider",
    )
    key_label = StringField(
        "Key Label",
        validators=[Optional(), Length(0, 100)],
        description="Optional label to distinguish multiple keys for the same provider (e.g. 'Production', 'Dev', 'Fallback'). Leave blank for the primary key.",
            # Chrome matches a text field followed by a password field as a
        # login form; this is the text half of that pair.
        render_kw={"autocomplete": "off"},
    )
    api_key = PasswordField(
        "API Key",
        validators=[Optional(), Length(0, 500)],
        description="Your API key for this provider (required for new settings, optional when editing)",
        # A third-party secret, not the user's password. Without this,
        # Chrome pattern-matches the preceding text field plus this one as a
        # login and offers a SAVED EMAIL AND PASSWORD -- an administrator who
        # misses the autofill highlight submits their own credentials as an
        # API key, which the backend then stores and uses. Seen live 30 Aug 2026.
        render_kw={"autocomplete": "new-password"},
    )
    settings_id = HiddenField()

    def validate_api_key(self, field):
        """Validate API key - required for new settings, optional when editing."""
        # This validation will be handled in the view for better control
        pass

    enabled = BooleanField("Enabled", default=True, description="Enable or disable this provider")
    default_model = StringField(
        "Default Model",
        validators=[Optional(), Length(0, 100)],
        description="Default model to use (e.g., claude - 3 - 5-sonnet - 20241022)",
    )
    max_tokens = IntegerField(
        "Max Tokens",
        validators=[Optional(), NumberRange(min=1, max=100000)],
        default=4000,
        description="Maximum tokens for responses",
    )
    temperature = FloatField(
        "Temperature",
        validators=[Optional(), NumberRange(min=0.0, max=2.0)],
        default=0.7,
        description="Temperature for model responses (0.0 - 2.0)",
    )
    # JIRA-specific fields
    jira_url = StringField(
        "JIRA URL",
        validators=[Optional(), Length(0, 255)],
        description="JIRA instance URL (e.g., https://your-domain.atlassian.net)",
    )
    jira_email = EmailField(
        "JIRA Email",
        validators=[Optional(), Email(), Length(0, 255)],
        description="Email for JIRA authentication",
    )

    # Hugging Face specific fields
    hf_model_id = StringField(
        "Hugging Face Model ID",
        validators=[Optional(), Length(0, 255)],
        description="Model ID (e.g., meta-llama/Llama - 3.1 - 8B-Instruct, mistralai/Mistral - 7B-Instruct-v0.2)",
    )
    hf_endpoint_url = StringField(
        "Hugging Face Endpoint URL",
        validators=[Optional(), Length(0, 500)],
        description="Custom endpoint URL (optional, uses default Hugging Face API if not provided)",
    )

    # Custom API specific fields
    custom_endpoint_url = StringField(
        "API Endpoint URL",
        validators=[Optional(), Length(0, 500)],
        description="API endpoint URL (e.g., https://api.example.com/v1/endpoint)",
    )
    custom_auth_method = SelectField(
        "Authentication Method",
        choices=[
            ("bearer", "Bearer Token"),
            ("api_key_header", "API Key Header"),
            ("basic_auth", "Basic Auth"),
            ("none", "No Authentication"),
        ],
        default="bearer",
        validators=[Optional()],
        description="How to authenticate with the API",
    )
    custom_headers = TextAreaField(
        "Custom Headers (JSON)",
        validators=[Optional(), Length(0, 1000)],
        description='Additional headers in JSON format (e.g., {"Content-Type": "application/json"})',
    )

    submit = SubmitField("Save Settings")
    test = SubmitField("Test Connection")

"""
Application Management Forms

WTForms for CRUD operations with CSRF protection.
"""

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    DecimalField,
    IntegerField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional


class ApplicationComponentForm(FlaskForm):
    """Form for creating/editing Application Components"""

    # Core fields
    name = StringField(
        "Application Name",
        validators=[DataRequired(), Length(min=1, max=255)],
        render_kw={"placeholder": "e.g., Customer Portal"},
    )

    description = TextAreaField(
        "Description",
        validators=[Optional(), Length(max=5000)],
        render_kw={"rows": 4, "placeholder": "Describe the application purpose and functionality"},
    )

    component_type = SelectField(
        "Component Type",
        choices=[
            ("", "-- Select Type --"),
            ("Web Application", "Web Application"),
            ("Mobile App", "Mobile App"),
            ("Desktop Application", "Desktop Application"),
            ("Microservice", "Microservice"),
            ("Backend Service", "Backend Service"),
            ("Integration Service", "Integration Service"),
            ("Batch Job", "Batch Job"),
        ],
        validators=[DataRequired()],
    )

    archimate_element_type = SelectField(
        "ArchiMate Element Type",
        choices=[
            ("ApplicationComponent", "Application Component"),
            ("ApplicationService", "Application Service"),
            ("ApplicationFunction", "Application Function"),
            ("ApplicationProcess", "Application Process"),
            ("ApplicationInterface", "Application Interface"),
            ("ApplicationCollaboration", "Application Collaboration"),
            ("ApplicationInteraction", "Application Interaction"),
            ("ApplicationEvent", "Application Event"),
        ],
        default="ApplicationComponent",
        validators=[Optional()],
    )

    application_category = SelectField(
        "Category",
        choices=[
            ("", "-- Select Category --"),
            ("Enterprise Application", "Enterprise Application"),
            ("Business Application", "Business Application"),
            ("System Application", "System Application"),
            ("Custom", "Custom"),
            ("COTS", "COTS (Commercial Off-The-Shelf)"),
            ("SaaS", "SaaS"),
        ],
        validators=[Optional()],
    )

    technology_stack = StringField(
        "Technology Stack",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "e.g., React, Node.js, PostgreSQL"},
    )

    version = StringField(
        "Version", validators=[Optional(), Length(max=50)], render_kw={"placeholder": "e.g., 2.1.0"}
    )

    deployment_status = SelectField(
        "Deployment Status",
        choices=[
            ("planned", "Planned"),
            ("development", "Development"),
            ("testing", "Testing"),
            ("staging", "Staging"),
            ("production", "Production"),
            ("deprecated", "Deprecated"),
            ("retired", "Retired"),
        ],
        default="planned",
        validators=[DataRequired()],
    )

    business_domain = StringField(
        "Business Domain",
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "e.g., Sales, Finance, Manufacturing"},
    )

    business_owner = StringField(
        "Business Owner",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Name of business owner"},
    )

    product_manager = StringField(
        "Product Manager",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Name of product manager"},
    )

    development_team = StringField(
        "Development Team",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Name of development team"},
    )

    technical_lead = StringField(
        "Technical Lead",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Name of technical lead"},
    )

    architecture_domain = StringField(
        "Architecture Domain",
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "e.g., Enterprise, Data, Security"},
    )

    support_team = StringField(
        "Support Team",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Name of support team"},
    )

    user_count = IntegerField(
        "User Count", validators=[Optional()], render_kw={"placeholder": "Number of active users"}
    )

    business_criticality = SelectField(
        "Business Criticality",
        choices=[
            ("", "-- Select Criticality --"),
            ("Critical", "Critical"),
            ("High", "High"),
            ("Medium", "Medium"),
            ("Low", "Low"),
        ],
        validators=[Optional()],
    )

    programming_languages = StringField(
        "Programming Languages",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "e.g., Python, JavaScript, Java"},
    )
    frameworks = StringField(
        "Frameworks",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "e.g., Flask, React, Spring Boot"},
    )
    primary_database = StringField(
        "Primary Database",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "e.g., PostgreSQL, MySQL"},
    )
    cache_technology = StringField(
        "Cache Technology",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "e.g., Redis, Memcached"},
    )
    message_queue = StringField(
        "Message Queue",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "e.g., RabbitMQ, Kafka"},
    )

    # Architecture Style
    architecture_style = SelectField(
        "Architecture Style",
        choices=[
            ("", "-- Select Style --"),
            ("Monolithic", "Monolithic"),
            ("Microservices", "Microservices"),
            ("SOA", "SOA"),
            ("Serverless", "Serverless"),
            ("Event-Driven", "Event-Driven"),
        ],
        validators=[Optional()],
    )

    # Version & Repository
    repository_type = SelectField(
        "Repository Type",
        choices=[
            ("", "-- Select Type --"),
            ("GitHub", "GitHub"),
            ("GitLab", "GitLab"),
            ("Bitbucket", "Bitbucket"),
            ("Azure DevOps", "Azure DevOps"),
        ],
        validators=[Optional()],
    )
    version_control_url = StringField(
        "Version Control URL",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "https://github.com/..."},
    )
    main_branch = StringField(
        "Main Branch", validators=[Optional(), Length(max=100)], render_kw={"placeholder": "main"}
    )

    # Deployment
    deployment_model = SelectField(
        "Deployment Model",
        choices=[
            ("", "-- Select Model --"),
            ("On-Premise", "On-Premise"),
            ("Cloud", "Cloud"),
            ("Hybrid", "Hybrid"),
            ("SaaS", "SaaS"),
        ],
        validators=[Optional()],
    )
    cloud_provider = StringField(
        "Cloud Provider",
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "AWS, Azure, GCP"},
    )
    deployment_region = StringField(
        "Deployment Region",
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "us-east - 1, eu-west - 1"},
    )
    container_image = StringField(
        "Container Image",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "myapp:v1.0.0"},
    )
    kubernetes_namespace = StringField(
        "Kubernetes Namespace",
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "production"},
    )

    # Users & Usage
    user_type = SelectField(
        "User Type",
        choices=[
            ("", "-- Select Type --"),
            ("Internal", "Internal"),
            ("External", "External"),
            ("B2B", "B2B"),
            ("B2C", "B2C"),
            ("Mixed", "Mixed"),
        ],
        validators=[Optional()],
    )
    concurrent_users_max = IntegerField(
        "Max Concurrent Users", validators=[Optional()], render_kw={"placeholder": "1000"}
    )
    average_daily_users = IntegerField(
        "Average Daily Users", validators=[Optional()], render_kw={"placeholder": "500"}
    )
    geographic_distribution = StringField(
        "Geographic Distribution",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "UK, EU, US"},
    )

    # Performance & Scalability
    response_time_target_ms = IntegerField(
        "Response Time Target (ms)", validators=[Optional()], render_kw={"placeholder": "200"}
    )
    throughput_target_tps = IntegerField(
        "Throughput Target (TPS)", validators=[Optional()], render_kw={"placeholder": "1000"}
    )
    current_response_time_ms = IntegerField(
        "Current Response Time (ms)", validators=[Optional()], render_kw={"placeholder": "150"}
    )
    current_throughput_tps = IntegerField(
        "Current Throughput (TPS)", validators=[Optional()], render_kw={"placeholder": "800"}
    )
    scalability_model = SelectField(
        "Scalability Model",
        choices=[
            ("", "-- Select Model --"),
            ("Horizontal", "Horizontal"),
            ("Vertical", "Vertical"),
            ("Auto-scaling", "Auto-scaling"),
        ],
        validators=[Optional()],
    )
    max_instances = IntegerField(
        "Max Instances", validators=[Optional()], render_kw={"placeholder": "10"}
    )
    min_instances = IntegerField(
        "Min Instances", validators=[Optional()], render_kw={"placeholder": "2"}
    )

    # Availability & Reliability
    sla_availability_percentage = DecimalField(
        "SLA Availability %",
        validators=[Optional(), NumberRange(min=0, max=100)],
        render_kw={"placeholder": "99.9"},
    )
    current_uptime_percentage = DecimalField(
        "Current Uptime %",
        validators=[Optional(), NumberRange(min=0, max=100)],
        render_kw={"placeholder": "99.95"},
    )
    disaster_recovery_enabled = BooleanField("Disaster Recovery Enabled", validators=[Optional()])
    rpo_hours = IntegerField("RPO (Hours)", validators=[Optional()], render_kw={"placeholder": "4"})
    rto_hours = IntegerField("RTO (Hours)", validators=[Optional()], render_kw={"placeholder": "2"})
    backup_frequency = StringField(
        "Backup Frequency",
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "Daily, Weekly"},
    )
    last_backup_date = DateField("Last Backup Date", validators=[Optional()], format="%Y-%m-%d")

    # Security & Compliance
    authentication_method = StringField(
        "Authentication Method",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "OAuth2, SAML, LDAP"},
    )
    authorization_model = SelectField(
        "Authorization Model",
        choices=[("", "-- Select Model --"), ("RBAC", "RBAC"), ("ABAC", "ABAC")],
        validators=[Optional()],
    )
    encryption_at_rest = BooleanField("Encryption at Rest", validators=[Optional()])
    encryption_in_transit = BooleanField(
        "Encryption in Transit", validators=[Optional()], default=True
    )
    pii_data_processed = BooleanField("PII Data Processed", validators=[Optional()])
    gdpr_compliant = BooleanField("GDPR Compliant", validators=[Optional()])
    compliance_tags = StringField(
        "Compliance Tags",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "PCI-DSS, HIPAA, SOC2"},
    )
    last_security_audit_date = DateField(
        "Last Security Audit", validators=[Optional()], format="%Y-%m-%d"
    )
    last_penetration_test_date = DateField(
        "Last Penetration Test", validators=[Optional()], format="%Y-%m-%d"
    )

    # Integration Points
    interfaces_count = IntegerField(
        "Interfaces Count", validators=[Optional()], render_kw={"placeholder": "5"}
    )
    dependencies_count = IntegerField(
        "Dependencies Count", validators=[Optional()], render_kw={"placeholder": "3"}
    )
    integration_pattern = SelectField(
        "Integration Pattern",
        choices=[
            ("", "-- Select Pattern --"),
            ("REST API", "REST API"),
            ("Message Queue", "Message Queue"),
            ("Event Stream", "Event Stream"),
            ("Batch", "Batch"),
        ],
        validators=[Optional()],
    )
    exposes_api = BooleanField("Exposes API", validators=[Optional()])
    api_documentation_url = StringField(
        "API Documentation URL",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "https://api-docs.example.com"},
    )

    # Data Management
    primary_data_store = StringField(
        "Primary Data Store",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "PostgreSQL Database"},
    )
    database_size_gb = DecimalField(
        "Database Size (GB)",
        validators=[Optional(), NumberRange(min=0)],
        render_kw={"placeholder": "50.5"},
    )
    data_retention_policy = TextAreaField(
        "Data Retention Policy",
        validators=[Optional(), Length(max=1000)],
        render_kw={"rows": 2, "placeholder": "Retain data for 7 years"},
    )
    data_classification = SelectField(
        "Data Classification",
        choices=[
            ("", "-- Select Classification --"),
            ("Public", "Public"),
            ("Internal", "Internal"),
            ("Confidential", "Confidential"),
            ("Restricted", "Restricted"),
        ],
        validators=[Optional()],
    )
    master_data_source = BooleanField("Master Data Source", validators=[Optional()])

    # Cost & Licensing
    license_type = SelectField(
        "License Type",
        choices=[
            ("", "-- Select Type --"),
            ("Commercial", "Commercial"),
            ("Open Source", "Open Source"),
            ("Proprietary", "Proprietary"),
            ("Custom", "Custom"),
        ],
        validators=[Optional()],
    )
    license_cost_annual = DecimalField(
        "License Cost (Annual)",
        validators=[Optional(), NumberRange(min=0)],
        render_kw={"placeholder": "50000.00"},
    )
    infrastructure_cost_monthly = DecimalField(
        "Infrastructure Cost (Monthly)",
        validators=[Optional(), NumberRange(min=0)],
        render_kw={"placeholder": "5000.00"},
    )
    development_cost_annual = DecimalField(
        "Development Cost (Annual)",
        validators=[Optional(), NumberRange(min=0)],
        render_kw={"placeholder": "200000.00"},
    )
    maintenance_cost = DecimalField(
        "Maintenance Cost (Annual)",
        validators=[Optional(), NumberRange(min=0)],
        render_kw={"placeholder": "50000.00"},
    )
    total_cost_of_ownership = DecimalField(
        "Total Cost of Ownership",
        validators=[Optional(), NumberRange(min=0)],
        render_kw={"placeholder": "500000.00"},
    )
    cost_center = StringField(
        "Cost Center",
        validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "IT - 001"},
    )

    # Lifecycle
    go_live_date = DateField("Go Live Date", validators=[Optional()], format="%Y-%m-%d")
    last_major_release_date = DateField(
        "Last Major Release", validators=[Optional()], format="%Y-%m-%d"
    )
    next_planned_release_date = DateField(
        "Next Planned Release", validators=[Optional()], format="%Y-%m-%d"
    )
    end_of_life_date = DateField("End of Life Date", validators=[Optional()], format="%Y-%m-%d")
    retirement_date = DateField("Retirement Date", validators=[Optional()], format="%Y-%m-%d")
    replacement_application = StringField(
        "Replacement Application",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "Name of replacement app"},
    )

    # DevOps & CI/CD
    ci_cd_pipeline_url = StringField(
        "CI/CD Pipeline URL",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "https://jenkins.example.com/job/myapp"},
    )
    build_tool = StringField(
        "Build Tool",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Jenkins, GitLab CI, GitHub Actions"},
    )
    automated_testing_coverage = DecimalField(
        "Test Coverage %",
        validators=[Optional(), NumberRange(min=0, max=100)],
        render_kw={"placeholder": "85.5"},
    )
    deployment_frequency = SelectField(
        "Deployment Frequency",
        choices=[
            ("", "-- Select Frequency --"),
            ("Daily", "Daily"),
            ("Weekly", "Weekly"),
            ("Monthly", "Monthly"),
            ("Quarterly", "Quarterly"),
        ],
        validators=[Optional()],
    )
    mean_time_to_recovery_hours = IntegerField(
        "MTTR (Hours)", validators=[Optional()], render_kw={"placeholder": "2"}
    )
    change_failure_rate_percent = DecimalField(
        "Change Failure Rate %",
        validators=[Optional(), NumberRange(min=0, max=100)],
        render_kw={"placeholder": "5.0"},
    )

    # Monitoring & Observability
    monitoring_enabled = BooleanField("Monitoring Enabled", validators=[Optional()])
    monitoring_tool = StringField(
        "Monitoring Tool",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Datadog, New Relic, Prometheus"},
    )
    logging_enabled = BooleanField("Logging Enabled", validators=[Optional()])
    logging_tool = StringField(
        "Logging Tool",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Splunk, ELK, CloudWatch"},
    )
    tracing_enabled = BooleanField("Tracing Enabled", validators=[Optional()])
    tracing_tool = StringField(
        "Tracing Tool",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "Jaeger, Zipkin, X-Ray"},
    )
    apm_enabled = BooleanField("APM Enabled", validators=[Optional()])
    health_check_url = StringField(
        "Health Check URL",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "https://api.example.com/health"},
    )

    # Quality Metrics
    code_quality_score = DecimalField(
        "Code Quality Score",
        validators=[Optional(), NumberRange(min=0, max=100)],
        render_kw={"placeholder": "85.0"},
    )
    technical_debt_hours = IntegerField(
        "Technical Debt (Hours)", validators=[Optional()], render_kw={"placeholder": "120"}
    )
    bugs_count = IntegerField("Bugs Count", validators=[Optional()], render_kw={"placeholder": "5"})
    vulnerabilities_count = IntegerField(
        "Vulnerabilities Count", validators=[Optional()], render_kw={"placeholder": "2"}
    )
    code_coverage_percent = DecimalField(
        "Code Coverage %",
        validators=[Optional(), NumberRange(min=0, max=100)],
        render_kw={"placeholder": "80.5"},
    )
    last_code_quality_scan = DateField(
        "Last Code Quality Scan", validators=[Optional()], format="%Y-%m-%d"
    )

    # Documentation
    documentation_url = StringField(
        "Documentation URL",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "https://docs.example.com"},
    )
    architecture_diagram_url = StringField(
        "Architecture Diagram URL",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "https://diagrams.example.com/app-arch"},
    )
    runbook_url = StringField(
        "Runbook URL",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "https://runbooks.example.com/myapp"},
    )
    user_manual_url = StringField(
        "User Manual URL",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "https://help.example.com/user-guide"},
    )

    # Metadata
    tags = StringField(
        "Tags",
        validators=[Optional(), Length(max=500)],
        render_kw={"placeholder": "production, critical, customer-facing"},
    )
    notes = TextAreaField(
        "Notes",
        validators=[Optional(), Length(max=5000)],
        render_kw={"rows": 4, "placeholder": "Additional notes and comments"},
    )

    vendor_products = SelectMultipleField(
        "Vendor Products", choices=[], coerce=int, validators=[Optional()]
    )


# NOTE: a second, standalone `class OverviewForm(FlaskForm)` with its own ten
# fields used to be defined immediately above this one. Python bound the name to
# whichever came last, so that definition was dead — every `OverviewForm()` in the
# codebase already resolved to the alias below. Removed rather than kept, since two
# forms sharing a name is how the wrong one gets edited.


# Alias for backward compatibility - OverviewForm is the same as ApplicationComponentForm
class OverviewForm(ApplicationComponentForm):
    """Form for application overview tab (alias for ApplicationComponentForm)"""

    pass


# Layer forms (placeholders for future implementation)
class StrategyLayerForm(FlaskForm):
    """Form for strategy layer elements"""

    pass


class MotivationLayerForm(FlaskForm):
    """Form for motivation layer elements"""

    pass


class BusinessLayerForm(FlaskForm):
    """Form for business layer elements"""

    pass


class ApplicationLayerForm(FlaskForm):
    """Form for application layer elements"""

    pass


class TechnologyLayerForm(FlaskForm):
    """Form for technology layer elements"""

    pass


class PhysicalLayerForm(FlaskForm):
    """Form for physical layer elements"""

    pass


class ImplementationLayerForm(FlaskForm):
    """Form for implementation and migration layer elements"""

    pass

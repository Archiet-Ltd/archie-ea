"""Code Workbench data models.

migration-exempt — all tables created via db.create_all() per migration-freeze policy.
"""
import logging
from datetime import datetime
from app.extensions import db
from app.models.mixins import TenantMixin

logger = logging.getLogger(__name__)


class CodegenGeneration(TenantMixin, db.Model):
    """Stores the state of a code generation session for a solution."""

    __tablename__ = "codegen_generations"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True
    )
    uml_snapshot = db.Column(db.JSON, nullable=True)
    config = db.Column(db.JSON, nullable=True)
    generated_files = db.Column(db.JSON, nullable=True)
    github_url = db.Column(db.String(500), nullable=True)
    github_pr_url = db.Column(db.String(500), nullable=True)
    github_commit_sha = db.Column(db.String(40), nullable=True)
    github_last_synced_at = db.Column(db.DateTime, nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    download_count = db.Column(db.Integer, nullable=False, default=0)

    # Architectural Genome — queryable IR for quality scoring and traceability.
    # Stored as JSON (not inside generated_files blob) so it can be read without
    # parsing the full files dict. NULL for non-genome generation modes.
    genome = db.Column(db.JSON, nullable=True)
    genome_quality_score = db.Column(db.Integer, nullable=True)

    solution = db.relationship("Solution", backref=db.backref("codegen", uselist=False))

    def __repr__(self):
        return f"<CodegenGeneration solution={self.solution_id} v={self.version}>"


class CodegenDriftReport(TenantMixin, db.Model):
    """Stores a point-in-time drift scan result comparing GitHub repo against generated baseline."""

    __tablename__ = "codegen_drift_reports"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True
    )
    scanned_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), nullable=False)  # clean | drifted | error
    drift_items = db.Column(db.JSON, nullable=True)     # [{path, change_type, additions, deletions, patch}]
    base_commit_sha = db.Column(db.String(40), nullable=True)
    head_commit_sha = db.Column(db.String(40), nullable=True)
    drift_file_count = db.Column(db.Integer, nullable=False, default=0)
    error_message = db.Column(db.String(500), nullable=True)

    solution = db.relationship("Solution", backref=db.backref("drift_reports", lazy="dynamic"))

    def __repr__(self):
        return f"<CodegenDriftReport solution={self.solution_id} status={self.status} files={self.drift_file_count}>"


class CodegenGenerationHistory(TenantMixin, db.Model):
    """Records each code generation run for diffing and audit (GAP-04)."""

    __tablename__ = "codegen_generation_history"
    __tenant_parent__ = (('codegen_generation_id', 'codegen_generations'),)

    id = db.Column(db.Integer, primary_key=True)
    codegen_generation_id = db.Column(
        db.Integer, db.ForeignKey("codegen_generations.id"), nullable=False, index=True
    )
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    generated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    language = db.Column(db.String(50), nullable=False)
    mode = db.Column(db.String(20), nullable=False)
    file_count = db.Column(db.Integer, nullable=False)
    spec_maturity_score = db.Column(db.Float, nullable=True)
    chain_completeness_score = db.Column(db.Float, nullable=True)
    quality_score = db.Column(db.Float, nullable=True)
    quality_details = db.Column(db.JSON, nullable=True)
    version_label = db.Column(db.String(20), nullable=True)
    architecture_snapshot = db.Column(db.JSON, nullable=True)
    file_manifest = db.Column(db.JSON, nullable=True)

    generation = db.relationship("CodegenGeneration", backref="history_entries")

    def __repr__(self):
        return f"<CodegenGenerationHistory gen={self.codegen_generation_id} v={self.version_label}>"


class CodegenSystemBoundary(TenantMixin, db.Model):
    """Groups multiple solutions into a logical system boundary for multi-service composition (Feature 2).

    Named CodegenSystemBoundary (not SystemBoundary) to avoid SQLAlchemy registry conflict
    with app.models.system_architecture.SystemBoundary (table: system_boundaries).
    This model maps to codegen_system_boundaries — a completely separate table.
    """

    __tablename__ = "codegen_system_boundaries"
    __tenant_parent__ = (('created_by_id', 'users'),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    generated_artifacts = db.Column(db.JSON, nullable=True)  # {docker_compose, contracts, client_sdks}
    generated_at = db.Column(db.DateTime, nullable=True)

    solutions = db.relationship(
        "SystemBoundarySolution", backref="boundary",
        lazy="dynamic", cascade="all,delete-orphan",
    )

    def __repr__(self):
        return f"<CodegenSystemBoundary id={self.id} name={self.name!r}>"


class SystemBoundarySolution(TenantMixin, db.Model):
    """Junction: which solutions participate in a system boundary and in what role."""

    __tablename__ = "codegen_system_boundary_solutions"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    boundary_id = db.Column(
        db.Integer, db.ForeignKey("codegen_system_boundaries.id"), nullable=False, index=True,
    )
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id"), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="service")  # producer | consumer | service
    service_port = db.Column(db.Integer, nullable=True)  # assigned port in docker-compose
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    solution = db.relationship("Solution", foreign_keys=[solution_id])

    __table_args__ = (
        db.UniqueConstraint("boundary_id", "solution_id", name="uq_sbs_boundary_solution"),
    )

    def __repr__(self):
        return f"<SystemBoundarySolution boundary={self.boundary_id} solution={self.solution_id} role={self.role}>"


class CodegenChatMessage(TenantMixin, db.Model):
    """Persists chat messages for the NL code editing panel.

    migration-exempt — created via db.create_all().
    """

    __tablename__ = "codegen_chat_messages"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True)
    generation_id = db.Column(
        db.Integer, db.ForeignKey("codegen_generations.id"), nullable=True, index=True
    )
    role = db.Column(db.String(16), nullable=False)  # user | assistant
    content = db.Column(db.Text, nullable=False)
    file_context = db.Column(db.String(500), nullable=True)  # file open at time of message
    patches_json = db.Column(db.JSON, nullable=True)  # [{file, diff, warnings}] from assistant
    patch_applied = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    solution = db.relationship("Solution", backref=db.backref("chat_messages", lazy="dynamic"))

    def __repr__(self):
        return f"<CodegenChatMessage sol={self.solution_id} role={self.role}>"


class CodegenTemplateSet(TenantMixin, db.Model):
    """Named collection of Jinja2 template overrides (Feature 6: Template Marketplace).

    migration-exempt — created via db.create_all().
    """

    __tablename__ = "codegen_template_sets"
    __tenant_parent__ = (('created_by_id', 'users'),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    language = db.Column(db.String(50), nullable=False)  # python-fastapi|go-chi|java-spring-boot|salesforce-apex|react-shadcn
    description = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    files = db.relationship(
        "CodegenTemplateFile", backref="template_set",
        cascade="all,delete-orphan", lazy="dynamic",
    )

    def __repr__(self):
        return f"<CodegenTemplateSet id={self.id} name={self.name!r} lang={self.language}>"


class CodegenTemplateFile(TenantMixin, db.Model):
    """Single Jinja2 template file override within a CodegenTemplateSet.

    migration-exempt — created via db.create_all().
    """

    __tablename__ = "codegen_template_files"
    __tenant_parent__ = (('set_id', 'codegen_template_sets'),)

    id = db.Column(db.Integer, primary_key=True)
    set_id = db.Column(
        db.Integer, db.ForeignKey("codegen_template_sets.id"), nullable=False, index=True
    )
    template_name = db.Column(db.String(200), nullable=False)  # e.g. "main.py.j2"
    content = db.Column(db.Text, nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("set_id", "template_name", name="uq_ctf_set_template"),
    )

    def __repr__(self):
        return f"<CodegenTemplateFile set={self.set_id} template={self.template_name!r}>"


class SolutionInstance(TenantMixin, db.Model):
    """Tracks a running deployment of a generated solution on Coolify.

    migration-exempt — table created via db.create_all()

    Credentials (database_url) are Fernet-encrypted at rest.
    Use credential_encryption.encrypt_credential() before storing,
    credential_encryption.decrypt_credential() after reading.
    """

    __tablename__ = "codegen_solution_instances"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True
    )
    version = db.Column(db.Integer, default=1, nullable=False)
    coolify_project_id = db.Column(db.String(100))
    coolify_service_id = db.Column(db.String(100))
    deployment_url = db.Column(db.String(500))
    database_url_encrypted = db.Column(db.LargeBinary)  # Fernet-encrypted
    health_status = db.Column(
        db.String(20), default="deploying", nullable=False
    )  # deploying, healthy, unhealthy, stopped
    last_health_check = db.Column(db.DateTime)

    # Resource quotas (admin-configurable per instance)
    max_container_ram_mb = db.Column(db.Integer, default=512)
    max_db_size_mb = db.Column(db.Integer, default=1024)
    max_n8n_workflows = db.Column(db.Integer, default=20)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    deployed_at = db.Column(db.DateTime)

    solution = db.relationship(
        "Solution",
        backref=db.backref("deployment_instance", uselist=False),
    )

    @property
    def database_url(self) -> str | None:
        """Decrypt and return the database URL. Returns None if not set or decryption fails."""
        if not self.database_url_encrypted:
            return None
        from app.modules.codegen.services.credential_encryption import decrypt_credential
        return decrypt_credential(self.database_url_encrypted)

    def __repr__(self):
        return f"<SolutionInstance solution={self.solution_id} v{self.version} [{self.health_status}]>"


class SolutionRule(TenantMixin, db.Model):
    """Tracks a business rule defined by the BA for a generated solution.

    Rules flow: BA input (template params, NL text, or workflow JSON)
    -> structured rule_definition (JSON) -> compiled implementation_artifacts.

    migration-exempt -- created via db.create_all() per migration-freeze policy.
    """

    __tablename__ = "codegen_solution_rules"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True
    )
    name = db.Column(db.String(200), nullable=False)
    source = db.Column(db.String(20), nullable=True)  # template | natural_language | workflow_builder
    source_text = db.Column(db.Text, nullable=True)  # original BA input
    rule_definition = db.Column(db.JSON, nullable=False)  # structured intermediate representation
    implementation_artifacts = db.Column(db.JSON, nullable=True)  # {files, n8n_workflows, keycloak_configs}
    version = db.Column(db.Integer, nullable=False, default=1)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    solution = db.relationship("Solution", backref=db.backref("business_rules", lazy="dynamic"))

    def __repr__(self):
        return f"<SolutionRule id={self.id} solution={self.solution_id} name={self.name!r}>"


class SolutionLayerElement(TenantMixin, db.Model):
    """Layer 2 (Solution) element — formalizes what codegen produces implicitly.

    Element types: use_case, component, data_model, behavior, deployment_node.
    Each element links back to ArchiMate Layer 1 via CrossLayerRelationship.

    migration-exempt — table created via db.create_all()
    """

    __tablename__ = "solution_layer_elements"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True
    )
    element_type = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(20), nullable=False, default="generated")
    status = db.Column(db.String(20), nullable=False, default="draft")
    confidence = db.Column(db.Float, nullable=True)
    generated_file_path = db.Column(db.String(500), nullable=True)
    spec_hash = db.Column(db.String(64), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    solution = db.relationship("Solution", backref=db.backref("solution_layer_elements", lazy="dynamic"))

    __table_args__ = (
        db.Index("idx_sle_solution_type", "solution_id", "element_type"),
    )

    def __repr__(self):
        return f"<SolutionLayerElement id={self.id} type={self.element_type} name={self.name!r}>"


class ExperienceElement(TenantMixin, db.Model):
    """Layer 3 (Experience) element — UI/UX model tied to solution layer.

    Element types: persona, journey, screen, ui_component, design_token.
    Each element links to Layer 2 via CrossLayerRelationship.

    migration-exempt — table created via db.create_all()
    """

    __tablename__ = "experience_elements"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True
    )
    element_type = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(20), nullable=False, default="generated")
    status = db.Column(db.String(20), nullable=False, default="draft")
    confidence = db.Column(db.Float, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    solution = db.relationship("Solution", backref=db.backref("experience_elements", lazy="dynamic"))

    __table_args__ = (
        db.Index("idx_ee_solution_type", "solution_id", "element_type"),
    )

    def __repr__(self):
        return f"<ExperienceElement id={self.id} type={self.element_type} name={self.name!r}>"


class CrossLayerRelationship(TenantMixin, db.Model):
    """Traceability link between elements across layers (ArchiMate ↔ Solution ↔ Experience).

    Relationship types: realizes, decomposes, implements, binds_to, constrains, verifies.
    Source/target layer: archimate, solution, experience, test.

    migration-exempt — table created via db.create_all()
    """

    __tablename__ = "cross_layer_relationships"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    relationship_type = db.Column(db.String(30), nullable=False)
    source_layer = db.Column(db.String(20), nullable=False)
    source_element_type = db.Column(db.String(50), nullable=False)
    source_element_id = db.Column(db.Integer, nullable=False)
    target_layer = db.Column(db.String(20), nullable=False)
    target_element_type = db.Column(db.String(50), nullable=False)
    target_element_id = db.Column(db.Integer, nullable=False)
    confidence = db.Column(db.Float, nullable=True)
    source_origin = db.Column(db.String(20), nullable=False, default="generated")
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    solution = db.relationship("Solution", backref=db.backref("cross_layer_relationships", lazy="dynamic"))

    __table_args__ = (
        db.Index("idx_clr_source", "source_layer", "source_element_id"),
        db.Index("idx_clr_target", "target_layer", "target_element_id"),
        db.Index("idx_clr_solution", "solution_id"),
    )

    def __repr__(self):
        return (
            f"<CrossLayerRelationship {self.source_layer}:{self.source_element_id} "
            f"--{self.relationship_type}--> {self.target_layer}:{self.target_element_id}>"
        )


class SolutionConnector(TenantMixin, db.Model):
    """Tracks an active integration connector for a deployed solution.
    migration-exempt — table created via db.create_all()
    """
    __tablename__ = "codegen_solution_connectors"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True)
    connector_type = db.Column(db.String(50), nullable=False)
    n8n_workflow_id = db.Column(db.String(100))
    sync_frequency = db.Column(db.String(20))
    last_sync_at = db.Column(db.DateTime)
    last_sync_status = db.Column(db.String(20))
    records_synced = db.Column(db.Integer, default=0)
    credential_ref = db.Column(db.String(100))
    object_mappings = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    solution = db.relationship("Solution", backref=db.backref("connectors", lazy="dynamic"))

    def __repr__(self):
        return f"<SolutionConnector id={self.id} solution={self.solution_id} type={self.connector_type}>"


# ConnectorCredential (codegen_connector_credentials) lives in credential_vault.py,
# a services module never imported at boot, so db.create_all() (via `flask
# init-db`) never registered it and the table was never created on a fresh
# database -- CredentialVault.store() would raise UndefinedTable the first
# time anything actually called it. Imported here, next to its sibling
# SolutionConnector, so init-db picks it up the same way.
from app.modules.codegen.services.credential_vault import ConnectorCredential  # noqa: E402,F401


class SolutionVersion(db.Model):
    """Tracks a versioned deployment of a solution.
    migration-exempt — table created via db.create_all()
    """
    __tablename__ = "codegen_solution_versions"

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True)
    version_number = db.Column(db.Integer, nullable=False)
    change_summary = db.Column(db.Text)
    change_plan = db.Column(db.JSON)
    migration_scripts = db.Column(db.JSON)
    rule_changes = db.Column(db.JSON)
    test_results = db.Column(db.JSON)
    rollback_script = db.Column(db.Text)
    coolify_deployment_id = db.Column(db.String(100))
    status = db.Column(db.String(20), default="deploying", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    deployed_at = db.Column(db.DateTime)
    created_by = db.Column(db.String(100))

    solution = db.relationship("Solution", backref=db.backref("deploy_versions", lazy="dynamic"))

    def __repr__(self):
        return f"<SolutionVersion solution={self.solution_id} v{self.version_number} [{self.status}]>"


class DataImport(TenantMixin, db.Model):
    """Tracks a spreadsheet data import session for a deployed solution.

    Lifecycle: pending -> importing -> completed | failed
    Stores column mappings, row counts, and per-row errors for audit trail.

    migration-exempt — created via db.create_all() per migration-freeze policy.
    """

    __tablename__ = "codegen_data_imports"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True
    )
    filename = db.Column(db.String(500), nullable=False)
    sheet_name = db.Column(db.String(200), nullable=False)
    row_count = db.Column(db.Integer, nullable=False)
    column_count = db.Column(db.Integer, nullable=False)
    mapped_columns = db.Column(db.Integer, nullable=False, default=0)
    unmapped_columns = db.Column(db.Integer, nullable=False, default=0)
    column_mappings = db.Column(db.JSON, nullable=False, default=list)
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending | importing | completed | failed
    imported_count = db.Column(db.Integer, nullable=False, default=0)
    error_count = db.Column(db.Integer, nullable=False, default=0)
    errors = db.Column(db.JSON, nullable=True)  # [{row, error}]
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    solution = db.relationship("Solution", backref=db.backref("data_imports", lazy="dynamic"))

    def __repr__(self):
        return f"<DataImport id={self.id} solution={self.solution_id} file={self.filename!r} [{self.status}]>"


class WorkflowDesign(TenantMixin, db.Model):
    """Visual workflow definition created in the drag-drop designer.

    Stores the JointJS graph JSON (workflow_definition) and the compiled
    n8n workflow JSON (compiled_n8n). Linked to a solution.

    migration-exempt — created via db.create_all() per migration-freeze policy.
    """

    __tablename__ = "codegen_workflow_designs"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True
    )
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    workflow_definition = db.Column(db.JSON, nullable=False)  # JointJS graph serialization
    compiled_n8n = db.Column(db.JSON, nullable=True)  # output of WorkflowToN8nCompiler
    template_id = db.Column(db.String(50), nullable=True)  # source template if created from one
    version = db.Column(db.Integer, nullable=False, default=1)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    solution = db.relationship("Solution", backref=db.backref("workflow_designs", lazy="dynamic"))

    def __repr__(self):
        return f"<WorkflowDesign id={self.id} solution={self.solution_id} name={self.name!r}>"


class TestRun(TenantMixin, db.Model):
    """Records a complete test execution run against a deployed solution.

    Each run contains multiple steps (TestRunStep). Runs are triggered by
    post-deploy hooks, on-demand requests, or the nightly scheduler.

    migration-exempt -- created via db.create_all() per migration-freeze policy.
    """

    __tablename__ = "codegen_test_runs"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True
    )
    version = db.Column(db.Integer, nullable=True)  # solution version tested
    trigger = db.Column(db.String(30), nullable=False)  # manual | post_deploy | nightly | on_demand
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending | running | pass | fail | error
    summary = db.Column(db.JSON, nullable=False, default=dict)  # {pass: N, fail: N, error: N, total: N}
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    solution = db.relationship("Solution", backref=db.backref("test_runs", lazy="dynamic"))
    steps = db.relationship("TestRunStep", backref="test_run", cascade="all,delete-orphan", lazy="dynamic")

    def __repr__(self):
        return f"<TestRun id={self.id} solution={self.solution_id} status={self.status}>"


class TestRunStep(TenantMixin, db.Model):
    """Individual step result within a TestRun.

    migration-exempt -- created via db.create_all() per migration-freeze policy.
    """

    __tablename__ = "codegen_test_run_steps"
    __tenant_parent__ = (('test_run_id', 'codegen_test_runs'),)

    id = db.Column(db.Integer, primary_key=True)
    test_run_id = db.Column(
        db.Integer, db.ForeignKey("codegen_test_runs.id"), nullable=False, index=True
    )
    scenario_id = db.Column(db.Integer, nullable=False)
    step_number = db.Column(db.Integer, nullable=False)
    action = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending | pass | fail | error | skipped
    expected_outcome = db.Column(db.Text, nullable=True)
    actual_outcome = db.Column(db.Text, nullable=True)
    screenshot_path = db.Column(db.String(500), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<TestRunStep run={self.test_run_id} scenario={self.scenario_id} step={self.step_number} [{self.status}]>"


def persist_traceability(solution_id, code_bundle, spec_hash=None):
    """Persist Layer 2 SolutionElement records and CrossLayerRelationship links from a code bundle.

    Called after code generation to stop discarding the archimate_sources data
    that the generator computes but the flat {path: content} serialization throws away.

    Idempotent: deletes existing records for this solution+spec_hash before inserting.
    """
    try:
        if spec_hash:
            SolutionLayerElement.query.filter_by(
                solution_id=solution_id, spec_hash=spec_hash
            ).delete(synchronize_session=False)
            CrossLayerRelationship.query.filter_by(solution_id=solution_id).delete(
                synchronize_session=False
            )

        element_map = {}  # file_path -> SolutionElement.id

        for gf in code_bundle.files:
            if not gf.path or not gf.content or not gf.content.strip():
                continue

            # Classify generated files into solution element types
            etype = _classify_file_type(gf.path)
            if not etype:
                continue

            name = _element_name_from_path(gf.path)
            elem = SolutionLayerElement(
                solution_id=solution_id,
                element_type=etype,
                name=name,
                source="generated",
                status="draft",
                confidence=1.0,
                generated_file_path=gf.path,
                spec_hash=spec_hash,
            )
            db.session.add(elem)
            db.session.flush()
            element_map[gf.path] = elem.id

            # Create cross-layer links to ArchiMate source elements
            for archimate_id in (gf.archimate_sources or []):
                rel = CrossLayerRelationship(
                    relationship_type="realizes",
                    source_layer="solution",
                    source_element_type=etype,
                    source_element_id=elem.id,
                    target_layer="archimate",
                    target_element_type="element",
                    target_element_id=archimate_id,
                    confidence=1.0,
                    source_origin="generated",
                    solution_id=solution_id,
                )
                db.session.add(rel)

        db.session.flush()
        logger.info(
            "persist_traceability: solution=%d, %d elements, spec_hash=%s",
            solution_id, len(element_map), spec_hash,
        )
        return element_map
    except Exception as e:
        logger.warning("persist_traceability failed (non-fatal): %s", e)
        db.session.rollback()
        return {}


def _classify_file_type(path):
    """Map a generated file path to a solution element type."""
    p = path.lower()
    if p.startswith("app/routes/") or p.startswith("app/routers/"):
        return "component"
    if p.startswith("app/models/"):
        return "data_model"
    if p.startswith("app/handlers/") or p.startswith("app/validators/"):
        return "behavior"
    if p.startswith("app/state_machines/"):
        return "behavior"
    if p.startswith("app/orchestrators/"):
        return "behavior"
    if p.startswith("app/schemas/"):
        return "data_model"
    if p.startswith("app/clients/"):
        return "component"
    if p.startswith("k8s/") or p.startswith("helm/") or p == "Dockerfile":
        return "deployment_node"
    if p == "docker-compose.yml":
        return "deployment_node"
    if p.startswith("app/main") or p == "app/database.py":
        return "component"
    return None


def _element_name_from_path(path):
    """Extract a human-readable element name from a file path."""
    import os
    base = os.path.basename(path)
    name = os.path.splitext(base)[0]
    return name.replace("_", " ").title()

# Project Management Models - Internal project management without external dependencies
import uuid
from datetime import datetime

from sqlalchemy import Enum
from sqlalchemy.dialects.postgresql import UUID

from app import db
from app.models.mixins import TenantMixin


class Project(TenantMixin, db.Model):
    """
    Internal project model for project management functionality.
    Independent of JIRA or external systems for users who want internal tracking.
    """

    __tablename__ = "projects"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(200), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)  # Project code/identifier
    description = db.Column(db.Text)

    # Project metadata
    status = db.Column(
        Enum("planning", "active", "on_hold", "completed", "cancelled", name="project_status"),
        default="planning",
    )
    priority = db.Column(
        Enum("low", "medium", "high", "critical", name="project_priority"), default="medium"
    )

    # Date tracking
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    updated_date = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Project manager and team.
    # project_manager is free text and stays for back-compat; project_manager_id is
    # the real link. Without it there is no ownership graph — nothing can answer
    # "what am I accountable for", and no notification can find a person.
    project_manager = db.Column(db.String(100))
    project_manager_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    team_members = db.Column(db.Text)  # JSON or comma-separated list of team members

    # Budget and progress
    budget = db.Column(db.Numeric(10, 2))
    budget_currency = db.Column(db.String(3), default="USD")
    progress_percentage = db.Column(db.Integer, default=0)  # 0 - 100

    # Relationships
    tasks = db.relationship("Task", backref="project", lazy="dynamic", cascade="all, delete-orphan")
    milestones = db.relationship(
        "Milestone", backref="project", lazy="dynamic", cascade="all, delete-orphan"
    )

    # ── Traceability to architecture ────────────────────────────────────────
    # These were bare Integers with a comment where a foreign key belonged, so
    # nothing could traverse them: the architecture repository and the delivery
    # repository held the same ids and could not join. Declaring the FK is what
    # makes "which initiative is closing this capability gap, and is it on track"
    # answerable in one query instead of never.
    #
    # projects.id is a UUID, which is irrelevant here — a foreign key column on
    # this table simply has to match the *target* primary key's type (Integer).
    #
    # work_package_id is the important one. WorkPackage is the ArchiMate
    # Implementation & Migration element (DESIGN.md: ArchiMate is the backbone,
    # not a view), already linked to capability, goal, plateau and application
    # component. Hanging Project off it inherits that whole graph.
    #
    # All nullable: `projects` is an existing table and reconcile-schema can only
    # ADD nullable columns (ADR 0002). Existing rows keep working unlinked.
    work_package_id = db.Column(
        db.Integer, db.ForeignKey("work_packages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    capability_id = db.Column(
        db.Integer,
        db.ForeignKey("unified_capabilities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    requirement_id = db.Column(
        db.Integer, db.ForeignKey("requirements.id", ondelete="SET NULL"), nullable=True, index=True
    )

    work_package = db.relationship("WorkPackage", backref="projects", foreign_keys=[work_package_id])
    capability = db.relationship(
        "UnifiedCapability", backref="delivery_projects", foreign_keys=[capability_id]
    )
    project_manager_user = db.relationship("User", foreign_keys=[project_manager_id])

    def __repr__(self):
        return f"<Project {self.name}>"

    @property
    def traces_to_architecture(self) -> bool:
        """True when this project is reachable from the architecture repository.

        A project with no link is invisible to every roadmap, gap and portfolio
        view — it consumes budget the architecture cannot see.
        """
        return any((self.work_package_id, self.capability_id, self.requirement_id))

    @property
    def total_tasks(self):
        return self.tasks.count()

    @property
    def completed_tasks(self):
        return self.tasks.filter_by(status="completed").count()

    @property
    def overdue_tasks(self):
        today = datetime.now().date()
        return self.tasks.filter(Task.due_date < today, Task.status != "completed").count()


class Task(TenantMixin, db.Model):
    """
    Individual task within a project.
    """

    __tablename__ = "tasks"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)

    # Task metadata
    status = db.Column(
        Enum("todo", "in_progress", "in_review", "completed", "cancelled", name="task_status"),
        default="todo",
    )
    priority = db.Column(
        Enum("low", "medium", "high", "critical", name="task_priority"), default="medium"
    )
    category = db.Column(db.String(50))  # development, testing, documentation, etc.

    # Assignment and tracking
    assigned_to = db.Column(db.String(100))  # Could link to User model if needed
    estimated_hours = db.Column(db.Float)
    actual_hours = db.Column(db.Float)

    # Date tracking
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    updated_date = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    due_date = db.Column(db.Date)
    completed_date = db.Column(db.DateTime)

    # Relationships
    project_id = db.Column(UUID(as_uuid=True), db.ForeignKey("projects.id"), nullable=False)
    milestone_id = db.Column(UUID(as_uuid=True), db.ForeignKey("milestones.id"))
    parent_task_id = db.Column(UUID(as_uuid=True), db.ForeignKey("tasks.id"))  # For subtasks

    # Self-referential relationship for subtasks
    subtasks = db.relationship(
        "Task", backref=db.backref("parent_task", remote_side="Task.id"), lazy="dynamic"
    )

    # Traceability
    requirement_id = db.Column(
        db.Integer, db.ForeignKey("requirements.id")
    )  # Fixed: requirements.id is INTEGER not UUID
    function_id = db.Column(
        db.Integer, nullable=True
    )  # Optional link to business functions (INTEGER)

    def __repr__(self):
        return f"<Task {self.title}>"

    @property
    def is_overdue(self):
        if self.due_date and self.status != "completed":
            return self.due_date < datetime.now().date()
        return False


class Milestone(db.Model):
    """
    Project milestones for tracking major deliverables.
    """

    __tablename__ = "milestones"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)

    # Milestone tracking
    status = db.Column(
        Enum("pending", "in_progress", "completed", "delayed", name="milestone_status"),
        default="pending",
    )

    # Date tracking
    target_date = db.Column(db.Date, nullable=False)
    actual_date = db.Column(db.Date)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    updated_date = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    project_id = db.Column(UUID(as_uuid=True), db.ForeignKey("projects.id"), nullable=False)
    tasks = db.relationship("Task", backref="milestone", lazy="dynamic")

    # Success criteria
    success_criteria = db.Column(db.Text)  # What defines milestone completion

    def __repr__(self):
        return f"<Milestone {self.name}>"

    @property
    def is_overdue(self):
        if self.status != "completed" and self.target_date:
            return self.target_date < datetime.now().date()
        return False

    @property
    def completion_percentage(self):
        total_tasks = self.tasks.count()
        if total_tasks == 0:
            return 0
        completed_tasks = self.tasks.filter_by(status="completed").count()
        return (completed_tasks / total_tasks) * 100


class ProjectNote(db.Model):
    """
    Notes and updates for projects - like a project journal.
    """

    __tablename__ = "project_notes"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = db.Column(db.String(200))
    content = db.Column(db.Text, nullable=False)
    note_type = db.Column(
        Enum("general", "meeting", "decision", "risk", "issue", name="note_type"), default="general"
    )

    # Tracking
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.String(100))  # Could link to User model if needed

    # Relationships
    project_id = db.Column(UUID(as_uuid=True), db.ForeignKey("projects.id"), nullable=False)
    project = db.relationship(
        "Project", backref=db.backref("notes", lazy="dynamic", cascade="all, delete-orphan")
    )

    def __repr__(self):
        return f"<ProjectNote {self.title or self.content[:50]}>"


class ProjectResource(db.Model):
    """
    Resources assigned to projects - people, equipment, budget allocations.
    """

    __tablename__ = "project_resources"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(200), nullable=False)
    resource_type = db.Column(
        Enum("human", "equipment", "software", "budget", "other", name="resource_type")
    )
    description = db.Column(db.Text)

    # Allocation details
    allocation_start = db.Column(db.Date)
    allocation_end = db.Column(db.Date)
    allocation_percentage = db.Column(db.Integer)  # For partial allocation

    # Cost tracking
    cost_per_unit = db.Column(db.Numeric(10, 2))
    units_allocated = db.Column(db.Float)
    currency = db.Column(db.String(3), default="USD")

    # Relationships
    project_id = db.Column(UUID(as_uuid=True), db.ForeignKey("projects.id"), nullable=False)
    project = db.relationship(
        "Project", backref=db.backref("resources", lazy="dynamic", cascade="all, delete-orphan")
    )

    def __repr__(self):
        return f"<ProjectResource {self.name}>"

    @property
    def total_cost(self):
        if self.cost_per_unit and self.units_allocated:
            return self.cost_per_unit * self.units_allocated
        return 0

"""
SolutionCodeBundle — Tracks generated code bundles for solutions.

Wave 1: One row per generation. Stores metadata only (not the files).
Files are regenerated deterministically from the same spec_hash.
"""
from app import db
from app.models.solution_models import Solution


class SolutionCodeBundle(db.Model):
    __tablename__ = "solution_code_bundles"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(
        db.Integer,
        db.ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bundle_id = db.Column(db.String(64), nullable=False, unique=True)
    language = db.Column(db.String(30), nullable=False, default="python-fastapi")
    spec_hash = db.Column(db.String(64))
    status = db.Column(db.String(20), default="generated")
    file_count = db.Column(db.Integer)
    test_summary = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.now())

    # Direct class reference and explicit foreign_keys (not a bare "Solution"
    # string) so this relationship never depends on app.models.solution_models
    # having already been imported by the time SQLAlchemy next configures
    # mappers. A deferred string lookup here only resolved because
    # app/models/__init__.py happens to import this module (line ~69)
    # before it imports Solution (line ~318); anything that forces mapper
    # configuration in between -- or any future change to that import
    # order -- would raise "One or more mappers failed to initialize"
    # against this class. Importing Solution directly makes the dependency
    # a normal Python import instead of an ordering assumption.
    solution = db.relationship(
        Solution,
        foreign_keys=[solution_id],
        backref=db.backref("code_bundles", lazy="dynamic"),
    )

    def __repr__(self):
        return f"<SolutionCodeBundle {self.bundle_id} for solution {self.solution_id}>"

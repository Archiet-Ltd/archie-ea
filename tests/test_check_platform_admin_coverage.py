"""The platform-admin-coverage gate finds a write route on a platform-wide model
with no @platform_admin_required.

Each test builds a tiny fake ``app/`` tree in a temp directory, proven against the
exact shape of the real bug it exists for (an admin route, gated only by
@admin_required or nothing, that writes a model with no tenant column at all --
ExternalSystem, Job, AIPromptTemplate, ScoringConfiguration, FeatureFlag in the real
codebase), not just against the repository as it happens to be today.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_platform_admin_coverage.py"
_spec = importlib.util.spec_from_file_location("check_platform_admin_coverage", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_platform_admin_coverage"] = gate
_spec.loader.exec_module(gate)

MODELS = """
from app import db
from app.models.mixins import TenantMixin


class Fenced(TenantMixin, db.Model):
    __tablename__ = "fenced_things"
    id = db.Column(db.Integer, primary_key=True)


class ColumnOnly(db.Model):
    __tablename__ = "column_only_things"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer)


class PlatformWide(db.Model):
    __tablename__ = "platform_wide_things"
    id = db.Column(db.Integer, primary_key=True)
"""


def _tree(tmp_path, code, models=MODELS):
    app = tmp_path / "app"
    (app / "models").mkdir(parents=True, exist_ok=True)
    (app / "models" / "things.py").write_text(models)
    (app / "routes.py").write_text(textwrap.dedent(code))
    return app


def _scan(tmp_path, code):
    app = _tree(tmp_path, code)
    return gate.scan(app=app, repo=tmp_path)


def test_models_in_scope_have_no_tenant_column_at_all(tmp_path):
    """ColumnOnly (has the column, just not the mixin) is check_tenant_scoping.py's
    job, not this gate's -- it would be WRONG to demand platform_admin_required on
    a route that already scopes ColumnOnly by its own organization_id."""
    app = _tree(tmp_path, "x = 1\n")
    models = gate.platform_wide_models(app, tmp_path)

    assert set(models) == {"PlatformWide"}


def test_a_post_route_writing_a_platform_wide_model_with_admin_required_is_flagged(tmp_path):
    """The real bug, in miniature: admin_required is not platform_admin_required."""
    hits = _scan(tmp_path, """
        @bp.route("/settings", methods=["POST"])
        @login_required
        @admin_required
        def save_settings():
            row = PlatformWide(name=request.json["name"])
            db.session.add(row)
            db.session.commit()
    """)

    assert len(hits) == 1
    assert hits[0]["function"] == "save_settings"
    assert hits[0]["models"] == ["PlatformWide"]
    assert hits[0]["methods"] == ["POST"]


def test_a_post_route_with_no_auth_decorator_at_all_is_flagged(tmp_path):
    """Worse than admin_required: no admin check whatsoever (the real
    app/api/dashboard_routes.py ScoringConfiguration bug this gate found)."""
    hits = _scan(tmp_path, """
        @bp.route("/settings", methods=["POST"])
        @login_required
        def save_settings():
            row = PlatformWide(name=request.json["name"])
            db.session.add(row)
    """)

    assert len(hits) == 1


def test_platform_admin_required_clears_the_route(tmp_path):
    hits = _scan(tmp_path, """
        @bp.route("/settings", methods=["POST"])
        @login_required
        @platform_admin_required
        def save_settings():
            row = PlatformWide(name=request.json["name"])
            db.session.add(row)
    """)

    assert hits == []


def test_a_get_only_route_is_not_a_write_route(tmp_path):
    hits = _scan(tmp_path, """
        @bp.route("/settings")
        @login_required
        def list_settings():
            return PlatformWide.query.all()
    """)

    assert hits == []


def test_a_write_route_on_a_tenant_scoped_model_is_not_flagged(tmp_path):
    """Fenced has TenantMixin -- this is not this gate's concern at all."""
    hits = _scan(tmp_path, """
        @bp.route("/widgets", methods=["POST"])
        @login_required
        @admin_required
        def save_widget():
            row = Fenced(name=request.json["name"])
            db.session.add(row)
    """)

    assert hits == []


def test_a_write_route_on_a_column_only_model_is_not_this_gates_concern(tmp_path):
    """ColumnOnly has organization_id but no mixin -- check_tenant_scoping.py's
    job. Demanding platform_admin_required here would be the wrong fix."""
    hits = _scan(tmp_path, """
        @bp.route("/columns", methods=["POST"])
        @login_required
        @admin_required
        def save_column():
            row = ColumnOnly(organization_id=current_user.organization_id)
            db.session.add(row)
    """)

    assert hits == []


def test_every_write_shape_is_seen(tmp_path):
    hits = _scan(tmp_path, """
        @bp.route("/a", methods=["POST"])
        @admin_required
        def a():
            return PlatformWide(name="x")

        @bp.route("/b", methods=["PUT"])
        @admin_required
        def b(i):
            return PlatformWide.query.filter_by(id=i).update({"name": "y"})

        @bp.route("/c", methods=["DELETE"])
        @admin_required
        def c(i):
            row = db.session.get(PlatformWide, i)
            db.session.delete(row)

        @bp.route("/d", methods=["PATCH"])
        @admin_required
        def d(i):
            row = db.session.query(PlatformWide).filter_by(id=i).first()
            row.name = "z"
    """)

    assert sorted(h["function"] for h in hits) == ["a", "b", "c", "d"]


def test_a_bare_read_returned_directly_is_not_a_write(tmp_path):
    """The ADMPhase false positive, in miniature: a lookup used only to
    validate something, never assigned-to or deleted, is not a write."""
    hits = _scan(tmp_path, """
        @bp.route("/validate", methods=["POST"])
        @admin_required
        def validate_transition(i):
            phase = PlatformWide.query.get(i)
            if not phase:
                return {"valid": False}
            return {"valid": True, "order": phase.order}
    """)

    assert hits == []


def test_an_exemption_marker_clears_the_route(tmp_path):
    hits = _scan(tmp_path, """
        @bp.route("/settings", methods=["POST"])
        @login_required
        # platform-admin-ok: receiver verifies an HMAC signature, not a user session
        @admin_required
        def save_settings():
            row = PlatformWide(name=request.json["name"])
            db.session.add(row)
    """)

    assert hits == []


def test_a_bare_marker_with_no_reason_does_not_clear_the_route(tmp_path):
    hits = _scan(tmp_path, """
        @bp.route("/settings", methods=["POST"])
        # platform-admin-ok
        @admin_required
        def save_settings():
            row = PlatformWide(name=request.json["name"])
            db.session.add(row)
    """)

    assert len(hits) == 1


def test_named_platform_models_is_the_confirmed_real_five():
    """pr321-review-v1 nit 3: the narrow ratchet is scoped to exactly the
    five models every PR this session actually fixed -- not a stand-in for
    "every unfenced model", which is the broad scan's (noisier) job."""
    assert gate.NAMED_PLATFORM_MODELS == {
        "ExternalSystem", "Job", "AIPromptTemplate", "ScoringConfiguration", "FeatureFlag",
    }


def test_integration_named_count_matches_baseline():
    """Run scan() against the real app/ directory and assert the named-model
    count equals the expected baseline (10). This test fails on main because
    the FeatureFlag write routes there are still gated by @admin_required
    instead of @platform_admin_required, so the named count on main is higher.
    On this branch the count is exactly 10 (all from ExternalSystem/Job routes
    pending PR #314)."""
    import importlib.util
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "check_platform_admin_coverage.py"
    spec = importlib.util.spec_from_file_location("check_platform_admin_coverage", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_platform_admin_coverage"] = mod
    spec.loader.exec_module(mod)

    models = mod.platform_wide_models()
    hits = mod.scan(models=models)
    named_hits = [h for h in hits if set(h["models"]) & mod.NAMED_PLATFORM_MODELS]
    assert len(named_hits) == 10, (
        f"Expected 10 named-model hits (ExternalSystem/Job routes pending PR #314), "
        f"got {len(named_hits)}. If PR #314 has merged, update this baseline. "
        f"Hits: {[(h['file'], h['function'], h['models']) for h in named_hits]}"
    )


def test_named_count_filters_hits_to_only_named_models(tmp_path):
    """The --named-count filter (verified at the main()/CLI boundary): a hit
    on a model outside NAMED_PLATFORM_MODELS (PlatformWide, this fake tree's
    stand-in for the broad scan's FK-scoped-parent noise) must not count
    toward the narrow ratchet, even though it correctly counts toward the
    broad one."""
    hits = _scan(tmp_path, """
        @bp.route("/settings", methods=["POST"])
        @admin_required
        def save_settings():
            row = PlatformWide(name=request.json["name"])
            db.session.add(row)
    """)

    assert len(hits) == 1
    named_hits = [h for h in hits if set(h["models"]) & gate.NAMED_PLATFORM_MODELS]
    assert named_hits == []

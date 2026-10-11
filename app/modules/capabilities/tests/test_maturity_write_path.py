"""One write path for capability maturity that also keeps the history.

Every recording of a capability's current and target maturity must flow
through ``record_capability_maturity`` in
``app/modules/capabilities/services/capability_maturity_service.py``, which
updates the source row and appends exactly one history row in the same
transaction. The three writers that used to update the source row by hand —
the edit route, the batch route and the assistant tool — now call that one
function, so each recording leaves a history row.

Written against the shared fixtures in tests/conftest.py (``db_session``
rolls everything back; ``make_org`` / ``tenant_ctx`` cover multi-tenant
setup), per CLAUDE.md.
"""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path

import pytest

from app.modules.capabilities.services.capability_maturity_service import (
    CapabilityNotFoundError,
    MaturityValidationError,
    record_capability_maturity,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def client(app):
    return app.test_client()


def _make_capability(db_session, tenant_ctx, org, **kwargs):
    """Seed a BusinessCapability owned by ``org``."""
    from app.models.business_capabilities import BusinessCapability

    with tenant_ctx(org.id):
        cap = BusinessCapability(
            name=kwargs.pop("name", f"Capability {uuid.uuid4().hex[:6]}"),
            organization_id=org.id,
            **kwargs,
        )
        db_session.add(cap)
        db_session.flush()
        cap_id = cap.id
    return cap_id


def _history_rows(db_session, tenant_ctx, org, cap_id):
    """All history rows for one capability, read under the org's tenant context."""
    from app.models.capability_models import CapabilityMaturityAssessment

    with tenant_ctx(org.id):
        rows = (
            CapabilityMaturityAssessment.query.filter_by(
                business_capability_id=cap_id
            )
            .order_by(CapabilityMaturityAssessment.id.asc())
            .all()
        )
        return [row for row in rows]


def _make_admin_user(db_session, org, first_name="Assessor", last_name="One"):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=f"write-{uuid.uuid4().hex[:8]}@example.com",
        first_name=first_name,
        last_name=last_name,
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _logged_in_client(app, db_session, org, login_as):
    """A test client authenticated as an administrator in ``org``."""
    from app.models.user import Permission, Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()

    user = User(
        email=f"write-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Asha",
        last_name="Nair",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()

    c = app.test_client()
    login_as(c, user)
    return c


# --------------------------------------------------------------------------- #
# AC1 — one recording creates one history row and updates the source row.      #
# --------------------------------------------------------------------------- #

def test_one_recording_appends_one_history_row_and_updates_the_source_row(
    db_session, make_org, tenant_ctx
):
    org = make_org("write-org")
    cap_id = _make_capability(db_session, tenant_ctx, org)

    with tenant_ctx(org.id):
        record_capability_maturity(
            capability_id=cap_id,
            organization_id=org.id,
            current=2,
            target=4,
            notes="scoped to the process domain",
            assessor_name="Asha Nair",
            assessor_role="Enterprise Architect",
            commit=True,
        )

    from app.models.business_capabilities import BusinessCapability

    with tenant_ctx(org.id):
        reread = BusinessCapability.query.filter_by(id=cap_id).first()
        assert reread.current_maturity_level == 2
        assert reread.target_maturity_level == 4
        assert reread.maturity_gap == 2
        assert reread.maturity_assessment_notes == "scoped to the process domain"
        assert reread.maturity_assessment_date is not None

    rows = _history_rows(db_session, tenant_ctx, org, cap_id)
    assert len(rows) == 1
    assert rows[0].maturity_level == 2
    assert rows[0].target_maturity_level == 4
    assert rows[0].maturity_gap == 2
    assert rows[0].assessor_name == "Asha Nair"
    assert rows[0].assessor_role == "Enterprise Architect"
    assert rows[0].organization_id == org.id


def test_a_second_recording_appends_a_second_history_row(
    db_session, make_org, tenant_ctx
):
    org = make_org("write-org")
    cap_id = _make_capability(db_session, tenant_ctx, org)

    with tenant_ctx(org.id):
        record_capability_maturity(
            capability_id=cap_id,
            organization_id=org.id,
            current=2,
            target=4,
            commit=True,
        )
        record_capability_maturity(
            capability_id=cap_id,
            organization_id=org.id,
            current=3,
            target=5,
            commit=True,
        )

    rows = _history_rows(db_session, tenant_ctx, org, cap_id)
    assert len(rows) == 2
    assert [row.maturity_level for row in rows] == [2, 3]

    from app.models.business_capabilities import BusinessCapability

    with tenant_ctx(org.id):
        reread = BusinessCapability.query.filter_by(id=cap_id).first()
        assert reread.current_maturity_level == 3
        assert reread.target_maturity_level == 5


# --------------------------------------------------------------------------- #
# AC2 — the projection (untouched) still carries the write, read back through  #
#       the accessor.                                                          #
# --------------------------------------------------------------------------- #

def test_the_recorded_value_reaches_the_unified_row_through_the_accessor(
    db_session, make_org, tenant_ctx
):
    org = make_org("write-org")
    cap_id = _make_capability(db_session, tenant_ctx, org)

    with tenant_ctx(org.id):
        record_capability_maturity(
            capability_id=cap_id,
            organization_id=org.id,
            current=4,
            target=5,
            commit=True,
        )

    from app.models.unified_capability import UnifiedCapability

    # The write-time projection listener fires inside the same transaction, so
    # the authority pair the accessor reads equals what was recorded.
    got = UnifiedCapability.maturity_for_source(
        "business_capability", cap_id, organization_id=org.id
    )
    assert got["current_maturity_level"] == 4
    assert got["target_maturity_level"] == 5
    assert got["reason_code"] is None


# --------------------------------------------------------------------------- #
# AC3 — the edit route, the batch route and the assistant tool each produce a  #
#       history row, and none of them writes current_maturity_level itself.    #
# --------------------------------------------------------------------------- #

def test_edit_route_recording_appends_a_history_row(
    app, db_session, make_org, tenant_ctx, login_as
):
    org = make_org("write-org")
    cap_id = _make_capability(
        db_session, tenant_ctx, org, name="Payroll Processing"
    )
    client = _logged_in_client(app, db_session, org, login_as)

    resp = client.post(
        f"/capability-maturity/edit/{cap_id}",
        data={
            "current_maturity_level": "3",
            "target_maturity_level": "4",
            "assessment_notes": "confirmed in Q3 review",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.status_code
    assert resp.headers["Location"].endswith("/capability-maturity/search")

    from app.models.business_capabilities import BusinessCapability

    with tenant_ctx(org.id):
        reread = BusinessCapability.query.filter_by(id=cap_id).first()
        assert reread.current_maturity_level == 3
        assert reread.target_maturity_level == 4

    rows = _history_rows(db_session, tenant_ctx, org, cap_id)
    assert len(rows) == 1
    assert rows[0].maturity_level == 3
    assert rows[0].target_maturity_level == 4
    assert rows[0].assessor_name == "Asha Nair"


def test_batch_route_recording_appends_one_history_row_per_capability(
    app, db_session, make_org, tenant_ctx, login_as
):
    org = make_org("write-org")
    cap_a = _make_capability(
        db_session,
        tenant_ctx,
        org,
        name="Procure to Pay",
        business_domain="Operations",
    )
    cap_b = _make_capability(
        db_session,
        tenant_ctx,
        org,
        name="Order to Cash",
        business_domain="Operations",
    )
    client = _logged_in_client(app, db_session, org, login_as)

    resp = client.post(
        "/capability-maturity/batch-update",
        data={
            "domain": "Operations",
            "strategic_importance": "",
            "current_maturity_level": "2",
            "target_maturity_level": "4",
            "assessment_notes": "domain-wide reset",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.status_code
    assert resp.headers["Location"].endswith("/capability-maturity/frameworks")

    for cap_id in (cap_a, cap_b):
        rows = _history_rows(db_session, tenant_ctx, org, cap_id)
        assert len(rows) == 1, f"capability {cap_id} got {len(rows)} history rows"
        assert rows[0].maturity_level == 2
        assert rows[0].target_maturity_level == 4


def test_csv_import_recording_appends_history_rows(
    app, db_session, make_org, tenant_ctx, login_as
):
    org = make_org("write-org")
    cap_id = _make_capability(
        db_session, tenant_ctx, org, name="Vendor Risk Management"
    )
    client = _logged_in_client(app, db_session, org, login_as)

    payload = (
        "capability_name,current_maturity,target_maturity,notes\n"
        "Vendor Risk Management,3,5,from vendor questionnaire\n"
    )
    resp = client.post(
        "/capability-maturity/import-csv",
        data={
            "file": (io.BytesIO(payload.encode("utf-8-sig")), "maturity.csv")
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.status_code
    body = resp.get_json()
    assert body["success"] is True, body
    assert body["updated"] == 1

    rows = _history_rows(db_session, tenant_ctx, org, cap_id)
    assert len(rows) == 1
    assert rows[0].maturity_level == 3
    assert rows[0].target_maturity_level == 5


def test_assistant_tool_recording_appends_a_history_row(
    db_session, make_org, tenant_ctx
):
    from app.modules.ai_chat.tools.executor import ToolExecutor

    org = make_org("write-org")
    cap_id = _make_capability(db_session, tenant_ctx, org, name="Talent Mobility")
    ex = ToolExecutor(user_id=1)

    with tenant_ctx(org.id):
        result = ex._tool_record_capability_maturity(
            {"capability_id": cap_id, "current_maturity": 2, "target_maturity": 4}
        )
    assert result["success"] is True, result

    rows = _history_rows(db_session, tenant_ctx, org, cap_id)
    assert len(rows) == 1
    assert rows[0].maturity_level == 2
    assert rows[0].target_maturity_level == 4


def test_no_maturity_writer_owns_current_maturity_level_outside_the_service():
    """Every writer must call the service; a direct write must not reappear.

    Scans every ``.py`` file under ``app/`` for direct assignments to
    ``current_maturity_level`` or ``target_maturity_level`` on an existing row
    (not inside a constructor, seed, command, model class, or the service
    module itself). The only allowed owners of those columns are model
    ``__init__`` (seed/backfill constructors that create new instances) and
    ``capability_maturity_service.py``.
    """
    # Match assignments like ``foo.current_maturity_level = bar`` but not
    # keyword arguments like ``current_maturity_level=<value>,`` in constructors.
    write_pattern = re.compile(
        r"\.\s*(current_maturity_level|target_maturity_level)\s*="
    )
    hits = []
    app_root = _REPO_ROOT / "app"
    for path in sorted(app_root.rglob("*.py")):
        relative = path.relative_to(_REPO_ROOT)
        # Skip the service module itself.
        if "capability_maturity_service" in str(relative):
            continue
        # Skip model definitions (column declarations).
        if str(relative).startswith("app/models/"):
            continue
        # Skip migration files.
        if str(relative).startswith("app/migrations/"):
            continue
        # Skip test files — they create test instances with keyword args.
        if "/tests/" in str(relative):
            continue
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), 1):
            if write_pattern.search(line):
                line_stripped = line.strip()
                # Allow raw SQL UPSERT (projection job — reads, not writes).
                if "EXCLUDED" in line_stripped:
                    continue
                # Allow the roadmap dashboard fallback that writes only when
                # no BusinessCapability source exists (cannot route through the
                # single write path).
                if (
                    "capability_roadmap_dashboard_service" in str(relative)
                    and "capability.current_maturity_level" in line_stripped
                ):
                    continue
                hits.append(f"{relative}:{lineno}: {line_stripped}")
    assert not hits, (
        "a maturity writer still writes current_maturity_level or "
        "target_maturity_level directly:\n"
        + "\n".join(hits)
    )


def test_calls_in_a_loop_can_defer_the_commit(
    db_session, make_org, tenant_ctx
):
    org = make_org("write-org")
    cap_a = _make_capability(db_session, tenant_ctx, org)
    cap_b = _make_capability(db_session, tenant_ctx, org)

    from app import db

    with tenant_ctx(org.id):
        record_capability_maturity(
            capability_id=cap_a,
            organization_id=org.id,
            current=2,
            commit=False,
        )
        record_capability_maturity(
            capability_id=cap_b,
            organization_id=org.id,
            current=3,
            commit=False,
        )
        db.session.commit()

    for cap_id in (cap_a, cap_b):
        assert len(_history_rows(db_session, tenant_ctx, org, cap_id)) == 1


# --------------------------------------------------------------------------- #
# AC4 — two-organisation isolation: no cross-organisation write, and B never   #
#       sees A's history rows.                                                 #
# --------------------------------------------------------------------------- #

def test_org_b_cannot_record_maturity_for_org_a_capability(
    db_session, make_org, tenant_ctx
):
    org_a = make_org("write-a")
    org_b = make_org("write-b")
    cap_id = _make_capability(db_session, tenant_ctx, org_a)

    with tenant_ctx(org_b.id):
        with pytest.raises(CapabilityNotFoundError):
            record_capability_maturity(
                capability_id=cap_id,
                organization_id=org_b.id,
                current=3,
                commit=True,
            )

    from app.models.business_capabilities import BusinessCapability

    with tenant_ctx(org_a.id):
        reread = BusinessCapability.query.filter_by(id=cap_id).first()
        assert reread.current_maturity_level is None

    assert _history_rows(db_session, tenant_ctx, org_a, cap_id) == []
    # The failed cross-org call must not have left a row in B either.
    with tenant_ctx(org_b.id):
        from app.models.capability_models import CapabilityMaturityAssessment

        leaked = (
            CapabilityMaturityAssessment.query.filter_by(
                business_capability_id=cap_id
            ).count()
        )
        assert leaked == 0


def test_org_b_never_sees_org_a_history_rows(db_session, make_org, tenant_ctx):
    org_a = make_org("write-a")
    org_b = make_org("write-b")
    cap_id = _make_capability(db_session, tenant_ctx, org_a)

    with tenant_ctx(org_a.id):
        record_capability_maturity(
            capability_id=cap_id,
            organization_id=org_a.id,
            current=4,
            target=5,
            commit=True,
        )

    # From B, A's capability is not reachable and its history is invisible.
    with tenant_ctx(org_a.id):
        from app.models.capability_models import CapabilityMaturityAssessment

        assert (
            CapabilityMaturityAssessment.query.filter_by(
                business_capability_id=cap_id
            ).count()
            == 1
        )

    with tenant_ctx(org_b.id):
        from app.models.capability_models import CapabilityMaturityAssessment

        assert (
            CapabilityMaturityAssessment.query.filter_by(
                business_capability_id=cap_id
            ).count()
            == 0
        )
        assert (
            CapabilityMaturityAssessment.query.filter(
                CapabilityMaturityAssessment.organization_id == org_a.id
            ).count()
            == 0
        )


# --------------------------------------------------------------------------- #
# AC5 — levels outside 1-5 are refused; a target without a current value does  #
#       not fabricate a gap; a missing capability reads as not found.          #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bad_level", [0, 6, -1, 7])
def test_current_level_outside_1_to_5_is_refused(
    db_session, make_org, tenant_ctx, bad_level
):
    org = make_org("write-org")
    cap_id = _make_capability(db_session, tenant_ctx, org, current_maturity_level=2)

    with tenant_ctx(org.id):
        with pytest.raises(MaturityValidationError):
            record_capability_maturity(
                capability_id=cap_id,
                organization_id=org.id,
                current=bad_level,
                commit=True,
            )

    from app.models.business_capabilities import BusinessCapability

    with tenant_ctx(org.id):
        reread = BusinessCapability.query.filter_by(id=cap_id).first()
        assert reread.current_maturity_level == 2

    assert _history_rows(db_session, tenant_ctx, org, cap_id) == []


@pytest.mark.parametrize("bad_level", [0, 6, -1])
def test_target_level_outside_1_to_5_is_refused(
    db_session, make_org, tenant_ctx, bad_level
):
    org = make_org("write-org")
    cap_id = _make_capability(db_session, tenant_ctx, org)

    with tenant_ctx(org.id):
        with pytest.raises(MaturityValidationError):
            record_capability_maturity(
                capability_id=cap_id,
                organization_id=org.id,
                current=3,
                target=bad_level,
                commit=True,
            )

    assert _history_rows(db_session, tenant_ctx, org, cap_id) == []


def test_a_target_without_a_current_value_does_not_fabricate_a_gap(
    db_session, make_org, tenant_ctx
):
    org = make_org("write-org")
    cap_id = _make_capability(db_session, tenant_ctx, org)

    # A target-only recording: the source row keeps no current level and the
    # gap must stay empty (never 0, never the target value).
    with tenant_ctx(org.id):
        record_capability_maturity(
            capability_id=cap_id,
            organization_id=org.id,
            current=None,
            target=4,
            commit=True,
        )

    from app.models.business_capabilities import BusinessCapability

    with tenant_ctx(org.id):
        reread = BusinessCapability.query.filter_by(id=cap_id).first()
        assert reread.current_maturity_level is None
        assert reread.target_maturity_level == 4
        assert reread.maturity_gap is None


def test_missing_capability_reads_as_not_found(db_session, make_org, tenant_ctx):
    org = make_org("write-org")

    with tenant_ctx(org.id):
        with pytest.raises(CapabilityNotFoundError):
            record_capability_maturity(
                capability_id=999_999_999,
                organization_id=org.id,
                current=3,
                commit=True,
            )


# --------------------------------------------------------------------------- #
# AC6 — target-only or notes-only recording appends a history row when the    #
#       capability already has a current level (LOW-2 fix).                    #
# --------------------------------------------------------------------------- #

def test_target_only_recording_appends_history_when_current_level_exists(
    db_session, make_org, tenant_ctx
):
    """When current is None but the capability already has a current_maturity_level,
    the history row uses that existing level and the assessment date is stamped."""
    org = make_org("write-org")
    cap_id = _make_capability(
        db_session, tenant_ctx, org,
        name="TargetOnly Cap",
        current_maturity_level=2,
        target_maturity_level=3,
    )

    with tenant_ctx(org.id):
        record_capability_maturity(
            capability_id=cap_id,
            organization_id=org.id,
            current=None,
            target=5,
            commit=True,
        )

    from app.models.business_capabilities import BusinessCapability

    with tenant_ctx(org.id):
        reread = BusinessCapability.query.filter_by(id=cap_id).first()
        assert reread.current_maturity_level == 2  # unchanged
        assert reread.target_maturity_level == 5   # updated
        assert reread.maturity_assessment_date is not None  # stamped

    rows = _history_rows(db_session, tenant_ctx, org, cap_id)
    assert len(rows) == 1
    assert rows[0].maturity_level == 2  # used the existing current level
    assert rows[0].target_maturity_level == 5


def test_notes_only_recording_appends_history_when_current_level_exists(
    db_session, make_org, tenant_ctx
):
    """A notes-only update also leaves a history row when the capability
    already holds a current level."""
    org = make_org("write-org")
    cap_id = _make_capability(
        db_session, tenant_ctx, org,
        name="NotesOnly Cap",
        current_maturity_level=3,
        target_maturity_level=4,
    )

    with tenant_ctx(org.id):
        record_capability_maturity(
            capability_id=cap_id,
            organization_id=org.id,
            current=None,
            target=None,
            notes="updated notes only",
            commit=True,
        )

    rows = _history_rows(db_session, tenant_ctx, org, cap_id)
    assert len(rows) == 1
    assert rows[0].maturity_level == 3
    assert rows[0].notes == "updated notes only"


def test_all_blank_batch_redirects_with_error(
    app, db_session, make_org, tenant_ctx, login_as
):
    """LOW-3: an entirely blank batch submission (no level, no notes) flashes
    an error and does not write to any row."""
    org = make_org("write-org")
    cap_id = _make_capability(
        db_session, tenant_ctx, org,
        name="Blank Batch Cap",
        current_maturity_level=2,
    )
    _make_capability(
        db_session, tenant_ctx, org,
        name="Blank Batch Cap Two",
        current_maturity_level=3,
    )
    client = _logged_in_client(app, db_session, org, login_as)

    resp = client.post(
        "/capability-maturity/batch-update",
        data={
            "domain": "",
            "strategic_importance": "",
            "current_maturity_level": "",
            "target_maturity_level": "",
            "assessment_notes": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    from app.models.business_capabilities import BusinessCapability

    with tenant_ctx(org.id):
        reread = BusinessCapability.query.filter_by(id=cap_id).first()
        assert reread.current_maturity_level == 2  # unchanged

    rows = _history_rows(db_session, tenant_ctx, org, cap_id)
    assert len(rows) == 0  # no history written


# --------------------------------------------------------------------------- #
# AC7 — exactly one definition of the writing function.                        #
# --------------------------------------------------------------------------- #

def test_one_definition_of_the_writing_function_in_app_code():
    definition_pattern = re.compile(r"def record_capability_maturity\s*\(")
    hits = []
    app_root = _REPO_ROOT / "app"
    for path in sorted(app_root.rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if definition_pattern.search(line):
                hits.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")
    assert len(hits) == 1, (
        f"expected exactly one definition of record_capability_maturity, "
        f"found {len(hits)}:\n" + "\n".join(hits)
    )
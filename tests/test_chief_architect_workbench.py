"""Truthfulness contract for the enterprise lenses of the Chief Architect Workbench.

The workbench aggregates five enterprise domains beside the existing solution /
ARB / transformation posture. Its whole value rests on a reader being able to
tell a measured fact from a missing one, so these tests pin exactly that:

* a count is a real ``COUNT`` over a named column, with its own denominator;
* a lens that cannot be read reports ``unavailable`` with ``None``, never ``0``;
* records missing a field are counted as missing rather than dropped;
* counts are tenant-scoped;
* every measure names the column it came from, so the page is traceable.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.organization import Organization
from app.models.user import User
from app.modules.solutions_strategic.v2.services.enterprise_posture_service import (
    EnterprisePostureService,
)


def _org(db_session, slug):
    org = Organization(name=f"Workbench {slug}", slug=f"workbench-{slug}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org):
    user = User(
        email=f"workbench-{org.id}@example.com",
        first_name="Chief",
        last_name="Architect",
        organization_id=org.id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    user.password = "TestPass123!"
    db_session.add(user)
    db_session.flush()
    return user


def _today():
    return datetime.now(timezone.utc).date()


def _lens(posture, key):
    return next(lens for lens in posture["lenses"] if lens["key"] == key)


def _measure(lens, label):
    return next(m for m in lens["measures"] if m["label"] == label)


def _missing(lens, label):
    return next(m for m in lens["missing"] if m["label"] == label)


# ── application portfolio ────────────────────────────────────────────────────


def test_application_obsolescence_counts_only_dated_records(db_session, tenant_ctx):
    """Applications with no end-of-life date are missing, not compliant.

    The trap this pins: counting undated applications into the "not past EOL"
    numerator would report a clean portfolio built mostly out of ignorance.
    """
    from app.models.application_portfolio import ApplicationComponent

    org = _org(db_session, "eol")
    today = _today()
    with tenant_ctx(org.id):
        db_session.add_all(
            [
                ApplicationComponent(
                    name="Past EOL", end_of_life_date=today - timedelta(days=30)
                ),
                ApplicationComponent(
                    name="EOL soon", end_of_life_date=today + timedelta(days=100)
                ),
                ApplicationComponent(
                    name="EOL far", end_of_life_date=today + timedelta(days=900)
                ),
                ApplicationComponent(name="No EOL date"),
            ]
        )
        db_session.flush()
        posture = EnterprisePostureService.enterprise_posture()

    lens = _lens(posture, "application")
    assert lens["state"] == "measured"
    assert lens["total"] == 4

    past = _measure(lens, "Past end of life")
    assert past["value"] == 1
    # Denominator is dated records only — the undated one is excluded from both
    # sides rather than silently counted as healthy.
    assert past["of"] == 3
    assert past["source"] == "ApplicationComponent.end_of_life_date < today"

    soon = _measure(lens, "End of life within 12 months")
    assert soon["value"] == 1
    assert soon["of"] == 3

    undated = _missing(lens, "Applications with no end-of-life date")
    assert undated["value"] == 1
    assert undated["of"] == 4
    assert undated["kind"] == "missing"


def test_application_missing_fields_are_counted_not_dropped(db_session, tenant_ctx):
    """An empty string counts as missing, exactly as NULL does."""
    from app.models.application_portfolio import ApplicationComponent

    org = _org(db_session, "missing-fields")
    with tenant_ctx(org.id):
        db_session.add_all(
            [
                ApplicationComponent(
                    name="Complete",
                    lifecycle_status="production",
                    criticality="high",
                    application_owner="A. Owner",
                ),
                ApplicationComponent(name="Blank owner", application_owner=""),
                ApplicationComponent(name="Null everything"),
            ]
        )
        db_session.flush()
        posture = EnterprisePostureService.enterprise_posture()

    lens = _lens(posture, "application")
    assert _measure(lens, "Lifecycle status recorded")["value"] == 1
    assert _measure(lens, "Criticality recorded")["value"] == 1
    assert _missing(lens, "Applications with no lifecycle status")["value"] == 2
    assert _missing(lens, "Applications with no recorded owner")["value"] == 2


def test_past_eol_raises_a_critical_attention_item_with_a_real_action(
    db_session, tenant_ctx
):
    from app.models.application_portfolio import ApplicationComponent

    org = _org(db_session, "eol-attention")
    with tenant_ctx(org.id):
        db_session.add(
            ApplicationComponent(
                name="Unsupported", end_of_life_date=_today() - timedelta(days=1)
            )
        )
        db_session.flush()
        posture = EnterprisePostureService.enterprise_posture()

    item = next(
        i for i in posture["attention"] if i["id"] == "application-past-eol"
    )
    assert item["severity"] == "critical"
    assert item["flagged"] == 1
    assert item["next_action"]
    # The queue is only useful if a row leads to the record it is about.
    assert item["action_url"] == item["evidence_url"]


# ── capability ───────────────────────────────────────────────────────────────


def test_capability_maturity_denominator_excludes_unassessed(db_session, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability

    org = _org(db_session, "capability")
    with tenant_ctx(org.id):
        db_session.add_all(
            [
                BusinessCapability(
                    name="Assessed on target",
                    current_maturity_level=3,
                    maturity_gap=0,
                    business_owner="B. Owner",
                ),
                BusinessCapability(
                    name="Assessed with gap",
                    current_maturity_level=2,
                    maturity_gap=2,
                ),
                BusinessCapability(name="Never assessed"),
            ]
        )
        db_session.flush()
        posture = EnterprisePostureService.enterprise_posture()

    lens = _lens(posture, "capability")
    assert lens["total"] == 3
    assert _measure(lens, "Maturity assessed")["value"] == 2
    assert _measure(lens, "Below target maturity")["value"] == 1
    assert _missing(lens, "Capabilities with no maturity assessment")["value"] == 1
    assert _missing(lens, "Capabilities with no business owner")["value"] == 2


# ── standards & exceptions ───────────────────────────────────────────────────


def test_expired_exception_in_force_is_critical(db_session, tenant_ctx):
    """An approved exception past its expiry is a deviation without a mandate."""
    from app.models.architecture_review_board import ARBException

    org = _org(db_session, "exceptions")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with tenant_ctx(org.id):
        user = _user(db_session, org)
        db_session.add_all(
            [
                ARBException(
                    organization_id=org.id,
                    exception_number="EXC-EXPIRED",
                    status="approved",
                    requested_by_id=user.id,
                    expires_at=now - timedelta(days=5),
                ),
                ARBException(
                    organization_id=org.id,
                    exception_number="EXC-LIVE",
                    status="approved",
                    requested_by_id=user.id,
                    expires_at=now + timedelta(days=30),
                ),
                ARBException(
                    organization_id=org.id,
                    exception_number="EXC-UNDATED",
                    status="approved",
                    requested_by_id=user.id,
                ),
                # Revoked: no longer in force, must not be counted.
                ARBException(
                    organization_id=org.id,
                    exception_number="EXC-REVOKED",
                    status="approved",
                    requested_by_id=user.id,
                    revoked_at=now - timedelta(days=1),
                ),
            ]
        )
        db_session.flush()
        posture = EnterprisePostureService.enterprise_posture()

    lens = _lens(posture, "standards")
    assert _measure(lens, "Approved exceptions in force")["value"] == 3
    assert _measure(lens, "Exceptions expiring within 90 days")["value"] == 1
    assert _missing(lens, "Exceptions in force with no expiry date")["value"] == 1

    expired = next(
        i for i in posture["attention"] if i["id"] == "standards-exception-expired"
    )
    assert expired["severity"] == "critical"
    assert expired["flagged"] == 1


# ── roadmap ──────────────────────────────────────────────────────────────────


def test_unknown_gap_status_is_treated_as_open(db_session, tenant_ctx):
    """An unfamiliar resolution_status must stay visible, not vanish as resolved."""
    from app.models.implementation_migration import Gap

    org = _org(db_session, "gaps")
    with tenant_ctx(org.id):
        db_session.add_all(
            [
                Gap(name="Closed", resolution_status="resolved"),
                Gap(name="Closed differently", resolution_status="CLOSED"),
                Gap(name="Unknown status", resolution_status="parked"),
                Gap(name="No status at all"),
            ]
        )
        db_session.flush()
        posture = EnterprisePostureService.enterprise_posture()

    lens = _lens(posture, "roadmap")
    open_gaps = _measure(lens, "Open gaps")
    assert open_gaps["value"] == 2
    assert open_gaps["of"] == 4


def test_overdue_work_package_ignores_completed_ones(db_session, tenant_ctx):
    from app.models.implementation_migration import WorkPackage

    org = _org(db_session, "packages")
    past = _today() - timedelta(days=10)
    with tenant_ctx(org.id):
        db_session.add_all(
            [
                WorkPackage(name="Late", target_date=past),
                WorkPackage(name="Late but done", target_date=past, completed_date=past),
                WorkPackage(name="Undated"),
            ]
        )
        db_session.flush()
        posture = EnterprisePostureService.enterprise_posture()

    lens = _lens(posture, "roadmap")
    overdue = _measure(lens, "Work packages past target date")
    assert overdue["value"] == 1
    assert overdue["of"] == 2
    assert _missing(lens, "Work packages with no target date")["value"] == 1


# ── decisions ────────────────────────────────────────────────────────────────


def test_decision_quality_gaps_are_reported_as_missing(db_session, tenant_ctx):
    from app.models.architecture_decision import ArchitectureDecision

    org = _org(db_session, "decisions")
    with tenant_ctx(org.id):
        db_session.add_all(
            [
                ArchitectureDecision(
                    decision_id="AD-1",
                    title="Well recorded",
                    status="accepted",
                    context="A context.",
                    decision="A decision.",
                    rationale="Because.",
                    consequences="These.",
                    decided_at=datetime(2026, 1, 1),
                ),
                # `rationale` and `consequences` are both NOT NULL in the
                # database, so an unrecorded value reaches the record as an empty
                # string rather than as NULL. The lens counts both forms; this
                # pins the reachable one.
                ArchitectureDecision(
                    decision_id="AD-2",
                    title="Bare proposal",
                    status="proposed",
                    context="A context.",
                    decision="A decision.",
                    rationale="",
                    consequences="",
                ),
            ]
        )
        db_session.flush()
        posture = EnterprisePostureService.enterprise_posture()

    lens = _lens(posture, "decision")
    assert _measure(lens, "Accepted")["value"] == 1
    assert _measure(lens, "Awaiting a decision")["value"] == 1
    assert _missing(lens, "Decisions with no rationale")["value"] == 1
    assert _missing(lens, "Decisions with no decision date")["value"] == 1


# ── the rules that make the page trustworthy ─────────────────────────────────


def test_a_failed_lens_reports_unavailable_and_never_zero(
    monkeypatch, db_session, tenant_ctx
):
    """The single most important guarantee: a broken query must not read as zero."""
    org = _org(db_session, "failure")

    def boom():
        raise RuntimeError("application catalogue unavailable")

    monkeypatch.setattr(
        EnterprisePostureService, "_application_lens", staticmethod(boom)
    )

    with tenant_ctx(org.id):
        posture = EnterprisePostureService.enterprise_posture()

    lens = _lens(posture, "application")
    assert lens["state"] == "unavailable"
    assert lens["total"] is None
    assert lens["measures"] == []
    assert lens["missing"] == []
    assert lens["reason"]
    assert posture["lenses_unavailable"] == 1
    # The other lenses still render — one broken domain does not lose the page.
    # On this empty org they report "empty" (a measured answer), never
    # "unavailable"; the point is that exactly one lens failed, not all of them.
    others = [lens for lens in posture["lenses"] if lens["key"] != "application"]
    assert others
    assert all(lens["state"] == "empty" for lens in others)
    assert posture["state"] == "measured"


def test_every_measure_names_its_source_column(db_session, tenant_ctx):
    """Traceability: a reader must be able to reproduce any number on the page."""
    org = _org(db_session, "provenance")
    with tenant_ctx(org.id):
        posture = EnterprisePostureService.enterprise_posture()

    entries = [
        entry
        for lens in posture["lenses"]
        for entry in (lens["measures"] + lens["missing"])
    ]
    assert entries, "expected at least one measure across the lenses"
    for entry in entries:
        assert entry["source"], f"{entry['label']} has no source"
        assert entry["kind"] in {"measured", "missing"}


def test_counts_are_tenant_scoped(db_session, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent

    mine = _org(db_session, "tenant-mine")
    other = _org(db_session, "tenant-other")

    with tenant_ctx(mine.id):
        db_session.add(ApplicationComponent(name="Mine"))
        db_session.flush()
    with tenant_ctx(other.id):
        db_session.add_all(
            [ApplicationComponent(name=f"Theirs {n}") for n in range(5)]
        )
        db_session.flush()

    with tenant_ctx(mine.id):
        posture = EnterprisePostureService.enterprise_posture()

    assert _lens(posture, "application")["total"] == 1


def test_empty_tenant_reports_empty_rather_than_unavailable(db_session, tenant_ctx):
    """Nothing modelled is a real, measured answer — distinct from cannot-measure."""
    org = _org(db_session, "empty")
    with tenant_ctx(org.id):
        posture = EnterprisePostureService.enterprise_posture()

    assert posture["state"] == "measured"
    assert posture["lenses_unavailable"] == 0
    assert all(lens["state"] == "empty" for lens in posture["lenses"])
    assert posture["attention"] == []


# ── the AI briefing ──────────────────────────────────────────────────────────


def test_briefing_prompt_inherits_the_governed_evidence_rules():
    """The advisory surface must carry the platform's own AI governance.

    Restating the rules locally would let this surface drift away from every
    other architect persona the first time the shared rules changed.
    """
    from app.modules.ai_chat.services.architect_persona_charters import (
        governed_evidence_rules,
    )
    from app.modules.solutions_strategic.v2.services import (
        chief_architect_briefing_service as briefing,
    )

    prompt = briefing._build_prompt({"enterprise_lenses": []})

    assert governed_evidence_rules().strip() in prompt
    assert "NO FABRICATION" in prompt
    # The model must be told that a null is an absence of evidence, not a zero —
    # this is the single instruction that stops an outage reading as a clean bill.
    assert "COULD NOT BE MEASURED" in prompt
    assert "does not mean" in prompt


def test_briefing_digest_passes_only_what_the_reader_can_see():
    """The model may not be handed internal plumbing it would describe as findings."""
    from app.modules.solutions_strategic.v2.services.chief_architect_briefing_service import (
        evidence_digest,
    )

    digest = evidence_digest(
        {
            "scope": {"in_scope": 1},
            "avg_conformance": None,
            "attention": [
                {
                    "source_label": "Application portfolio",
                    "title": "1 past end of life",
                    "severity": "critical",
                    "reason": "Unsupported.",
                    "action_url": "/applications/",
                    "evidence_url": "/applications/",
                    "id": "application-past-eol",
                }
            ],
            "enterprise": {
                "lenses": [
                    {
                        "key": "application",
                        "label": "Application portfolio",
                        "state": "measured",
                        "total": 1,
                        "total_label": "application components",
                        "measures": [
                            {
                                "label": "Past end of life",
                                "value": 1,
                                "of": 1,
                                "source": "ApplicationComponent.end_of_life_date < today",
                            }
                        ],
                        "missing": [
                            {"label": "No owner", "value": 1, "of": 1, "source": "x"},
                            {"label": "Not a gap", "value": 0, "of": 1, "source": "y"},
                        ],
                    }
                ]
            },
        }
    )

    lens = digest["enterprise_lenses"][0]
    assert lens["measured"][0]["source_column"]
    # Zero-valued "missing" rows are not gaps and must not be listed as such.
    assert [m["label"] for m in lens["missing_information"]] == ["No owner"]
    # Internal routing keys must not reach the model.
    item = digest["attention_queue"][0]
    assert set(item) == {"domain", "title", "severity", "why"}


def test_briefing_never_substitutes_a_fallback(monkeypatch):
    """A briefing that cannot be trusted must raise, not be invented.

    Prose carries no em dash, so a fabricated briefing is undetectable to the
    reader — this is the one place a fallback would be most harmful.
    """
    from app.modules.solutions_strategic.v2.services import (
        chief_architect_briefing_service as briefing,
    )

    monkeypatch.setattr(
        briefing.LLMService,
        "generate_from_prompt",
        staticmethod(lambda *a, **k: "not json at all"),
    )
    with pytest.raises(briefing.ChiefArchitectBriefingError):
        briefing.generate_chief_architect_briefing({})

    monkeypatch.setattr(
        briefing.LLMService,
        "generate_from_prompt",
        staticmethod(lambda *a, **k: '{"headline": "ok"}'),
    )
    with pytest.raises(briefing.ChiefArchitectBriefingError):
        briefing.generate_chief_architect_briefing({})

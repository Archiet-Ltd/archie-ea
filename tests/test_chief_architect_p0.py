"""P0 truthfulness contract for the Chief Architect command centre.

The page must describe exactly the deterministic evidence it has, rather than
turn unavailable controls or partial enterprise data into a clean portfolio
claim.
"""

from datetime import datetime, timedelta, timezone
import re

from app import db
from app.models.organization import Organization
from app.models.user import User
from app.modules.solutions_strategic.v2.services.chief_architect_service import (
    ChiefArchitectService,
)


def _org(db_session, slug):
    org = Organization(name=f"Chief Architect {slug}", slug=f"chief-{slug}")
    db_session.add(org)
    db_session.flush()
    return org


def _solution(db_session, name):
    from app.models.solution_models import Solution

    solution = Solution(name=name)
    db_session.add(solution)
    db_session.flush()
    return solution


def _application_element(db_session, solution_id, name="Application"):
    return _domain_element(
        db_session,
        solution_id,
        name=name,
        element_type="ApplicationComponent",
        layer="application",
        element_table="application_components",
    )


def _domain_element(
    db_session,
    solution_id,
    *,
    name,
    element_type,
    layer,
    element_table,
):
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_models import SolutionArchiMateElement

    element = ArchiMateElement(name=name, type=element_type, layer=layer)
    db_session.add(element)
    db_session.flush()
    db_session.add(
        SolutionArchiMateElement(
            solution_id=solution_id,
            element_id=element.id,
            layer_type=layer,
            element_table=element_table,
            element_name=name,
        )
    )
    db_session.flush()
    return element


def _review_item(db_session, org, number, status, submitted_at, submitter_id, decision=None):
    from app.models.architecture_review_board import ARBReviewItem

    item = ARBReviewItem(
        organization_id=org.id,
        review_number=number,
        title=number,
        review_type="solution",
        status=status,
        submitted_at=submitted_at,
        submitter_id=submitter_id,
        decision=decision,
    )
    db_session.add(item)
    db_session.flush()
    return item


def _user(db_session, org):
    user = User(
        email=f"chief-{org.id}@example.com",
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


def _visible_text(resp):
    html = resp.get_data(as_text=True)
    html = re.sub(r"(?is)<script\b.*?</script>", " ", html)
    html = re.sub(r"(?is)<style\b.*?</style>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html))


def test_unavailable_required_control_withholds_aggregate_and_is_ranked(monkeypatch, db_session, tenant_ctx):
    """A failed review control must not produce a conformance aggregate."""
    from app.modules.solutions_strategic.v2.services.conformance_reviewer import (
        ConformanceReviewer,
    )

    org = _org(db_session, "unavailable")
    with tenant_ctx(org.id):
        solution = _solution(db_session, "Control failure")
        _application_element(db_session, solution.id)

        def unavailable_business_control(_solution_id):
            raise RuntimeError("business catalogue unavailable")

        monkeypatch.setattr(
            ConformanceReviewer,
            "_business_findings",
            staticmethod(unavailable_business_control),
        )

        synthesis = ChiefArchitectService.portfolio_synthesis()

    assert synthesis["solutions_unavailable"] == 1
    assert synthesis["avg_conformance"] is None
    assert synthesis["coverage"]["evaluated"] == 0
    assert synthesis["attention"][0]["status"] == "unavailable"
    assert "business" in synthesis["attention"][0]["reason"].lower()
    assert synthesis["attention"][0]["evidence_url"].endswith("/review-packet")


def test_arb_summary_uses_authoritative_statuses_and_tenant_scope(db_session, tenant_ctx):
    """Open, blocked, decided and SLA age come from visible ARB records only."""
    mine = _org(db_session, "arb-mine")
    other = _org(db_session, "arb-other")
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with tenant_ctx(mine.id):
        mine_user = _user(db_session, mine)
        _review_item(db_session, mine, "REV-MINE-OPEN", "submitted", now - timedelta(days=24), mine_user.id)
        _review_item(db_session, mine, "REV-MINE-BLOCKED", "pending_information", now - timedelta(days=7), mine_user.id)
        _review_item(db_session, mine, "REV-MINE-DECIDED", "approved", now - timedelta(days=4), mine_user.id, "approved")
    with tenant_ctx(other.id):
        other_user = _user(db_session, other)
        _review_item(db_session, other, "REV-OTHER-OPEN", "under_review", now - timedelta(days=90), other_user.id)

    with tenant_ctx(mine.id):
        synthesis = ChiefArchitectService.portfolio_synthesis()

    assert synthesis["arb"] == {
        "open": 2,
        "decided": 1,
        "blocked_or_not_ready": 1,
        "dated_open": 2,
        "overdue_open": 1,
        "oldest_open_age_days": 24,
        "undated_open": 0,
        "sla_days": 21,
    }


def test_command_centre_synthesises_canonical_evidence_and_scopes_dependencies(
    db_session, tenant_ctx
):
    """Measured lines of sight use canonical records and visible solution IDs."""
    from app.models.solution_lifecycle_models import SolutionPlateau, SolutionRisk
    from app.models.solution_sad_models import MigrationDependency
    from app.models.strategic import StrategicInitiative

    mine = _org(db_session, "evidence-mine")
    other = _org(db_session, "evidence-other")

    with tenant_ctx(mine.id):
        programme = StrategicInitiative(
            name="Core renewal",
            initiative_type="brownfield",
            status="in_progress",
            priority="high",
        )
        db_session.add(programme)
        db_session.flush()
        solution = _solution(db_session, "Customer platform")
        solution.initiative_id = programme.id
        solution.status = "in_progress"
        solution.deployment_status = "testing"
        solution.adm_phase = "G"
        for name, element_type, layer, table in (
            ("Serve customer", "BusinessProcess", "business", "business_processes"),
            ("Customer record", "DataObject", "application", "data_objects"),
            ("Customer API", "ApplicationComponent", "application", "application_components"),
            ("Runtime", "Node", "technology", "technology_nodes"),
            ("Growth", "Goal", "motivation", "goals"),
        ):
            _domain_element(
                db_session,
                solution.id,
                name=name,
                element_type=element_type,
                layer=layer,
                element_table=table,
            )
        db_session.add(
            SolutionRisk(
                solution_id=solution.id,
                risk_name="Cutover outage",
                risk_description="Customer service may be unavailable during cutover.",
                impact="critical",
                probability="high",
                status="open",
            )
        )
        current = SolutionPlateau(solution_id=solution.id, name="Current")
        target = SolutionPlateau(solution_id=solution.id, name="Target")
        db_session.add_all([current, target])
        db_session.flush()
        db_session.add(
            MigrationDependency(
                solution_id=solution.id,
                from_plateau_id=current.id,
                to_plateau_id=target.id,
                dependency_type="strict_precedence",
                lag_days=10,
            )
        )
        db_session.flush()

    with tenant_ctx(other.id):
        hidden_solution = _solution(db_session, "Hidden programme dependency")
        hidden_current = SolutionPlateau(solution_id=hidden_solution.id, name="Hidden current")
        hidden_target = SolutionPlateau(solution_id=hidden_solution.id, name="Hidden target")
        db_session.add_all([hidden_current, hidden_target])
        db_session.flush()
        db_session.add(
            MigrationDependency(
                solution_id=hidden_solution.id,
                from_plateau_id=hidden_current.id,
                to_plateau_id=hidden_target.id,
            )
        )
        db_session.flush()

    with tenant_ctx(mine.id):
        synthesis = ChiefArchitectService.portfolio_synthesis()

    assert synthesis["strategic"] == {
        "state": "available",
        "programmes_total": 1,
        "programmes_in_flight": 1,
        "solutions_assigned": 1,
        "solutions_denominator": 1,
    }
    domains = {item["key"]: item for item in synthesis["architecture"]["domains"]}
    assert synthesis["architecture"]["state"] == "available"
    assert synthesis["architecture"]["measured"] == 1
    assert synthesis["architecture"]["in_scope"] == 1
    assert {
        key: (domains[key]["covered"], domains[key]["denominator"])
        for key in ("business", "data", "application", "technology", "motivation")
    } == {key: (1, 1) for key in ("business", "data", "application", "technology", "motivation")}
    assert synthesis["delivery"]["in_scope"] == 1
    assert synthesis["delivery"]["in_progress"] == 1
    assert synthesis["delivery"]["phase_counts"] == {"G": 1}
    assert synthesis["risk_dependency"]["risks"]["material_open"] == 1
    assert synthesis["risk_dependency"]["risks"]["unowned_material"] == 1
    assert synthesis["risk_dependency"]["dependencies"] == {
        "state": "available",
        "recorded": 1,
        "strict_precedence": 1,
        "with_lag": 1,
    }
    risk_items = [item for item in synthesis["attention"] if item["kind"] == "risk"]
    assert len(risk_items) == 1
    assert risk_items[0]["severity"] == "critical"
    assert risk_items[0]["next_action"] == "Assign an owner and agree mitigation"
    assert risk_items[0]["evidence_url"].endswith("/risks/heatmap")


def test_command_centre_withholds_domain_counts_when_measurement_fails(
    monkeypatch, db_session, tenant_ctx
):
    """A failed domain read is unavailable, never five measured zeroes."""
    from app.modules.solutions_strategic.v2.services.conformance_reviewer import (
        ConformanceReviewer,
    )

    org = _org(db_session, "domain-unavailable")
    with tenant_ctx(org.id):
        _solution(db_session, "Domain read failure")

        def unavailable_elements(_solution_id):
            raise RuntimeError("element catalogue unavailable")

        monkeypatch.setattr(
            ConformanceReviewer,
            "_element_counts",
            staticmethod(unavailable_elements),
        )
        synthesis = ChiefArchitectService.portfolio_synthesis()

    assert synthesis["architecture"]["state"] == "unavailable"
    assert synthesis["architecture"]["measured"] == 0
    assert synthesis["architecture"]["unavailable"] == 1
    assert all(item["covered"] is None for item in synthesis["architecture"]["domains"])
    assert all(item["denominator"] is None for item in synthesis["architecture"]["domains"])


def test_overdue_arb_review_enters_attention_with_age_evidence_and_action(
    db_session, tenant_ctx
):
    """An overdue review is a prioritised intervention, not just a summary count."""
    org = _org(db_session, "arb-attention")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with tenant_ctx(org.id):
        user = _user(db_session, org)
        review = _review_item(
            db_session,
            org,
            "REV-OVERDUE",
            "under_review",
            now - timedelta(days=30),
            user.id,
        )
        synthesis = ChiefArchitectService.portfolio_synthesis()

    arb_items = [item for item in synthesis["attention"] if item["kind"] == "governance"]
    assert len(arb_items) == 1
    assert arb_items[0]["id"] == review.id
    assert arb_items[0]["severity"] == "high"
    assert arb_items[0]["age_days"] == 30
    assert arb_items[0]["evidence_url"].endswith(f"/arb/reviews/{review.id}")
    assert arb_items[0]["next_action"] == "Progress the review to a recorded decision"


def test_review_packet_renders_unavailable_conformance_controls(
    monkeypatch, db_session, client, login_as, tenant_ctx
):
    """A failed check is evidence of unavailable readiness, never a clean review."""
    from app.modules.solutions_strategic.v2.services.conformance_reviewer import (
        ConformanceReviewer,
    )

    org = _org(db_session, "packet-unavailable")
    with tenant_ctx(org.id):
        user = _user(db_session, org)
        solution = _solution(db_session, "Unavailable packet controls")

    monkeypatch.setattr(
        ConformanceReviewer,
        "review",
        staticmethod(
            lambda _solution_id: {
                "success": True,
                "score": None,
                "flagged": 0,
                "findings": [],
                "unassessed": False,
                "controls_available": False,
                "unavailable_checks": ["business"],
                "summary": "Conformance controls unavailable: business. No score has been calculated.",
            }
        ),
    )
    login_as(client, user)

    response = client.get(f"/solutions/{solution.id}/review-packet")

    assert response.status_code == 200
    text = _visible_text(response)
    assert "Conformance controls unavailable" in text
    assert "Required conformance controls available" in text
    assert "business" in text
    assert "Ready for the board" not in text


def test_attention_metadata_discloses_when_queue_is_limited(db_session, tenant_ctx):
    """The visible queue must disclose rows omitted by its fixed display limit."""
    org = _org(db_session, "attention-limit")
    with tenant_ctx(org.id):
        for number in range(11):
            _solution(db_session, f"Unassessed {number}")
        synthesis = ChiefArchitectService.portfolio_synthesis()

    assert len(synthesis["attention"]) == 10
    assert synthesis["attention_total"] == 11
    assert synthesis["attention_displayed"] == 10
    assert synthesis["attention_truncated"] is True


def test_withdrawn_arb_record_is_not_decided_throughput(db_session, tenant_ctx):
    """Withdrawn records are closed administratively, not ARB decision output."""
    org = _org(db_session, "withdrawn")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with tenant_ctx(org.id):
        user = _user(db_session, org)
        _review_item(db_session, org, "REV-WITHDRAWN", "withdrawn", now, user.id)
        _review_item(db_session, org, "REV-APPROVED", "approved", now, user.id, "approved")
        synthesis = ChiefArchitectService.portfolio_synthesis()

    assert synthesis["arb"]["decided"] == 1


def test_arb_oldest_age_is_withheld_when_an_open_record_has_no_submission_date(
    db_session, tenant_ctx
):
    """An undated open review makes the queue age incomplete, not precisely younger."""
    org = _org(db_session, "undated-open")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with tenant_ctx(org.id):
        user = _user(db_session, org)
        _review_item(db_session, org, "REV-DATED", "submitted", now - timedelta(days=24), user.id)
        _review_item(db_session, org, "REV-UNDATED", "under_review", None, user.id)
        synthesis = ChiefArchitectService.portfolio_synthesis()

    assert synthesis["arb"]["open"] == 2
    assert synthesis["arb"]["undated_open"] == 1
    assert synthesis["arb"]["oldest_open_age_days"] is None


def test_flagged_total_is_withheld_without_an_evaluated_review(db_session, tenant_ctx):
    """Zero issues cannot be presented as clean when no solution was evaluable."""
    org = _org(db_session, "no-evaluated")
    with tenant_ctx(org.id):
        _solution(db_session, "No evidence")
        synthesis = ChiefArchitectService.portfolio_synthesis()

    assert synthesis["coverage"]["evaluated"] == 0
    assert synthesis["flagged_total"] is None

"""Every place that prints a risk's score prints the score its band is computed from.

Two risks share a base likelihood and impact of 2 x 5: one is treated down to a residual 1 x 2, the
other has an inherent pair of 4 x 4 recorded. Neither base score of 10 may appear beside a band
computed from a different number, on the ranking sentence, the assessment order, the assistant's
risk answers, the heat map payload, the solution record or the persona context.
"""

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _risk(db_session, org_id, title, pair=(2, 5), residual=None, inherent=None, element=None, solution_id=None):
    from app.models.risk import Risk

    row = Risk(title=title, likelihood=pair[0], impact=pair[1], organization_id=org_id,
               residual_likelihood=residual[0] if residual else None,
               residual_impact=residual[1] if residual else None,
               inherent_likelihood=inherent[0] if inherent else None,
               inherent_impact=inherent[1] if inherent else None,
               archimate_element_id=element.id if element is not None else None,
               solution_id=solution_id)
    db_session.add(row)
    db_session.flush()
    return row


def _element(db_session, org_id, name):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def test_the_pair_a_score_comes_from_is_named_once(db_session, make_org):
    org = make_org("pair")
    treated = _risk(db_session, org.id, "Treated", residual=(1, 2))
    raw = _risk(db_session, org.id, "Raw", pair=(2, 2), inherent=(4, 4))
    bare = _risk(db_session, org.id, "Bare", pair=(3, 3))
    assert [(r.score_kind, r._effective_score, r.score_text) for r in (treated, raw, bare)] == [
        ("Residual", 2, "Residual 2"), ("Inherent", 16, "Inherent 16"), ("Inherent", 9, "Inherent 9")]
    assert [r.risk_level for r in (treated, raw, bare)] == ["low", "critical", "high"]


def test_the_ranking_sentence_and_order_use_the_banded_score(db_session, make_org, tenant_ctx):
    from app.services.risk_service import ranked_risks

    org = make_org("rank")
    treated = _risk(db_session, org.id, "Treated", residual=(1, 2))
    mid = _risk(db_session, org.id, "Mid", pair=(3, 3))
    raw = _risk(db_session, org.id, "Raw", pair=(2, 2), inherent=(4, 4))
    with tenant_ctx(org.id):
        rows = ranked_risks()["rows"]
    assert [r["risk_id"] for r in rows] == [raw.id, mid.id, treated.id]
    by_id = {r["risk_id"]: r for r in rows}
    assert by_id[raw.id]["score_text"] == "Inherent 16"
    assert "recorded score of 4" not in by_id[raw.id]["reason"]
    assert "inherent score of 16" in by_id[raw.id]["reason"]
    assert "residual score of 2" in by_id[treated.id]["reason"]
    assert by_id[raw.id]["risk_score"] == 16
    assert by_id[treated.id]["risk_score"] == 2


def test_the_assessment_orders_rows_by_the_score_they_show(db_session, make_org, tenant_ctx):
    from app.services.risk_service import risk_assessment

    org = make_org("assess")
    treated = _risk(db_session, org.id, "Treated", residual=(1, 2))
    raw = _risk(db_session, org.id, "Raw untreated")
    with tenant_ctx(org.id):
        rows = risk_assessment()["rows"]
    assert [r["risk_id"] for r in rows] == [raw.id, treated.id]
    assert [r["score_text"] for r in rows] == ["Inherent 10", "Residual 2"]
    assert [r["risk_score"] for r in rows] == [10, 2]


def test_the_assistants_risk_answers_carry_the_banded_score(db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("lens")
    element = _element(db_session, org.id, "Ledger")
    _risk(db_session, org.id, "Treated", residual=(1, 2), element=element)
    with tenant_ctx(org.id):
        (row,) = IntelligenceQueryService.risk_for_element(element.id)["risks"]
    assert row["score_text"] == "Residual 2"
    assert row["risk_score"] == 2
    assert row["risk_level"] == "low"


def test_the_assistant_answer_page_prints_the_labelled_score():
    from pathlib import Path

    text = Path("app/templates/ai_chat/_lens_answer.html").read_text()
    assert "item.score_text" in text
    assert "= {{ item.risk_score }}" not in text


def test_the_heat_map_payload_and_panel_print_the_labelled_score(db_session, make_org, tenant_ctx):
    from pathlib import Path

    from app.services.risk_service import get_heat_map_data

    org = make_org("heat")
    treated = _risk(db_session, org.id, "Treated", residual=(1, 2))
    with tenant_ctx(org.id):
        payload = get_heat_map_data()
    (row,) = [r for r in payload["risks"] if r["id"] == treated.id]
    assert row["score_text"] == "Residual 2"
    assert row["risk_score"] == 2
    panel = Path("app/templates/solutions/risk_heat_map.html").read_text()
    assert "activeRisk.score_text" in panel
    assert "activeRisk.risk_score +" not in panel


def test_the_solution_record_prints_the_banded_score(db_session, make_org):
    from app.models.solution_models import Solution
    from app.modules.solutions_strategic.v2.services.solution_record_service import risks_for
    from tests.test_solution_record import _user

    org = make_org("record")
    author = _user(db_session, org, "solution_architect", "author")
    solution = Solution(name="Ledger renewal", organization_id=org.id, created_by_id=author.id)
    db_session.add(solution)
    db_session.flush()
    treated = _risk(db_session, org.id, "Treated", residual=(1, 2), solution_id=solution.id)
    result = risks_for(solution, [])
    (row,) = [r for r in result["rows"] if r["id"] == treated.id]
    assert row["score"] == 2
    assert row["band"] == "low"
    assert row["score_text"] == "Residual 2"


def test_the_governance_count_of_high_risks_matches_the_register_bands(db_session, make_org, tenant_ctx):
    from app.models.risk import Risk, RiskStatus
    from app.modules.governance.routes.governance_dashboard_routes import high_risk_count

    org = make_org("govcount")
    _risk(db_session, org.id, "Treated", residual=(1, 2))
    _risk(db_session, org.id, "Raw", pair=(2, 2), inherent=(4, 4))
    with tenant_ctx(org.id):
        expected = sum(1 for r in Risk.query.filter(Risk.status == RiskStatus.OPEN).all()
                       if r.risk_level in ("high", "critical"))
        assert high_risk_count() == expected == 1


def test_the_priority_actions_name_the_pair_their_score_comes_from(db_session, make_org):
    from app.services.priority_action_service import _risk_rows

    org = make_org("prio")
    treated = _risk(db_session, org.id, "Treated", residual=(1, 2))
    (row,) = [r for r in _risk_rows(org.id) if r["record_id"] == treated.id]
    assert "Residual 2 of 25" in row["why"]
    assert "likelihood 2 of 5, impact 5 of 5" not in str(row["footer"])
    assert "residual likelihood 1 of 5, impact 2 of 5" in str(row["footer"])

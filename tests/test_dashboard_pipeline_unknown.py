"""The solution pipeline on the Health Scorecard keeps its honesty rules.

The rebuilt Overview no longer draws a solution pipeline card; the one place the
dashboard shows where solutions sit in the ADM cycle is the Health Scorecard
(/dashboard/health), fed by recorded_phase_summary. These tests drive that real
page for two real organisations.
"""
import re

import pytest

from app import db
from app.models.solution_models import Solution
from app.modules.dashboard.v2.services import solution_phase_measures as measures
from tests.test_dashboard_health_integration import _add_solutions
from tests.test_dashboard_health_score_honesty import _org, _user


def page_text(client):
    response = client.get("/dashboard/health")
    assert response.status_code == 200
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", response.get_data(as_text=True)))


def phase_card(client, org, tag, phases, tenant_ctx, login_as, other_phases=()):
    """The text of /dashboard/health for an organisation seeded with the given phases."""
    other = _org(db.session, f"{tag}-other")
    with tenant_ctx(org.id):
        user = _user(org, tag)
        _add_solutions(org, phases)
    with tenant_ctx(other.id):
        _add_solutions(other, other_phases, name="Other organisation solution")
    login_as(client, user)
    return page_text(client)


def row(text, label):
    """The count shown beside a phase label."""
    return re.search(re.escape(label) + r"\S* (\d+) ", text)


@pytest.mark.parametrize("phases", [[None] * 4, [""] * 4, ["obsolete"] * 4])
def test_existing_unclassified_solutions_are_not_an_empty_pipeline(phases, db_session, client, login_as, tenant_ctx):
    org = _org(db_session, "pipeline-unclassified")
    text = phase_card(client, org, "unclassified", phases, tenant_ctx, login_as, other_phases=["A", "A", "A"])
    assert row(text, "Unclassified").group(1) == "4"
    assert row(text, "A: Vision").group(1) == "0"
    assert "Phase data unavailable" not in text


def test_mixed_phase_counts_preserve_unknown_and_known(db_session, client, login_as, tenant_ctx):
    org = _org(db_session, "pipeline-mixed")
    text = phase_card(client, org, "mixed", ["A", "A", None, None, None, "", "C"], tenant_ctx, login_as, other_phases=["A"])
    assert row(text, "A: Vision").group(1) == "2"
    assert row(text, "C: Info Systems").group(1) == "1"
    assert row(text, "Unclassified").group(1) == "4"


def test_normalized_phase_groups_sum_collisions_without_losing_solutions():
    result = measures.summarise_phases(["C", "C", " c ", " c ", " c ", None, "", "obsolete"])
    assert result["counts"]["C"] == 5
    assert result["unclassified"] == 3
    assert result["total"] == 8
    assert sum(result["counts"].values()) + result["unclassified"] == result["total"]


def test_normalized_phase_groups_are_summed_on_the_page(db_session, client, login_as, tenant_ctx):
    org = _org(db_session, "pipeline-normalised")
    text = phase_card(client, org, "normalised", ["C", "C", " c ", " c ", " c ", None, "", "obsolete"], tenant_ctx, login_as)
    assert row(text, "C: Info Systems").group(1) == "5"
    assert row(text, "Unclassified").group(1) == "3"


def test_query_failure_is_unavailable_not_no_solutions(db_session, client, login_as, tenant_ctx, monkeypatch):
    org = _org(db_session, "pipeline-failure")
    with tenant_ctx(org.id):
        user = _user(org, "failure")
        _add_solutions(org, ["A", "B"])
    login_as(client, user)

    def fail():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(measures, "recorded_phase_summary", fail)
    text = page_text(client)
    assert "Phase data unavailable" in text
    assert "Unclassified" not in text
    assert "A: Vision" not in text


def test_genuine_empty_pipeline_remains_empty(db_session, client, login_as, tenant_ctx):
    org = _org(db_session, "pipeline-empty")
    text = phase_card(client, org, "empty", [], tenant_ctx, login_as, other_phases=["A", "B"])
    assert "Phase data unavailable" not in text
    assert row(text, "A: Vision").group(1) == "0"
    assert "Unclassified" not in text

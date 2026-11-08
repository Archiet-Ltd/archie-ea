"""A gap is critical when either its severity or its priority says so, and every count reads that one rule."""

from __future__ import annotations

import re
import uuid
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _gap(db_session, org, **fields):
    from app.models.implementation_migration import Gap

    gap = Gap(organization_id=org.id, name="Gap %s" % uuid.uuid4().hex[:8], gap_type="coverage", **fields)
    db_session.add(gap)
    db_session.flush()
    return gap


def test_a_gap_critical_by_priority_alone_is_counted_and_listed(db_session, make_org):
    from app.services.gap_register_service import count_gaps, list_gaps

    mine, theirs = make_org("rule-a"), make_org("rule-b")
    by_priority = _gap(db_session, mine, priority="critical", severity="medium")
    by_severity = _gap(db_session, mine, priority="high", severity="critical")
    _gap(db_session, mine, priority="high", severity="high")
    _gap(db_session, theirs, priority="critical", severity="critical")

    assert count_gaps(mine.id, severity="critical") == 2
    assert {g.id for g in list_gaps(mine.id, severity="critical")} == {by_priority.id, by_severity.id}
    assert by_priority.rated_severity == "critical"


def test_update_moves_severity_with_priority_both_ways(db_session, make_org):
    from app.services.gap_archimate_service import gap_archimate_service
    from app.services.gap_register_service import count_gaps

    org = make_org("rule-update")
    gap = _gap(db_session, org, priority="medium", severity="medium")

    gap_archimate_service.update_gap(gap.id, {"priority": "critical"})
    db_session.flush()
    assert (gap.priority, gap.severity) == ("critical", "critical")
    assert count_gaps(org.id, severity="critical") == 1

    gap_archimate_service.update_gap(gap.id, {"priority": "low"})
    db_session.flush()
    assert (gap.priority, gap.severity) == ("low", "low")
    assert count_gaps(org.id, severity="critical") == 0


def test_the_gaps_list_page_counts_critical_by_the_rule(db_session, make_org, client, login_as):
    from tests.test_work_package_writer import _user

    org, other = make_org("rule-list"), make_org("rule-list-other")
    user = _user(db_session, org, "rulelist", role="enterprise_architect")
    _gap(db_session, org, priority="critical", severity="low")
    _gap(db_session, org, priority="high", severity="critical")
    _gap(db_session, other, priority="critical", severity="critical")
    login_as(client, user)
    with mock.patch("app.implementation_planning.routes.render_template", return_value="ok") as render:
        client.get("/implementation/gaps")
    assert render.call_args.kwargs["stats"]["critical"] == 2


def test_capability_health_threshold_words_follow_the_rule(db_session, make_org, client, login_as):
    from app.modules.capabilities.services import capability_health_service
    from tests.test_work_package_writer import _user

    org = make_org("rule-health")
    user = _user(db_session, org, "rulehealth", role="enterprise_architect")
    login_as(client, user)
    with mock.patch.object(capability_health_service, "AT_RISK_BELOW", 45):
        html = client.get("/strategic/capability-health").get_data(as_text=True)
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style|template).*?</\1>", "", html, flags=re.S))
    assert "below 45%" in text
    assert "below 60%" not in text

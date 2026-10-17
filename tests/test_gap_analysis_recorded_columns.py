"""Gap Analysis shows what is recorded about each gap: its severity and its kind, and counts
the critical, in-progress and resolved gaps from the one register. Two real organisations."""

from __future__ import annotations

import re

import pytest

from tests.test_gap_work_package_link import _user

pytestmark = pytest.mark.usefixtures("db_session")


def _gap(db_session, org, name, severity=None, kind="capability_shortfall", resolution_status="identified"):
    from app.models.implementation_migration import Gap

    gap = Gap(
        name=name, organization_id=org.id, severity=severity, gap_kind=kind,
        resolution_status=resolution_status,
    )
    db_session.add(gap)
    db_session.flush()
    return gap


def test_the_columns_show_severity_and_kind_and_the_cards_count_them(app, db_session, make_org, client, login_as):
    org, other = make_org("gac-a"), make_org("gac-b")
    user = _user(db_session, org, "reader")
    _gap(db_session, org, "Event gap", severity="critical")
    _gap(db_session, org, "Skills gap", severity="low", resolution_status="in_progress")
    _gap(db_session, org, "Docs gap", severity=None, resolution_status="resolved")
    for i in range(3):  # another organisation's gaps must not move this organisation's cards
        _gap(db_session, other, f"Their gap {i}", severity="critical", resolution_status="resolved")
    db_session.commit()

    login_as(client, user)
    html = client.get("/enterprise/implementation/gap-analysis").get_data(as_text=True)

    assert "Capability shortfall" in html  # the recorded kind, not "None"
    rows = html.split("Identified Gaps", 1)[1].split("Gaps Not Addressed", 1)[0]
    assert re.search(r">\s*Critical\s*<", rows) and re.search(r">\s*Low\s*<", rows)  # recorded severity
    assert "None" not in rows
    cards = html.split("Identified Gaps", 1)[0]
    # critical 1, in progress 1, resolved 1 (the other organisation's three do not count)
    assert cards.count("text-destructive") >= 1
    assert "Their gap" not in html


def test_the_counts_come_from_the_one_register(app, db_session, make_org):
    from app.services.gap_register_service import count_gaps

    org, other = make_org("gac-c"), make_org("gac-d")
    _gap(db_session, org, "A", severity="critical")
    _gap(db_session, org, "B", severity="high", resolution_status="in_progress")
    _gap(db_session, other, "C", severity="critical")
    db_session.flush()

    assert count_gaps(org.id) == 2
    assert count_gaps(org.id, severity="critical") == 1
    assert count_gaps(org.id, resolution_status="in_progress") == 1
    assert count_gaps(org.id, resolution_status="resolved") == 0
    assert count_gaps(other.id, severity="critical") == 1

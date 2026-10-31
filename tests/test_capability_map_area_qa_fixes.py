"""Regression tests for the Capability-map area QA findings (Sep 2026 Fortune-500 walkthrough).

Covers behavioural fixes on the capability map / roadmap / health screens:

* D13 — /enterprise/capability-map/applications reported "Active 0" while every row
  rendered STATUS "Active". ApplicationComponent has no ``status`` column at all, so
  ``app.status`` is always undefined; the status column falls back to "active" and
  shows every row Active, but the KPI counted ``selectattr('status','equalto','active')``
  which matched nothing. The KPI now counts with the SAME rule the column renders with.
* D4/D17 — the three surfaces that used to all say "Gaps" now carry distinct labels,
  because they count genuinely different things (detected capability-gap signals vs
  health coverage/maturity signals vs the persisted ArchiMate Gap register).

Written against the shared fixtures in ``tests/conftest.py`` (``db_session`` rolls the
whole test back), per the project convention.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"capmap-qa-{uuid.uuid4().hex[:10]}@example.test",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_app(db_session, org_id, name, **kwargs):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org_id, **kwargs)
    db_session.add(row)
    db_session.flush()
    return row


def _active_kpi_value(html: str) -> int:
    """Extract the numeric value of the 'Active' metrics card from rendered HTML."""
    # metrics_card renders: <p ...>Active</p> ... <div data-slot="card-title" ...> VALUE </div>
    m = re.search(
        r'>\s*Active\s*</p>.*?data-slot="card-title"[^>]*>\s*(\d+)',
        html,
        re.DOTALL,
    )
    assert m, "Could not find the 'Active' KPI card in the rendered page"
    return int(m.group(1))


def test_applications_active_kpi_matches_rendered_rows(db_session, make_org, client, login_as):
    """The 'Active' KPI must agree with the STATUS column, which shows every row Active."""
    org = make_org("d13")
    user = _make_user(db_session, org.id)
    # ApplicationComponent has no `status` column: every row renders "Active"
    # via the (app.status or 'active') fallback. The KPI must therefore count all.
    for i in range(3):
        _make_app(db_session, org.id, f"D13 App {i}")

    login_as(client, user)
    resp = client.get("/enterprise/capability-map/applications")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    html = resp.get_data(as_text=True)

    active = _active_kpi_value(html)
    # Regression guard: the old selectattr('status','equalto','active') returned 0
    # while all three rows displayed Active. The count now agrees with the display.
    assert active == 3, f"Active KPI was {active}, expected 3 (all rows render Active)"


def test_gap_surfaces_use_distinct_labels():
    """The three gap surfaces count different things and must not all read 'Total Gaps'."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "app" / "templates"
    roadmap = (root / "capability_roadmap" / "capability_roadmap.html").read_text(encoding="utf-8")
    health = (root / "strategic" / "capability_health.html").read_text(encoding="utf-8")
    register = (root / "enterprise" / "gap_analysis.html").read_text(encoding="utf-8")

    assert "Capability Gaps Detected" in roadmap
    assert "Capability Gap Signals" in health
    assert "Recorded gaps" in register
    # None of the three should reuse the bare ambiguous "Total Gaps" label.
    assert "Total Gaps" not in roadmap
    assert "Total Gaps" not in register

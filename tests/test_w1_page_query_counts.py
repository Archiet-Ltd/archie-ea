"""Wave 1 pages must not ask the database once per row (T-perf01).

Each test seeds enough rows that a query per row is plain in the count, then asserts the count does not grow
with the rows. The numbers come from the measured run on a large seeded organisation: the overview ran 2,133
statements, the applications list 616 and the health scorecard 554, all one-per-row repeats of one query.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import event

pytestmark = pytest.mark.usefixtures("db_session")

ROWS = 30


class Statements:
    """Counts the statements one block runs, optionally only those containing ``needle``."""

    def __init__(self, needle=None):
        self.needle = needle
        self.sql = []

    def __enter__(self):
        from app import db

        self.engine = db.session.get_bind().engine if hasattr(db.session.get_bind(), "engine") else db.engine
        event.listen(self.engine, "before_cursor_execute", self._hook)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hook)

    def _hook(self, conn, cursor, statement, parameters, context, executemany):
        text = re.sub(r"\s+", " ", statement)
        if self.needle is None or self.needle in text:
            self.sql.append(text)

    @property
    def count(self):
        return len(self.sql)


def _applications(db_session, org_id, count=ROWS, with_cost=True):
    from app.models.application_portfolio import ApplicationComponent

    rows = []
    for i in range(count):
        row = ApplicationComponent(
            name="Perf app %s %d" % (uuid.uuid4().hex[:6], i), organization_id=org_id,
            lifecycle_status="2.1 strategic", component_type="Application Component",
            total_cost_of_ownership=(1000.0 * (i + 1)) if with_cost else None,
        )
        db_session.add(row)
        rows.append(row)
    db_session.flush()
    return rows


def test_overview_table_reads_the_currency_once_for_the_rows_it_shows(db_session, make_org, tenant_ctx):
    """A person with a large estate opens the overview: formatting costs must not run per application."""
    from app.modules.dashboard.services import overview_figures

    org = make_org("perf-overview")
    _applications(db_session, org.id)
    with tenant_ctx(org.id), Statements("organizations.reporting_currency") as currency_reads:
        table = overview_figures._table(org.id, with_cost=True)
    assert table["total"] == ROWS
    assert len(table["rows"]) == 10
    assert all(row["cost_label"] for row in table["rows"]), "every row shown still carries its formatted cost"
    assert currency_reads.count <= 10, "currency read %d times for %d applications" % (currency_reads.count, ROWS)


def test_capability_coverage_asks_one_question_for_all_top_level_capabilities(db_session, make_org, tenant_ctx):
    """The health scorecard's coverage figure: same answer as before, a fixed number of queries."""
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.business_capability import BusinessCapability
    from app.modules.dashboard.v2.services.capability_coverage import compute_l1_capability_coverage

    org = make_org("perf-coverage")
    apps = _applications(db_session, org.id, count=3)
    tops = []
    for i in range(ROWS):
        top = BusinessCapability(name="Top %d" % i, level=1, organization_id=org.id)
        db_session.add(top)
        tops.append(top)
    db_session.flush()
    child = BusinessCapability(name="Child", level=2, parent_capability_id=tops[0].id, organization_id=org.id)
    db_session.add(child)
    db_session.flush()
    # one top-level capability is covered through its sub-capability, one directly; the rest are not
    db_session.add(ApplicationCapabilityMapping(application_component_id=apps[0].id, business_capability_id=child.id,
                                                organization_id=org.id))
    db_session.add(ApplicationCapabilityMapping(application_component_id=apps[1].id, business_capability_id=tops[1].id,
                                                organization_id=org.id))
    db_session.commit()
    with tenant_ctx(org.id), Statements("FROM application_capability_mapping") as reads:
        result = compute_l1_capability_coverage()
    assert result["total"] == ROWS
    assert result["covered"] == 2
    assert result["percentage"] == round(2 / ROWS * 100, 1)
    assert reads.count <= 2, "%d mapping reads for %d top-level capabilities" % (reads.count, ROWS)


def test_applications_list_does_not_ask_permissions_or_owner_counts_per_row(db_session, make_org, login_as, app):
    """A CTO opens the applications list: the page's statements do not grow with the rows listed."""
    from app.models.user import Role, User

    org = make_org("perf-list")
    _applications(db_session, org.id)
    role = Role.query.filter_by(name="Architect").first() or Role(name="Architect")
    db_session.add(role)
    user = User(email="perf-list-%s@example.com" % uuid.uuid4().hex[:8], first_name="Perf", last_name="List",
                organization_id=org.id, confirmed=True, enterprise_role="cto")
    user.role = role
    db_session.add(user)
    db_session.commit()
    client = app.test_client()
    login_as(client, user)
    client.get("/applications/")  # first request settles one-off lookups
    login_as(client, user)
    with Statements() as page:
        response = client.get("/applications/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert body.count("Perf app ") >= ROWS, "the listed applications are on the page"
    assert page.count <= 200, "%d statements for a page of %d applications" % (page.count, ROWS)
    per_row = [s for s in page.sql if "FROM org_roles" in s or "FROM permission_grants" in s]
    assert len(per_row) <= 20, "%d permission lookups for %d rows" % (len(per_row), ROWS)


def test_overview_activity_is_counted_by_the_database_not_read_row_by_row(db_session, make_org, tenant_ctx):
    """A busy estate has a long audit trail; the overview's activity figures come from counts, not from every row."""
    from datetime import datetime

    from app.models.audit_log import AuditLog
    from app.modules.dashboard.services import overview_figures

    org = make_org("perf-activity")
    now = datetime(2026, 10, 30, 12, 0, 0)
    for i in range(300):
        db_session.add(AuditLog(
            organization_id=org.id, action="create", table_name="archimate_elements", record_id=1000 + i,
            new_value={"name": "Element %d" % i, "source_kind": "connector" if i < 100 else "manual"},
            created_at=now,
        ))
    for i in range(20):
        db_session.add(AuditLog(
            organization_id=org.id, action="update", table_name="archimate_elements", record_id=1000 + i,
            new_value={"name": "Element %d" % i}, created_at=now,
        ))
    db_session.commit()
    with tenant_ctx(org.id), Statements("soc2_audit_log") as reads:
        history = overview_figures._history(org.id, now)
        activity = overview_figures._activity(history, now, org.id)
    seven = activity["ranges"]["7d"]
    assert (seven["connected"], seven["manual"], seven["total"]) == (100, 200, 300)
    assert activity["week"] == {"rows": activity["week"]["rows"], "total": 320, "added": 300, "updated": 20, "removed": 0}
    assert activity["this_year"] == 300
    assert len(activity["recent"]) == 5
    unbounded = [q[:80] for q in reads.sql if "GROUP BY" not in q and "LIMIT" not in q]
    assert not unbounded, "audit rows read in full: %s" % unbounded

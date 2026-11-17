"""The Risk Register table's own tiles and per-row badges must be accurate.

Both from live production checks on 6 Sep 2026:

- E2E-N: the CRITICAL tile counted risk_level alone with no status filter,
  so a risk that had already been closed still inflated the count a viewer
  reads as current exposure (TOTAL 1, OPEN 0, CRITICAL 1 - confirmed live).
- F6: a risk with risk_level "high" or "medium" was reported to render an
  empty Level cell, with every seed risk happening to be critical/low so it
  only surfaced once a user created a high/medium risk in normal use.
"""
from bs4 import BeautifulSoup
from flask import render_template


def _make_risk(organization_id, title, likelihood, impact, status="open"):
    from app.models.risk import Risk, RiskStatus

    return Risk(
        organization_id=organization_id, title=title,
        likelihood=likelihood, impact=impact,
        status=RiskStatus[status.upper()],
    )


def test_critical_tile_excludes_closed_and_mitigated_risks(app, db_session, make_org, tenant_ctx):
    org = make_org("risk-register-tiles")
    with tenant_ctx(org.id):
        db_session.add_all([
            _make_risk(org.id, "Closed critical (must not count)", 5, 5, status="closed"),
            _make_risk(org.id, "Open critical (must count)", 5, 5, status="open"),
            _make_risk(org.id, "Mitigated critical (must not count)", 5, 5, status="mitigated"),
        ])
        db_session.flush()

        from app.models.risk import Risk
        from app.modules.architecture.routes.risk_routes import serialize_risk_row
        from app.services import risk_service
        risks = Risk.query.order_by(Risk.id).all()
        with app.test_request_context("/"):
            html = render_template(
                "governance/risk_register.html", risks=risks,
                risk_rows=[serialize_risk_row(r) for r in risks], grid=[],
                total=len(risks), current_sort="id", current_dir="asc",
                raid_items=[], raid_kinds=[], programmes=[],
                counts=risk_service.level_counts(),
            )
        page = BeautifulSoup(html, "html.parser")
        tiles = page.select(".text-2xl.font-semibold.tabular-nums")
        # Order in the template: Total, Open, Critical, Mitigated.
        values = [t.get_text(strip=True) for t in tiles]
        assert values[0] == "3"   # Total
        assert values[1] == "1"   # Open
        assert values[2] == "1"   # Critical: only the open one
        assert values[3] == "1"   # Mitigated


def test_high_and_medium_risks_render_a_level_badge(app, db_session, make_org, tenant_ctx):
    # The table is now the shared data_table component (components/data_table.html):
    # rows are Alpine x-for over a RISK_ROWS JSON payload, not server-rendered
    # <td> text, so a plain Jinja render can no longer see per-row cell text
    # directly (a live Playwright check earlier this session confirmed the
    # actual browser output renders "High"/"Medium"/"Critical" badges
    # correctly). What a server-render test CAN and must still verify: the
    # risk_level each row actually carries, that it reaches the page as
    # RISK_ROWS, and that the shared component still maps 'high'/'medium' to
    # a real (non-empty) badge class rather than silently falling through.
    org = make_org("risk-register-levels")
    with tenant_ctx(org.id):
        db_session.add_all([
            _make_risk(org.id, "High risk", 3, 3),    # score 9 -> high
            _make_risk(org.id, "Medium risk", 2, 3),  # score 6 -> medium
        ])
        db_session.flush()

        from app.models.risk import Risk
        from app.modules.architecture.routes.risk_routes import serialize_risk_row
        risks = Risk.query.order_by(Risk.id).all()
        rows = [serialize_risk_row(r) for r in risks]
        levels = {row["title"]: row["risk_level"] for row in rows}
        assert levels["High risk"] == "high"
        assert levels["Medium risk"] == "medium"

        with app.test_request_context("/"):
            html = render_template(
                "governance/risk_register.html", risks=risks, risk_rows=rows,
                grid=[], total=len(risks), current_sort="id", current_dir="asc",
                raid_items=[], raid_kinds=[], programmes=[],
                counts={"open": 2, "critical": 0, "high": 1, "medium": 1, "low": 0},
            )
        assert '"risk_level": "high"' in html or '"risk_level":"high"' in html
        assert '"risk_level": "medium"' in html or '"risk_level":"medium"' in html
        assert "row['risk_level'] === 'high'" in html
        assert "row['risk_level'] === 'medium'" in html


def test_open_and_critical_tiles_show_the_figures_the_route_passes_in(app, db_session, make_org, tenant_ctx):
    # The assessment reads risk_service.level_counts; the register's tiles must show that same
    # answer rather than counting the list again, so a figure handed in is the figure shown.
    org = make_org("risk-register-one-count")
    with tenant_ctx(org.id):
        db_session.add(_make_risk(org.id, "One open critical", 5, 5))
        db_session.flush()

        from app.models.risk import Risk
        from app.modules.architecture.routes.risk_routes import serialize_risk_row
        risks = Risk.query.order_by(Risk.id).all()
        with app.test_request_context("/"):
            html = render_template(
                "governance/risk_register.html", risks=risks,
                risk_rows=[serialize_risk_row(r) for r in risks], grid=[],
                total=len(risks), current_sort="id", current_dir="asc",
                raid_items=[], raid_kinds=[], programmes=[],
                counts={"open": 7, "critical": 3, "high": 2, "medium": 2, "low": 0},
            )
        page = BeautifulSoup(html, "html.parser")
        assert page.select_one('[data-testid="register-open"]').get_text(strip=True) == "7"
        assert page.select_one('[data-testid="register-critical"]').get_text(strip=True) == "3"

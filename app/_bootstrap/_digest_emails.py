"""
PLT-009 / PLT-031: Weekly digest email functions.

These are called by APScheduler jobs (extensions.py) and CLI commands (cli.py).
Emails degrade gracefully: if SMTP is not configured, content is logged instead.
"""

import logging
from datetime import datetime, timedelta
from html import escape

logger = logging.getLogger(__name__)


def _safe_send_email(app, subject, recipients, html_body):
    """Send an email via Flask-Mail, falling back to logging if SMTP is unavailable.

    Returns True if sent, False if logged only.
    """
    if not recipients:
        logger.info("No recipients for '%s' — skipping.", subject)
        return False

    mail_server = app.config.get("MAIL_SERVER")
    mail_username = app.config.get("MAIL_USERNAME")
    if not mail_server or not mail_username:
        logger.warning(
            "SMTP not configured (MAIL_SERVER=%s) — logging email instead.",
            mail_server,
        )
        logger.info(
            "EMAIL [%s] to %s:\n%s",
            subject,
            ", ".join(recipients),
            html_body[:2000],
        )
        return False

    try:
        from flask_mail import Message
        from app.extensions import mail

        prefix = app.config.get("EMAIL_SUBJECT_PREFIX", "[Entelim]")
        sender = app.config.get("EMAIL_SENDER", mail_username)
        msg = Message(
            subject=f"{prefix} {subject}",
            sender=sender,
            recipients=recipients,
        )
        msg.html = html_body
        mail.send(msg)
        logger.info("Sent '%s' to %d recipients.", subject, len(recipients))
        return True
    except Exception as exc:
        logger.error("Failed to send '%s': %s — logging content instead.", subject, exc)
        logger.info(
            "EMAIL [%s] to %s:\n%s",
            subject,
            ", ".join(recipients),
            html_body[:2000],
        )
        return False


def _get_recipients_by_roles(role_list, organization_id):
    """Email addresses for users in ONE organisation holding one of these roles.

    `organization_id` is required, not defaulted. User is not a TenantMixin
    model, so no listener will scope this query -- and these callers run in a
    scheduler job with no request context, where nothing is scoped anyway. A
    default would mean every user in every organisation, which is precisely the
    defect this argument exists to close.
    """
    from app.models import User

    if organization_id is None:
        raise ValueError(
            "_get_recipients_by_roles requires an organization_id; a global "
            "recipient list would address one tenant's digest to another's users"
        )

    users = User.query.filter(
        User.organization_id == organization_id,
        User.enterprise_role.in_(role_list),
        User.confirmed.is_(True),
    ).all()
    return [u.email for u in users if u.email]


# ---------------------------------------------------------------------------
# PLT-009: Data Maturity Digest
# ---------------------------------------------------------------------------


def _compute_maturity_data():
    """Compute portfolio-wide completeness metrics for the digest."""
    from app.models.solution_models import Solution

    solutions = Solution.query.all()
    if not solutions:
        return {
            "total": 0,
            "avg_score": 0,
            "zero_connections": [],
            "top_incomplete": [],
            "junction_fill_rates": {},
        }

    scores = []
    zero_connections = []
    all_junction_filled = {}
    all_junction_total = {}

    for sol in solutions:
        try:
            cs = sol.architecture_completeness_score
            score = cs["score"]
            scores.append({"name": sol.name, "id": sol.id, "score": score})
            if score == 0:
                zero_connections.append({"name": sol.name, "id": sol.id})
            for jname in cs.get("filled", []):
                all_junction_filled[jname] = all_junction_filled.get(jname, 0) + 1
            for jname in cs.get("filled", []) + cs.get("missing", []):
                all_junction_total[jname] = all_junction_total.get(jname, 0) + 1
        except Exception:
            logger.debug("Could not compute score for solution %s", sol.id)

    total = len(scores)
    avg_score = round(sum(s["score"] for s in scores) / total) if total else 0
    top_incomplete = sorted(scores, key=lambda s: s["score"])[:10]

    junction_fill_rates = {}
    for jname, jtotal in all_junction_total.items():
        filled = all_junction_filled.get(jname, 0)
        junction_fill_rates[jname] = round(filled / jtotal * 100) if jtotal else 0

    return {
        "total": total,
        "avg_score": avg_score,
        "zero_connections": zero_connections,
        "top_incomplete": top_incomplete,
        "junction_fill_rates": junction_fill_rates,
    }


def _render_maturity_digest_html(data):
    """Build HTML email body for the data maturity digest."""
    now = datetime.utcnow().strftime("%Y-%m-%d")

    # Solution names are architect-authored freeform text, not system-generated
    # -- this is exactly the class of value that must be escaped before it
    # reaches an HTML email (see the 10 Sep 2026 incident this file's history
    # now carries in _render_error_digest_html's docstring).
    zero_rows = ""
    for s in data["zero_connections"][:20]:
        zero_rows += f'<tr><td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">{escape(s["name"])}</td></tr>\n'
    if not zero_rows:
        zero_rows = '<tr><td style="padding:4px 8px;color:#16a34a">All solutions have at least one connection.</td></tr>'

    incomplete_rows = ""
    for s in data["top_incomplete"][:10]:
        color = "#dc2626" if s["score"] < 25 else "#d97706" if s["score"] < 50 else "#2563eb"
        incomplete_rows += (
            f'<tr><td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">{escape(s["name"])}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;color:{color};font-weight:600">'
            f'{s["score"]}%</td></tr>\n'
        )

    junction_rows = ""
    for jname, rate in sorted(data["junction_fill_rates"].items(), key=lambda x: x[1]):
        bar_color = "#dc2626" if rate < 25 else "#d97706" if rate < 50 else "#2563eb" if rate < 75 else "#16a34a"
        junction_rows += (
            f'<tr><td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">{escape(jname.replace("_", " ").title())}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">'
            f'<div style="background:#e5e7eb;border-radius:4px;height:16px;width:120px;display:inline-block">'
            f'<div style="background:{bar_color};border-radius:4px;height:16px;width:{int(rate * 1.2)}px"></div>'
            f'</div> {rate}%</td></tr>\n'
        )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1f2937;max-width:640px;margin:0 auto;padding:16px">
<h2 style="color:#111827;border-bottom:2px solid #2563eb;padding-bottom:8px">
    Data Maturity Digest &mdash; {now}
</h2>

<table style="width:100%;margin-bottom:24px">
<tr>
  <td style="background:#eff6ff;padding:16px;border-radius:8px;text-align:center;width:50%">
    <div style="font-size:32px;font-weight:700;color:#2563eb">{escape(str(data['total']))}</div>
    <div style="font-size:13px;color:#6b7280">Total Solutions</div>
  </td>
  <td style="width:16px"></td>
  <td style="background:#f0fdf4;padding:16px;border-radius:8px;text-align:center;width:50%">
    <div style="font-size:32px;font-weight:700;color:#16a34a">{data['avg_score']}%</div>
    <div style="font-size:13px;color:#6b7280">Avg Completeness</div>
  </td>
</tr>
</table>

<h3 style="color:#dc2626">Solutions With Zero Connections ({len(data['zero_connections'])})</h3>
<table style="width:100%;border-collapse:collapse;margin-bottom:24px">
{zero_rows}
</table>

<h3 style="color:#d97706">Top 10 Least Complete Solutions</h3>
<table style="width:100%;border-collapse:collapse;margin-bottom:24px">
<tr><th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Solution</th>
    <th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Score</th></tr>
{incomplete_rows}
</table>

<h3 style="color:#2563eb">Junction Fill Rates (Portfolio-wide)</h3>
<table style="width:100%;border-collapse:collapse;margin-bottom:24px">
<tr><th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Junction</th>
    <th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Fill Rate</th></tr>
{junction_rows}
</table>

<p style="font-size:12px;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:8px">
    This is an automated weekly digest from Entelim. To unsubscribe, update your notification
    preferences in your profile settings.
</p>
</body>
</html>"""


def send_data_maturity_digest(app):
    """PLT-009: send ONE data maturity digest per organisation.

    Previously one email was composed from every tenant's solutions and
    addressed to every matching user in every tenant. The job runs under
    app_context and not a request, so no tenant listener applied and nothing
    was scoped. Now the harness runs the body once per organisation with
    g.current_org_id set, which scopes the TenantMixin models the digest reads
    (Solution, ARBReviewItem, SolutionRisk) -- and the recipient query is
    scoped explicitly, because User is not a TenantMixin model.
    """
    from app.jobs.tenant_safe_job import run_for_each_tenant

    def _digest_one_tenant(organization_id: int) -> dict:
        data = _compute_maturity_data()
        recipients = _get_recipients_by_roles(
            ["enterprise_architect", "portfolio_manager", "platform_admin"],
            organization_id,
        )
        if not recipients:
            # No one in this tenant holds a recipient role. Say so; an empty
            # send is not the same as a successful one.
            logger.info("PLT-009: org %s has no digest recipients", organization_id)
            return {"organization_id": organization_id, "recipients": 0}
        html = _render_maturity_digest_html(data)
        _safe_send_email(app, "Weekly Data Maturity Digest", recipients, html)
        logger.info(
            "PLT-009: org %s — %d solutions, %d%% avg score, %d recipients.",
            organization_id, data["total"], data["avg_score"], len(recipients),
        )
        return {"organization_id": organization_id, "recipients": len(recipients)}

    return run_for_each_tenant(app, "data-maturity-digest", _digest_one_tenant)


# ---------------------------------------------------------------------------
# PLT-031: Executive Summary
# ---------------------------------------------------------------------------


def _compute_executive_data():
    """Compute portfolio stats for the executive summary email."""
    from app.models.solution_models import Solution
    from app.models.solution_lifecycle_models import SolutionRisk

    one_week_ago = datetime.utcnow() - timedelta(days=7)

    # New solutions this week
    new_solutions = Solution.query.filter(
        Solution.created_at >= one_week_ago
    ).all()

    # All solutions for completeness
    all_solutions = Solution.query.all()
    scores = []
    for sol in all_solutions:
        try:
            cs = sol.architecture_completeness_score
            scores.append(cs["score"])
        except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
            continue

    avg_score = round(sum(scores) / len(scores)) if scores else 0

    # ARB decisions this week
    arb_decisions = []
    try:
        from app.models.architecture_review_board import ARBReviewItem
        decided = ARBReviewItem.query.filter(
            ARBReviewItem.decision_date >= one_week_ago,
            ARBReviewItem.decision.isnot(None),
        ).all()
        for item in decided:
            arb_decisions.append({
                "title": item.title,
                "decision": item.decision,
                "review_number": item.review_number,
            })
    except Exception as exc:
        logger.debug("Could not query ARB decisions: %s", exc)

    # New risks flagged this week
    new_risks = []
    try:
        risks = SolutionRisk.query.filter(
            SolutionRisk.created_at >= one_week_ago
        ).order_by(SolutionRisk.impact.desc()).limit(5).all()
        for r in risks:
            sol = Solution.query.get(r.solution_id)
            new_risks.append({
                "description": (r.risk_description or "")[:120],
                "impact": r.impact,
                "solution_name": sol.name if sol else "Unknown",
            })
    except Exception as exc:
        logger.debug("Could not query new risks: %s", exc)

    # Phase distribution
    phase_counts = {}
    for sol in all_solutions:
        p = sol.adm_phase or "A"
        phase_counts[p] = phase_counts.get(p, 0) + 1

    return {
        "total_solutions": len(all_solutions),
        "new_solutions": [{"name": s.name, "id": s.id} for s in new_solutions],
        "new_solutions_count": len(new_solutions),
        "avg_completeness": avg_score,
        "arb_decisions": arb_decisions,
        "arb_decisions_count": len(arb_decisions),
        "new_risks": new_risks,
        "phase_counts": phase_counts,
        "week_ending": datetime.utcnow().strftime("%Y-%m-%d"),
    }


def _render_executive_summary_html(data):
    """Build HTML email body for the executive summary."""
    week_ending = data["week_ending"]

    # Solution names, ARB titles/review numbers, and risk descriptions are all
    # user-authored freeform text -- escape() every one of them before they
    # reach this HTML email (see the 10 Sep 2026 incident referenced in
    # _render_error_digest_html's docstring; this file had the same bug in
    # two more places, found only once someone went looking).
    new_sol_rows = ""
    for s in data["new_solutions"][:10]:
        new_sol_rows += f'<tr><td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">{escape(s["name"])}</td></tr>\n'
    if not new_sol_rows:
        new_sol_rows = '<tr><td style="padding:4px 8px;color:#6b7280;font-style:italic">No new solutions this week.</td></tr>'

    # ARB decisions table
    arb_rows = ""
    for d in data["arb_decisions"][:10]:
        badge_color = "#16a34a" if "approved" in (d["decision"] or "") else "#dc2626" if d["decision"] == "rejected" else "#d97706"
        arb_rows += (
            f'<tr><td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">{escape(str(d["review_number"]))}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">{escape(d["title"][:60])}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;color:{badge_color};font-weight:600">'
            f'{escape((d["decision"] or "pending").replace("_", " ").title())}</td></tr>\n'
        )
    if not arb_rows:
        arb_rows = '<tr><td colspan="3" style="padding:4px 8px;color:#6b7280;font-style:italic">No ARB decisions this week.</td></tr>'

    # Risks table
    risk_rows = ""
    for r in data["new_risks"]:
        impact_color = "#dc2626" if r["impact"] in ("critical", "high") else "#d97706"
        risk_rows += (
            f'<tr><td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">{escape(r["solution_name"])}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">{escape(r["description"])}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;color:{impact_color};font-weight:600">'
            f'{escape(r["impact"].title())}</td></tr>\n'
        )
    if not risk_rows:
        risk_rows = '<tr><td colspan="3" style="padding:4px 8px;color:#6b7280;font-style:italic">No new risks flagged this week.</td></tr>'

    # Phase distribution
    phase_rows = ""
    phase_labels = {
        "A": "Architecture Vision",
        "B": "Business Architecture",
        "C": "Information Systems",
        "D": "Technology Architecture",
        "E": "Opportunities & Solutions",
        "F": "Migration Planning",
        "G": "Implementation Governance",
        "H": "Architecture Change Mgmt",
    }
    for phase in ["A", "B", "C", "D", "E", "F", "G", "H"]:
        count = data["phase_counts"].get(phase, 0)
        if count > 0:
            phase_rows += (
                f'<tr><td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">'
                f'Phase {phase} &mdash; {phase_labels.get(phase, phase)}</td>'
                f'<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;font-weight:600">{escape(str(count))}</td></tr>\n'
            )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1f2937;max-width:640px;margin:0 auto;padding:16px">
<h2 style="color:#111827;border-bottom:2px solid #7c3aed;padding-bottom:8px">
    Executive Architecture Summary &mdash; Week Ending {week_ending}
</h2>

<table style="width:100%;margin-bottom:24px">
<tr>
  <td style="background:#eff6ff;padding:12px;border-radius:8px;text-align:center;width:25%">
    <div style="font-size:28px;font-weight:700;color:#2563eb">{escape(str(data['total_solutions']))}</div>
    <div style="font-size:11px;color:#6b7280">Total Solutions</div>
  </td>
  <td style="width:8px"></td>
  <td style="background:#f0fdf4;padding:12px;border-radius:8px;text-align:center;width:25%">
    <div style="font-size:28px;font-weight:700;color:#16a34a">{escape(str(data['new_solutions_count']))}</div>
    <div style="font-size:11px;color:#6b7280">New This Week</div>
  </td>
  <td style="width:8px"></td>
  <td style="background:#faf5ff;padding:12px;border-radius:8px;text-align:center;width:25%">
    <div style="font-size:28px;font-weight:700;color:#7c3aed">{data['avg_completeness']}%</div>
    <div style="font-size:11px;color:#6b7280">Avg Completeness</div>
  </td>
  <td style="width:8px"></td>
  <td style="background:#fef2f2;padding:12px;border-radius:8px;text-align:center;width:25%">
    <div style="font-size:28px;font-weight:700;color:#dc2626">{escape(str(data['arb_decisions_count']))}</div>
    <div style="font-size:11px;color:#6b7280">ARB Decisions</div>
  </td>
</tr>
</table>

<h3 style="color:#2563eb">New Solutions Submitted ({data['new_solutions_count']})</h3>
<table style="width:100%;border-collapse:collapse;margin-bottom:24px">
{new_sol_rows}
</table>

<h3 style="color:#7c3aed">ARB Decisions This Week</h3>
<table style="width:100%;border-collapse:collapse;margin-bottom:24px">
<tr><th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Review #</th>
    <th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Title</th>
    <th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Decision</th></tr>
{arb_rows}
</table>

<h3 style="color:#dc2626">Top Risks Flagged This Week</h3>
<table style="width:100%;border-collapse:collapse;margin-bottom:24px">
<tr><th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Solution</th>
    <th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Risk</th>
    <th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Impact</th></tr>
{risk_rows}
</table>

<h3 style="color:#374151">Portfolio by TOGAF Phase</h3>
<table style="width:100%;border-collapse:collapse;margin-bottom:24px">
<tr><th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Phase</th>
    <th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Solutions</th></tr>
{phase_rows}
</table>

<p style="font-size:12px;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:8px">
    This is an automated weekly summary from Entelim. Sent to platform administrators.
</p>
</body>
</html>"""


def send_executive_summary(app):
    """PLT-031: send ONE executive summary per organisation.

    Same defect and same fix as the maturity digest above. platform_admin is a
    per-organisation role, so a global summary told each tenant's admin about
    every other tenant's portfolio.
    """
    from app.jobs.tenant_safe_job import run_for_each_tenant

    def _summary_one_tenant(organization_id: int) -> dict:
        data = _compute_executive_data()
        recipients = _get_recipients_by_roles(["platform_admin"], organization_id)
        if not recipients:
            logger.info("PLT-031: org %s has no summary recipients", organization_id)
            return {"organization_id": organization_id, "recipients": 0}
        html = _render_executive_summary_html(data)
        _safe_send_email(app, "Weekly Executive Architecture Summary", recipients, html)
        logger.info(
            "PLT-031: org %s — %d solutions, %d new, %d ARB decisions, %d recipients.",
            organization_id, data["total_solutions"], data["new_solutions_count"],
            data["arb_decisions_count"], len(recipients),
        )
        return {"organization_id": organization_id, "recipients": len(recipients)}

    return run_for_each_tenant(app, "executive-summary", _summary_one_tenant)


# ---------------------------------------------------------------------------
# Error digest (10 Sep 2026): read-only notification for /admin/errors.
#
# Deliberately NOT run_for_each_tenant -- ErrorEvent is cross-tenant by
# design (see app/models/error_event.py's docstring), same as the
# /admin/errors page itself, so recipients are every platform_admin across
# every organisation, queried once, not per-tenant.
#
# This is a READ-ONLY digest: it summarises new unresolved errors and emails
# them to humans. It does not, and must not, write any fix -- see the
# discussion in-session about autonomous production changes needing a human
# in the loop; this is the human-in-the-loop half, not a step toward removing
# the human.
# ---------------------------------------------------------------------------

_ERROR_DIGEST_WATERMARK_KEY = "error_digest_last_run_at"


def _get_platform_admin_recipients():
    """Every confirmed platform_admin, across every organisation.

    Deliberately global and unscoped -- error_events itself carries no
    organisation predicate (an error is a platform fact, not a tenant one),
    so there is no single organisation_id to scope this by. Matches the
    /admin/errors route's own tenant-scoping-ok justification.
    """
    from app.models import User

    users = User.query.filter(  # tenant-scoping-ok: platform-wide recipient list for platform-wide data, matching /admin/errors itself
        User.is_platform_admin.is_(True),
        User.confirmed.is_(True),
    ).all()
    return sorted({u.email for u in users if u.email})


def _get_watermark():
    from app.models.system_setting import SystemSetting

    row = SystemSetting.query.get(_ERROR_DIGEST_WATERMARK_KEY)
    if not row or not row.value:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


def _set_watermark(when):
    from app import db
    from app.models.system_setting import SystemSetting

    row = SystemSetting.query.get(_ERROR_DIGEST_WATERMARK_KEY)
    if row is None:
        row = SystemSetting(key=_ERROR_DIGEST_WATERMARK_KEY)
        db.session.add(row)
    row.value = when.isoformat()
    db.session.commit()


def _render_error_digest_html(events, since):
    since_label = since.strftime("%Y-%m-%d %H:%M UTC") if since else "the beginning"
    rows = ""
    for e in events[:25]:
        color = "#dc2626" if e.source == "server" else "#d97706"
        # This is a raw Python f-string, not a Jinja template -- Jinja
        # autoescapes on /admin/errors, but nothing does here by default, so
        # message/location (server log text, or attacker-controlled content
        # via the unauthenticated /api/client-error sink) must be escaped by
        # hand or it lands as live HTML in whatever renders this email.
        #
        # escape() is called INLINE at each interpolation site, not via a
        # pre-computed variable two lines above -- a prior version did that,
        # and check_raw_html_escaping.py's own escape hatch
        # ("already escape()'d two lines above") could not tell the
        # difference between that being true and someone later deleting the
        # upstream call, which would have silently reintroduced this exact
        # bug with the gate still green. Confirmed by deliberately
        # reintroducing the original bug and re-running the gate: it passed
        # right through the stale marker. Inlining the call removes the
        # class of gap entirely -- there is nothing left to trust.
        rows += (
            f'<tr><td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;color:{color};'
            f'font-weight:600">{escape(e.source)}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">{escape((e.message or "")[:160])}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">{escape(e.location) if e.location else "—"}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;text-align:right">{escape(str(e.occurrence_count))}</td></tr>\n'
        )
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1f2937;max-width:640px;margin:0 auto;padding:16px">
<h2 style="color:#111827;border-bottom:2px solid #dc2626;padding-bottom:8px">
    New Platform Errors Since {since_label}
</h2>
<p style="color:#6b7280;font-size:13px">
    {len(events)} new unresolved error{"s" if len(events) != 1 else ""} recorded. This is a
    read-only notification -- nothing has been changed automatically. Review and resolve at
    /admin/errors.
</p>
<table style="width:100%;border-collapse:collapse;margin-bottom:24px">
<tr><th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Source</th>
    <th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Message</th>
    <th style="text-align:left;padding:4px 8px;border-bottom:2px solid #e5e7eb">Location</th>
    <th style="text-align:right;padding:4px 8px;border-bottom:2px solid #e5e7eb">Count</th></tr>
{rows}
</table>
<p style="font-size:12px;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:8px">
    Automated error digest from Entelim. Sent to platform administrators only.
</p>
</body>
</html>"""


def send_error_digest(app):
    """Email platform admins a summary of unresolved errors seen since the
    last run. No-ops (and does not advance the watermark) when there is
    nothing new, so a quiet period produces no email at all.

    Returns a small dict for CLI/test reporting: {"new_events", "recipients"}.
    """
    from app.models.error_event import ErrorEvent

    since = _get_watermark()
    query = ErrorEvent.query.filter_by(resolved=False)  # tenant-scoping-ok: platform-wide, see _get_platform_admin_recipients
    if since:
        query = query.filter(ErrorEvent.first_seen_at > since)
    new_events = query.order_by(ErrorEvent.occurrence_count.desc()).all()

    now = datetime.utcnow()
    if not new_events:
        logger.info("Error digest: no new unresolved errors since %s", since)
        return {"new_events": 0, "recipients": 0}

    recipients = _get_platform_admin_recipients()
    if not recipients:
        logger.warning("Error digest: %d new error(s) but no platform_admin recipients", len(new_events))
        _set_watermark(now)
        return {"new_events": len(new_events), "recipients": 0}

    html = _render_error_digest_html(new_events, since)
    _safe_send_email(app, "New Platform Errors", recipients, html)
    _set_watermark(now)
    logger.info(
        "Error digest: %d new unresolved error(s) sent to %d recipient(s)",
        len(new_events), len(recipients),
    )
    return {"new_events": len(new_events), "recipients": len(recipients)}

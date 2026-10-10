"""CSRF default-deny coverage assertion (P-04, supersedes A-04/ARCH-051/C-10).

Flask-WTF's ``CSRFProtect`` already runs a global ``before_request`` that
rejects any POST/PUT/PATCH/DELETE without a token, *unless* the view or its
blueprint has been marked exempt with ``csrf.exempt(...)``. That mechanism is
sound, but it is implicit: an exemption is just a decorator or a function
call sitting in whatever file happened to register the blueprint, discoverable
only by grepping the whole tree. P-04 found this pattern had already produced
one falsely-justified blanket exemption (``api_v1``, closed in 9cda379) and a
second one this same audit round (``confidence_review`` API, closed
alongside this module) — a session-cookie-authenticated blueprint labelled
"programmatic callers, no CSRF token expected."

This module makes the opt-out list an explicit, reviewable inventory instead
of an implicit one. It does not replace flask-wtf's enforcement — it audits
it: at boot, once every blueprint is registered, it walks the *complete*
route table, finds every exemption flask-wtf actually holds (view-level and
blueprint-level), and fails loudly if any exemption exists that isn't on the
``CSRF_OPT_OUT`` allow-list below. Default-deny, applied to the exemptions
themselves: a new exemption someone adds later either gets a justification
here or breaks the boot.

``scripts/check_csrf_coverage.py`` runs the same walk as a verify.py gate
(so a bad exemption fails CI, not just a warning in server logs), and
``tests/test_csrf_coverage.py`` (R-30) drives write requests at every
enumerated route to prove the *effective* behaviour matches this list.
"""

from __future__ import annotations

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Every legitimate CSRF opt-out in the app, keyed by the dotted view name
# flask-wtf stores internally (``f"{view.__module__}.{view.__name__}"``).
# Audited 2026-08-18 against P-04's acceptance criteria: anything
# authenticated by the session cookie is NOT a legitimate entry here.
VIEW_OPT_OUT = {
    "app._bootstrap.routes.api_login":
        "Credential-based login endpoint — issues the session, so there is no "
        "session yet to carry a CSRF token.",
    "app._bootstrap.routes.api_logout":
        "Token-based/credential endpoint alongside api_login; logout changes "
        "nothing of consequence if forged (it only ends the caller's own "
        "session).",
    "app._bootstrap.routes.csp_report":
        "Browsers POST Content-Security-Policy violation reports "
        "automatically, with no user session or ability to attach a header.",
    "app.modules.monitoring.routes.error_events_routes.client_error":
        "Fired from a global window.onerror/unhandledrejection handler, not "
        "a user-initiated form submission -- same rationale as csp_report, "
        "and it must still work when the reported error is the CSRF fetch "
        "itself failing.",
    "app._bootstrap.routes.global_health_check":
        "Unauthenticated monitoring probe, no session to ride.",
    "app._bootstrap.routes.version_endpoint":
        "Unauthenticated monitoring/version probe, no session to ride.",
    "app._bootstrap.routes.prometheus_metrics":
        "Unauthenticated internal monitoring scraper, no session to ride.",
    "app.modules.admin.billing_routes.billing_webhook":
        "Stripe webhook — external caller, HMAC-signature verified, no "
        "session.",
    "app.modules.admin.v2.routes.admin_routes.jira_webhook":
        "Jira webhook receiver — external caller, signature-verified, no "
        "session.",
    "app.modules.admin.routes.admin_routes.jira_webhook":
        "Jira webhook receiver — external caller, signature-verified, no "
        "session. Same exemption as the v2 module above; this is the v1 "
        "fallback registered when USE_ADMIN_GUARDRAILS is off.",
    "app.modules.architecture.routes.webhook_routes.jira_webhook":
        "Jira webhook receiver — external caller, signature-verified, no "
        "session.",
    "app.modules.solutions_strategic.v2.routes.solution_design_routes.runtime_health_report":
        "Deployed-service runtime health reporter — authenticated by a "
        "constant-time HMAC comparison against RUNTIME_REPORT_TOKEN, fails "
        "closed (503) when unconfigured; not a session-cookie caller.",
    "app.routes.webhook.receive_webhook":
        "Generic external webhook receiver, signature-verified, no session.",
    "app.routes.webhook.slack_events":
        "Slack Events API — external platform, HMAC-signature verified, no "
        "session.",
    "app.routes.webhook.teams_notifications":
        "Microsoft Graph change notifications — external platform, "
        "client-state/signature verified, no session.",
    "app.modules.oauth_provider.routes.token":
        "OAuth token endpoint — called by OAuth clients (an assistant "
        "backend, a CLI) presenting an authorization code or refresh token, "
        "never a session cookie; there is no session here to carry a CSRF "
        "token.",
    "app.modules.oauth_provider.routes.revoke":
        "OAuth token revocation (RFC 7009) — same caller population as the "
        "token endpoint above, no session cookie read.",
    "app.modules.oauth_provider.routes.register":
        "Unauthenticated dynamic client registration (RFC 7591) — there is "
        "no credential or session to present yet; rate-limited per remote "
        "address instead of session-gated.",
    "app.modules.mcp.blueprint.mcp_endpoint":
        "Exempted from flask-wtf's own blanket before_request only because "
        "that hook runs before the tenant-context hook has set g.auth_mode, "
        "so it cannot yet tell a genuine bearer-only MCP call apart from "
        "anything else. app.modules.mcp.blueprint._csrf_guard (a "
        "blueprint-level before_request that runs after tenant-context) "
        "re-applies the real check unconditionally for every request except "
        "one that resolved via the bearer loader, to this exact path, with "
        "no session cookie present at all — see that module's CSRF "
        "docstring section. A session-cookie-carrying request to /mcp is "
        "not exempt from anything; it still gets the ordinary check.",
}

# Whole blueprints exempted. Every route in the blueprint must share the same
# justification, or it belongs in VIEW_OPT_OUT instead of here.
BLUEPRINT_OPT_OUT = {
    "health": "Unauthenticated monitoring probes (liveness/readiness), no session to ride.",
    "scim": "SCIM 2.0 provisioning (R1-B26): authenticated only by a per-organisation "
            "bearer token and never reads the session cookie, so there is no ambient "
            "credential a forged request could ride.",
}


class CsrfCoverageError(RuntimeError):
    """Raised at boot when an undeclared CSRF exemption is found."""


def _dest_name(view_func):
    return f"{view_func.__module__}.{view_func.__name__}"


def collect_write_rules(app):
    """Return (endpoint, dest_name, blueprint_name, methods, rule) for every
    POST/PUT/PATCH/DELETE route in the app."""
    out = []
    for rule in app.url_map.iter_rules():
        methods = (rule.methods or set()) & WRITE_METHODS
        if not methods:
            continue
        view_func = app.view_functions.get(rule.endpoint)
        if view_func is None:
            continue
        out.append(
            {
                "endpoint": rule.endpoint,
                "dest": _dest_name(view_func),
                "blueprint": rule.endpoint.rsplit(".", 1)[0] if "." in rule.endpoint else None,
                "methods": sorted(methods),
                "rule": rule.rule,
                "rule_obj": rule,
            }
        )
    return out


def is_exempt(app, entry):
    """True if flask-wtf's CSRFProtect will actually skip this route."""
    from app.extensions import csrf

    if entry["dest"] in csrf._exempt_views:
        return True
    bp = app.blueprints.get(entry["blueprint"]) if entry["blueprint"] else None
    if bp is not None and bp in csrf._exempt_blueprints:
        return True
    return False


def audit(app):
    """Walk the route table and classify every write route.

    Returns a dict: total, protected, exempt_allowed, exempt_unjustified
    (list of entries — should always be empty), all keyed for the gate
    script and the boot-time assertion to share one implementation.
    """
    entries = collect_write_rules(app)
    protected = []
    exempt_allowed = []
    exempt_unjustified = []

    for entry in entries:
        if not is_exempt(app, entry):
            protected.append(entry)
            continue
        if entry["dest"] in VIEW_OPT_OUT:
            exempt_allowed.append(entry)
        elif entry["blueprint"] in BLUEPRINT_OPT_OUT:
            exempt_allowed.append(entry)
        else:
            exempt_unjustified.append(entry)

    return {
        "total": len(entries),
        "protected": protected,
        "exempt_allowed": exempt_allowed,
        "exempt_unjustified": exempt_unjustified,
    }


def assert_csrf_coverage(app):
    """Boot-time gate: fail loudly if an exemption exists with no declared
    justification in this module. Default-deny applied to the exemption list
    itself, so a future ``csrf.exempt(...)`` either gets an entry here or
    breaks the app rather than silently widening the write surface."""
    result = audit(app)
    unjustified = result["exempt_unjustified"]
    if unjustified:
        lines = "\n".join(
            f"  - {e['dest']} ({', '.join(e['methods'])} {e['rule']})" for e in unjustified
        )
        raise CsrfCoverageError(
            "CSRF coverage gate failed: the following write routes are "
            "CSRF-exempt in code but have no justification recorded in "
            "app/_bootstrap/csrf_coverage.py (VIEW_OPT_OUT / "
            "BLUEPRINT_OPT_OUT). Every exemption must be declared and "
            "justified there — see P-04.\n" + lines
        )
    app.logger.info(
        "[CSRF] coverage: %d write routes, %d protected, %d justified opt-outs",
        result["total"],
        len(result["protected"]),
        len(result["exempt_allowed"]),
    )
    return result

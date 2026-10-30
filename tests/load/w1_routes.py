"""The wave-1 page list, the three personas, and how a route pattern becomes a URL.

One definition shared by ``tests/load/locustfile.py`` (the locust scenario) and
``scripts/ops/w1_timings.py`` (the fixed-request timing runner), so the two cannot
drift apart on which pages count as wave 1.

A route pattern is the page's route rule as the wave plan names it. Placeholders
(``<slug>``, ``<id>`` ...) are filled by ``resolve_route`` from a ``Samples`` mapping
the caller discovers from the running server; a pattern that cannot be filled is
reported as skipped with the reason, never silently dropped.

Reads only: every entry is fetched with GET.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# (id in the wave plan, route pattern). Order follows the plan.
W1_ROUTES: list[tuple[str, str]] = [
    ("PG-003", "/account/manage/change-email"),
    ("PG-004", "/account/manage/change-password"),
    ("PG-005", "/account/login"),
    ("PG-006", "/account/manage"),
    ("PG-007", "/account/manage/info"),
    ("PG-008", "/account/mfa-challenge"),
    ("PG-018", "/admin/audit-log"),
    ("PG-019", "/admin/user/<user_id>/change-account-type"),
    ("PG-020", "/admin/user/<user_id>/change-email"),
    ("PG-024", "/admin/user/<user_id>/delete"),
    ("PG-030", "/admin/"),
    ("PG-031", "/admin/invite-user"),
    ("PG-033", "/admin/new-user"),
    ("PG-038", "/admin/users"),
    ("PG-042", "/admin/user/<user_id>/set-password"),
    ("PG-047", "/admin/user/<user_id>"),
    ("PG-048", "/admin/user/<user_id>/info"),
    ("PG-059", "/dashboard/applications/create"),
    ("PG-133", "/batch-import/batches/<batch_id>/review"),
    ("PG-134", "/batch-import/"),
    ("PG-135", "/batch-import/jobs/<job_id>"),
    ("PG-136", "/batch-import/new"),
    ("PG-137", "/admin/billing/"),
    ("PG-165", "/dashboard/health"),
    ("PG-166", "/dashboard/overview"),
    ("PG-279", "/admin/connectors"),
    ("PG-288", "/demo/lantern-quay"),
    ("PG-303", "/"),
    ("PG-304", "/vs/<vs_slug>"),
    ("PG-305", "/vs"),
    ("PG-306", "/connectors/<connector_slug>"),
    ("PG-307", "/how-archiet-runs-on-entelim"),
    ("PG-308", "/data-processing-agreement"),
    ("PG-308", "/cookie-policy"),
    ("PG-308", "/refund-policy"),
    ("PG-308", "/commercial-licence"),
    ("PG-309", "/modules/<module_slug>"),
    ("PG-310", "/roles/<role_slug>"),
    ("PG-311", "/about"),
    ("PG-311", "/security"),
    ("PG-311", "/privacy"),
    ("PG-311", "/terms"),
    ("PG-311", "/contact"),
    ("PG-311", "/features"),
    ("PG-311", "/pricing"),
    ("PG-311", "/docs"),
    ("PG-311", "/architecture-health-check"),
    ("PG-311", "/team-annual-onboarding"),
    ("PG-312", "/use-cases/<use_case_slug>"),
    ("PG-313", "/use-cases"),
    ("PG-314", "/vision"),
    ("PG-315", "/settings"),
    ("PG-326", "/modules/"),
    ("PG-330", "/my-applications/"),
    ("PG-358", "/status"),
    ("PG-410", "/ai-chat/approvals/inbox"),
    ("PG-412", "/ai-chat"),
    ("PG-413", "/ai-chat/"),
    ("PG-414", "/ai-chat/run-records/<record_id>"),
    ("PG-415", "/ai-chat/run-records"),
    ("PG-416", "/applications/<application_id>/edit"),
    ("PG-417", "/applications/<application_id>/fact-sheet"),
    ("PG-418", "/applications/"),
    ("PG-419", "/applications/ownership-coverage"),
    ("PG-467", "/admin/user/<user_id>/role"),
    ("NP-01", "/account/register"),
    ("NP-02", "/account/unconfirmed"),
    ("NP-03", "/account/reset-password"),
    ("NP-04", "/account/join/<invite_token>"),
    ("NP-10", "/account/mfa-enroll"),
    ("NP-11", "/admin/connectors/github"),
    ("NP-12", "/admin/connectors/github/review"),
]

# Wave-1 pages that are not a single GET of a fixed path (a POST-only step, a
# screen reached by a state change, or a shared template with no route of its
# own). They are listed so the report accounts for every wave-1 item.
W1_NOT_ROUTES: list[tuple[str, str]] = [
    ("NP-05", "Website reading progress and company profile confirm screen"),
    ("NP-06", "Start the sample company (POST)"),
    ("NP-07", "Plan checkout and payment result (payment provider)"),
    ("NP-08", "Cancel plan and its confirmation (POST)"),
    ("NP-09", "Shared error, no-permission and sign-in-again screens"),
]

# The pages that must answer faster than the general threshold.
STRICT_ROUTES = {"/dashboard/overview", "/applications/"}
P95_LIMIT_S = 2.0
P95_STRICT_LIMIT_S = 1.5
MAX_LIMIT_S = 8.0


@dataclass(frozen=True)
class Persona:
    key: str          # stable id used in the results file
    label: str
    enterprise_role: str
    role_name: str    # app Role.name
    org_admin: bool = False


PERSONAS: list[Persona] = [
    Persona("new_cto", "New CTO", "cto", "Architect"),
    Persona("invited_viewer", "Invited Viewer", "enterprise_architect", "Viewer"),
    Persona("org_admin", "Organisation administrator", "platform_admin", "Administrator", True),
]

_PLACEHOLDER = re.compile(r"<([a-z_]+)>")


@dataclass
class Samples:
    """Concrete ids and slugs found on the running server for this run."""
    values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self.values.get(key)


def placeholders(pattern: str) -> list[str]:
    return _PLACEHOLDER.findall(pattern)


def resolve_route(pattern: str, samples: Samples) -> tuple[str | None, str | None]:
    """Return ``(url_path, None)`` or ``(None, reason)`` when a placeholder has no sample."""
    missing = [name for name in placeholders(pattern) if not samples.get(name)]
    if missing:
        return None, "no sample for " + ", ".join(sorted(set(missing)))
    return _PLACEHOLDER.sub(lambda m: samples.values[m.group(1)], pattern), None


def limit_for(pattern: str) -> float:
    return P95_STRICT_LIMIT_S if pattern in STRICT_ROUTES else P95_LIMIT_S

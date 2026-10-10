"""Fail-closed allowlist gate for connector types reaching a data-entry boundary.

Two boundaries call this today. The crosswalk resolves an external identity
(a ServiceNow sys_id, a Jira key, a device object from an AD/M365 sync, ...)
to an internal element -- that write is the point where a connected system's
data enters the model. Storing a connector's credentials and creating its
sync workflow (`ConnectorOrchestrator.create_sync_workflow`) is the point
where a connected system is first wired up at all. Both are places where a
source with no recorded lawful basis for the data it carries (HR systems,
security/SIEM tooling, arbitrary "mystery" sources nobody has reviewed) must
be refused rather than silently accepted.

The mechanism is an allowlist, not a denylist: an unrecognised connector
type is refused by default, because the failure mode of a denylist here is
"a new source ships and nobody remembers to add it to the block list", which
is exactly backwards for data that may carry personal or security
information. Only a source that is either hard-coded below or explicitly
approved via configuration is permitted through.

This module shipped ahead of the crosswalk write path it guards. The writer
now lives in `CrosswalkService.write_link`, and the static gate
`scripts/check_crosswalk_writer_gated.py` keeps that path load-bearing: if
another crosswalk writer appears without a call to this function on its path,
the build fails.

Scoping how a connector is *configured* (credentials, endpoint URLs, etc.
accepted at connector-setup time) through this same gate is a deliberate
choice, not scope creep: the permitted set is a security decision about
which sources are reviewed, and applying it at storage/wiring time closes it
before the crosswalk boundary rather than only at it. A type recognised by
`ConnectorOrchestrator`'s own workflow-builder table (Salesforce, SAP,
SharePoint, ...) but not on this allowlist is refused all the same -- add it
to `COMPLIANCE_APPROVED_CONNECTOR_TYPES` once it has been reviewed.

For the narrower historical question this module originally answered: it does
not gate a passive configuration schema object or a read path. Those surfaces
were unfenced; this function instead gates the live boundary where a connector
is wired or where its identifier is about to be written into the crosswalk.
"""

from __future__ import annotations

import logging

from flask import current_app, g

logger = logging.getLogger("archie.intelligence.connector_allowlist")

# Sources with a recorded, reviewed basis for the data they carry, permitted
# without any additional configuration:
#   - servicenow, jira, m365, devops, lucidchart: architecture/ITSM tooling
#     already in use, none of it carrying HR or security-sensitive payloads
#     by itself.
#   - ea_tool: architecture-tool exports (models, viewpoints, capability
#     maps). It is already configurable today with no gate in front of it at
#     all, and it carries architecture metadata, not personal, HR or
#     security data -- so adding a gate here would restrict something that
#     was never restricted, for data that isn't the concern this gate exists
#     to address.
PERMITTED_CONNECTOR_TYPES = frozenset(
    {"servicenow", "jira", "m365", "devops", "lucidchart", "ea_tool"}
)

# Sources known to require a compliance review before they can be connected
# (they carry HR, personal or security data), used only to produce a
# specific, actionable refusal message naming the missing artifact. This set
# is never consulted to decide whether to refuse a connector -- everything
# not in PERMITTED_CONNECTOR_TYPES (unioned with the config-approved set) is
# refused regardless of whether it appears here or not. Its only job is
# making the refusal message for a *known* pending source more useful than
# "not permitted".
GATED_CONNECTOR_TYPES = frozenset({"hr", "hris", "siem", "identity_provider"})


class ConnectorNotPermitted(PermissionError):
    """Raised when a connector type is not on the effective allowlist.

    Carries a human-readable reason naming the missing compliance artifact
    when the type is a recognised pending source, or stating plainly that
    the type is unrecognised otherwise.
    """


def _effective_allowlist() -> frozenset:
    """The permitted set plus whatever has been approved via configuration.

    `COMPLIANCE_APPROVED_CONNECTOR_TYPES` defaults to an empty tuple, so the
    default behaviour is closed: nothing is approved until a specific
    connector type is added to this configuration key, which should only
    happen once a compliance reviewer has signed off on that source's
    lawful basis for the data it carries and on the hosting implications of
    running it under this project's AGPL terms.
    """
    approved = current_app.config.get("COMPLIANCE_APPROVED_CONNECTOR_TYPES", ())
    return PERMITTED_CONNECTOR_TYPES | frozenset(approved)


def assert_connector_permitted(connector_type: str) -> None:
    """Raise ``ConnectorNotPermitted`` unless *connector_type* is allowed.

    Call this before any crosswalk write for the given connector type. Does
    nothing (returns normally) when the type is permitted; every other case
    is refused, logged at WARNING with the connector type and the current
    organisation, and surfaced to the caller via the ``feed_not_connected``
    reason code.
    """
    if connector_type in _effective_allowlist():
        return

    org_id = getattr(g, "current_org_id", None)

    if connector_type in GATED_CONNECTOR_TYPES:
        reason = (
            f"connector type {connector_type!r} requires a compliance "
            "sign-off artifact approving its lawful basis and AGPL hosting "
            "implications before it can be connected; none is on file"
        )
    else:
        reason = f"connector type {connector_type!r} is not on the permitted allowlist"

    logger.warning(
        "connector %r refused for organisation %r: %s",
        connector_type, org_id, reason,
    )

    error = ConnectorNotPermitted(reason)
    error.reason_code = "feed_not_connected"
    error.connector_type = connector_type
    raise error


__all__ = [
    "PERMITTED_CONNECTOR_TYPES",
    "GATED_CONNECTOR_TYPES",
    "ConnectorNotPermitted",
    "assert_connector_permitted",
]

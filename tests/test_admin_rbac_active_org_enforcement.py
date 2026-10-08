"""url_map-wide sweep: every route gated by ``admin_required`` or
``org_admin_required`` must refuse an administrator who is only a Viewer in
the organisation currently active in the session.

Background (R1 admin-rbac systemic fix). Three authorization mechanisms
exist in this codebase for "is this user an administrator":

  1. ``admin_required`` (app/_decorators_base.py) used to check only
     ``current_user.can(Permission.ADMINISTER)`` -- a GLOBAL flag on the
     user's own Role, independent of which organisation is active in the
     session (``g.current_org_id``, set by
     ``app/middleware/tenant_context.py`` from the session's switched-to
     organisation, or the user's home organisation if never switched).
  2. ``org_admin_required`` (app/middleware/tenant_decorators.py) looked
     LIKE the correct per-organisation check, but delegated to
     ``current_user.is_org_admin``, a property that is always computed from
     the user's own HOME ``organization_id`` -- never from
     ``g.current_org_id``. Same hole, different wrapper.
  3. ``rbac_service.is_org_admin(user, org_id)`` (app/services/rbac_service.py)
     is the one mechanism that takes an explicit ``org_id`` and answers the
     right question.

Since every self-registered user is granted Administrator of their own
organisation (``AccountService.register_user`` / ``User.grant_org_admin``),
Permission.ADMINISTER alone was the only foothold an attacker needed: accept
any invitation into a victim organisation (even as a read-only Viewer),
switch the session's active organisation to it via the real
``/account/switch-organization`` flow, and every route gated by (1) or (2)
above acted with full administrator authority over the victim organisation,
because ``TenantMixin`` silently scopes the underlying query to
``g.current_org_id`` while neither decorator checked it.

Both decorators now resolve through ``rbac_service.is_org_admin(current_user,
g.current_org_id)`` (OR ``is_platform_admin`` for an actual platform admin),
and each marks the final, possibly-further-wrapped view function with a
``_active_org_rbac_gate`` attribute for this test to find (see the matching
comments in app/_decorators_base.py and app/middleware/tenant_decorators.py
for why ``functools.wraps`` makes this survive further decorator stacking
regardless of order or depth).

This test's job is NOT to prove today's known-bad set is fixed -- it is to
make it impossible for a FUTURE new route carrying either decorator to
reintroduce this hole silently. It walks the real, live ``url_map`` rather
than a hand-maintained list of routes, so a tenth vulnerable route added
next month fails here with no one needing to remember to add it anywhere.

Scope note: this sweep only reaches views wrapped by the two decorators
fixed in this branch. One other, unrelated implementation of
"admin_required" exists elsewhere in this codebase --
``app.core.auth.decorators.admin_required``, used by ``main.settings`` /
``main.get_system_settings`` / ``main.save_system_settings``, all three now
additionally gated by ``platform_admin_required`` directly, so they are not
a live gap even though they don't carry this test's marker. (A second such
implementation, ``app.utils.decorators.admin_required``, had exactly one
caller -- ``adm_kanban_view.init_phases`` -- which has been moved onto the
canonical ``app.decorators.admin_required`` fixed in this branch, so it now
carries the marker and is swept below like any other route; the old module
still exists, in case anything else references it later, but nothing in
this codebase imports it for a route any more.)
"""

from __future__ import annotations

import uuid

import pytest
from werkzeug.routing.converters import (
    FloatConverter,
    IntegerConverter,
    NumberConverter,
    PathConverter,
    UUIDConverter,
)

pytestmark = pytest.mark.usefixtures("db_session")


# Endpoints carrying the admin_required / org_admin_required marker that are
# deliberately excluded from the strict "must 403/404" sweep below, with the
# reason recorded inline -- a stale entry here is worse than none (see
# tests/test_route_authorisation.py's own ALLOWED dict for the same
# principle), so each one must keep justifying itself.
#
# Currently empty: every route this sweep has found carrying either
# decorator is expected to -- and, as of this fix, does -- refuse a Viewer
# of the active organisation. Routes that are genuinely platform-wide
# (system settings, the global role catalogue, seed management, sidebar
# items, solution-prompt reads, the security dashboard, deprecation
# monitoring) were moved onto @platform_admin_required in this same change
# rather than exempted here, because platform_admin_required also correctly
# refuses this sweep's attacker (who holds neither the platform flag nor
# active-org admin authority) -- so they need no special case. Routes that
# only ever act on the CALLER's own home organisation (new_user,
# invite_user -- keyed off current_user.organization_id, not
# g.current_org_id) also correctly refuse this attacker once admin_required
# is active-org-scoped, which is a conservative (safe) side effect: see the
# build report's "Deviations from brief" for the one behavioural nuance this
# creates for a multi-organisation admin who has switched away from their
# own home organisation.
ALLOW_LIST: dict[str, str] = {}

_PLACEHOLDER_STRING = "rbac-sweep-probe"


def _placeholder_value(converter):
    if isinstance(converter, UUIDConverter):
        return str(uuid.uuid4())
    if isinstance(converter, (IntegerConverter, NumberConverter)):
        return 999999999
    if isinstance(converter, FloatConverter):
        return 999999999.0
    if isinstance(converter, PathConverter):
        return f"{_PLACEHOLDER_STRING}/path"
    return _PLACEHOLDER_STRING


def _build_url(app, rule):
    """Reverse-build a concrete path for *rule*, filling any dynamic
    segments with harmless placeholder values.

    The decorator stack runs before the view body resolves any of these
    values against the database, so a placeholder that matches the
    converter's syntax (an int for <int:...>, a real UUID for <uuid:...>,
    ...) is enough to reach the decorator; it never needs to name a real row.
    """
    converters = getattr(rule, "_converters", {}) or {}
    values = {arg: _placeholder_value(converters.get(arg)) for arg in rule.arguments}
    adapter = app.url_map.bind("sweep.test")
    return adapter.build(rule.endpoint, values=values)


def _attacker_switched_into_victim_org(db_session, make_org, client, login_as):
    """An administrator of their own organisation who holds only a
    read-only Viewer OrgRole in a second organisation, with the session's
    active organisation switched to that second one via the real
    ``/account/switch-organization`` flow -- not a database shortcut.

    This is the exact shape of attacker the sweep described above names:
    the only foothold they need is one accepted invitation, at the lowest
    privilege level that exists.
    """
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation
    from app.models.user import Role, User

    home_org = make_org("active-org-sweep-home")
    victim_org = make_org("active-org-sweep-victim")

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")

    attacker = User(
        email=f"active-org-sweep-{uuid.uuid4().hex[:8]}@example.test",
        first_name="ActiveOrg",
        last_name="Sweep",
        organization_id=home_org.id,
        confirmed=True,
        role=admin_role,
    )
    attacker.password = uuid.uuid4().hex
    db_session.add(attacker)
    db_session.flush()
    assert attacker.is_admin() is True, "fixture setup must grant home-org admin"

    inviter = User(
        email=f"active-org-sweep-inviter-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Victim",
        last_name="Inviter",
        organization_id=victim_org.id,
        confirmed=True,
        role=admin_role,
    )
    inviter.password = uuid.uuid4().hex
    db_session.add(inviter)
    db_session.flush()

    invitation, _created = PendingInvitation.create_for(
        victim_org.id, attacker.id, "viewer", invited_by_id=inviter.id
    )
    db_session.commit()

    login_as(client, attacker)
    accepted = client.post(
        f"/account/invitation/{invitation.id}/accept", follow_redirects=False
    )
    assert accepted.status_code == 302, (
        f"fixture setup: accepting the invitation into the victim org failed "
        f"({accepted.status_code})"
    )
    assert OrgRole.get_role(victim_org.id, attacker.id) == "viewer", (
        "fixture setup: attacker must hold only Viewer in the victim org"
    )

    switched = client.post(
        "/account/switch-organization",
        data={"organization_id": str(victim_org.id)},
        follow_redirects=True,
    )
    assert switched.status_code == 200, (
        f"fixture setup: switching the active session into the victim org "
        f"failed ({switched.status_code})"
    )

    return attacker, home_org, victim_org


def test_a_viewer_of_the_active_org_is_refused_by_every_admin_required_route(
    app, db_session, make_org, client, login_as
):
    attacker, home_org, victim_org = _attacker_switched_into_victim_org(
        db_session, make_org, client, login_as
    )

    findings = []
    checked = 0
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.endpoint):
        view = app.view_functions.get(rule.endpoint)
        gate = getattr(view, "_active_org_rbac_gate", None)
        if gate is None:
            continue
        if rule.endpoint in ALLOW_LIST:
            continue

        try:
            url = _build_url(app, rule)
        except Exception as exc:  # noqa: BLE001 - a build failure is itself a finding
            findings.append(
                f"{rule.endpoint} [{gate}] ({rule.rule}): could not build a "
                f"probe URL ({exc!r})"
            )
            continue

        methods = sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}) or ["GET"]
        for method in methods:
            checked += 1
            response = client.open(url, method=method)
            if response.status_code not in (403, 404):
                findings.append(
                    f"{rule.endpoint} {method} {url} [{gate}] -> "
                    f"{response.status_code} (expected 403 or 404)"
                )

    # A detector that never matches anything would make the assertion below
    # vacuous -- pin a floor so a future refactor that breaks the
    # _active_org_rbac_gate marker itself fails loudly here, not silently.
    assert checked >= 40, (
        f"only found {checked} (rule, method) pairs carrying the "
        f"admin_required/org_admin_required marker -- expected dozens; the "
        f"marker itself may be broken (see app/_decorators_base.py's "
        f"admin_required and app/middleware/tenant_decorators.py's "
        f"org_admin_required)"
    )

    assert not findings, (
        f"{len(findings)} admin_required/org_admin_required route(s) let a "
        f"Viewer of the active organisation ({victim_org.slug}, where the "
        f"attacker holds only Viewer) through, instead of refusing with 403 "
        f"or 404:\n  " + "\n  ".join(findings)
    )


def test_the_same_attacker_is_still_admitted_to_their_own_home_org(
    app, db_session, make_org, client, login_as
):
    """The sweep above must be measuring the active-org check, not merely
    rejecting this attacker outright. Switch back to the home organisation
    (still logged in as the same user) and confirm at least one
    admin_required route that is genuinely organisation-scoped admits them
    there -- proving a false "refused" above cannot be explained by a
    broken login or an unrelated 403.
    """
    attacker, home_org, _victim_org = _attacker_switched_into_victim_org(
        db_session, make_org, client, login_as
    )

    switched_home = client.post(
        "/account/switch-organization",
        data={"organization_id": str(home_org.id)},
        follow_redirects=True,
    )
    assert switched_home.status_code == 200

    response = client.get("/admin/users")
    assert response.status_code == 200, (
        f"the same admin, back in their own home organisation, was refused "
        f"/admin/users ({response.status_code}) -- the sweep above cannot "
        f"be trusted if this fails"
    )

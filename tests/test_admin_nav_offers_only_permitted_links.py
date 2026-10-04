"""The admin console must not link to a destination it will refuse.

The sweep found ``/admin/`` rendering 11 links for a user whose
``enterprise_role`` is ``platform_admin``, two of which answered **403**:
``/admin/api-settings`` and ``/admin/feature-flags``.

The cause is that ``enterprise_role`` is a *persona* string driving sidebar and
dashboard composition, while ``@org_admin_required`` and
``@platform_admin_required`` read the ``is_org_admin`` / ``is_platform_admin``
Boolean **columns**. Nothing sets one from the other.

Which half was wrong: the guards are right. ``/admin/api-settings`` edits LLM
provider credentials and ``/admin/feature-flags`` changes platform-wide
behaviour, so both are legitimately narrower than "can see the admin console".
Relaxing them to plain ``@admin_required`` would widen credential access to
every admin — a security regression to fix a navigation bug. The link is
therefore rendered on the same predicate the route enforces.

These tests pin both halves: the tile is absent for a user the guard will
refuse, and present for one it will admit — so a future edit cannot fix one
direction by breaking the other.
"""

from __future__ import annotations

import uuid
import re

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _rendered_hrefs(response):
    return set(re.findall(r'href="([^"]+)"', response.get_data(as_text=True)))


def _make_admin(db_session, make_org, *, org_admin: bool, platform_admin: bool):
    """An admin-console user with the two guard columns set explicitly.

    ``/admin/`` itself is guarded by ``@admin_required``, which resolves
    ``current_user.is_admin()`` — the ADMINISTER permission bit on the attached
    ``Role`` — or falls back to ``is_superuser``. Attach the administrator role
    so the console renders at all. Never assign to ``user.is_admin``: it is a
    method, and assigning shadows it.
    """
    from app.models.user import Permission, Role, User

    org = make_org("adminnav")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"adminnav-{suffix}@example.com",
        first_name="Admin",
        last_name="Nav",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="platform_admin",
    )
    user.password = "Sup3rSecret!23"
    user.is_org_admin = org_admin
    user.is_platform_admin = platform_admin
    db_session.add(user)
    admin_role = Role.query.filter(
        Role.permissions.op("&")(Permission.ADMINISTER) == Permission.ADMINISTER
    ).first()
    if admin_role is not None:
        user.role = admin_role
    db_session.flush()
    if not user.is_admin():
        pytest.skip("no ADMINISTER role seeded in this database; /admin/ is unreachable")
    return user


GUARDED = [
    # is_org_admin now derives from is_admin() (Permission.ADMINISTER),
    # so @org_admin_required is equivalent to @admin_required.  /admin/api-settings
    # is therefore reachable by every admin user and no longer needs a separate
    # org_admin guard entry here.  /admin/feature-flags is still independently
    # gated on the is_platform_admin cross-tenant flag.
    ("/admin/feature-flags", False, True),
]


@pytest.mark.parametrize("href,needs_org_admin,needs_platform_admin", GUARDED)
def test_link_is_absent_when_the_guard_would_refuse(
    app, db_session, make_org, client, login_as, href, needs_org_admin, needs_platform_admin
):
    user = _make_admin(db_session, make_org, org_admin=False, platform_admin=False)
    login_as(client, user)

    index = client.get("/admin/")
    assert index.status_code == 200, index.get_data(as_text=True)[:400]
    assert href not in _rendered_hrefs(index), (
        f"/admin/ offers {href} to a user the route will 403"
    )

    # And confirm the destination really does refuse, so the assertion above is
    # measuring the guard rather than a coincidence.
    assert client.get(href).status_code == 403


@pytest.mark.parametrize("href,needs_org_admin,needs_platform_admin", GUARDED)
def test_link_is_present_when_the_guard_would_admit(
    app, db_session, make_org, client, login_as, href, needs_org_admin, needs_platform_admin
):
    user = _make_admin(
        db_session,
        make_org,
        org_admin=needs_org_admin,
        platform_admin=needs_platform_admin,
    )
    login_as(client, user)

    index = client.get("/admin/")
    assert index.status_code == 200, index.get_data(as_text=True)[:400]
    assert href in _rendered_hrefs(index), (
        f"/admin/ hides {href} from a user the route will admit"
    )
    assert client.get(href).status_code != 403


def test_no_admin_index_link_answers_403(app, db_session, make_org, client, login_as):
    """Exhaustive form: every internal link on /admin/ must be reachable.

    This is the invariant the two parametrised tests above are instances of. It
    walks the rendered page rather than a hand-maintained list, so a tile added
    later behind a narrower guard fails here without anyone remembering to
    extend GUARDED.
    """
    user = _make_admin(db_session, make_org, org_admin=False, platform_admin=False)
    login_as(client, user)

    body = client.get("/admin/").get_data(as_text=True)
    hrefs = {
        href.split("?")[0].split("#")[0]
        for href in re.findall(r'href="(/[^"]*)"', body)
    }
    hrefs = {h for h in hrefs if not h.startswith("/static/")}

    refused = []
    for href in sorted(hrefs):
        status = client.get(href).status_code
        if status in (401, 403):
            refused.append((href, status))

    assert not refused, f"/admin/ links to destinations it refuses: {refused}"

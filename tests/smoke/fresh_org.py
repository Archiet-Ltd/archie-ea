"""A brand-new organisation with one signed-in person and no records at all.

The shared ``seeded`` organisation carries applications, solutions, contracts
and radar elements created through the ORM, so a journey run against it can
pass on data a real customer would never have on day one. The launch journeys
use this instead: the organisation and its user are the only rows created
directly, and every record the journey reads back afterwards was made through
the product's own screens or the interfaces those screens call.
"""

import uuid

from .conftest import PAGE_TIMEOUT, PASSWORD


def create_fresh_org(enterprise_role, *, org_admin=False, extra_roles=()):
    """Create an empty organisation and a person in it; return the ids.

    ``extra_roles`` adds more people to the same organisation (for a second
    persona a journey hands work to). Returns
    ``{"org_id", "suffix", "emails": {role: email}, "user_ids": {role: id}}``.
    """
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    out = {"suffix": suffix, "emails": {}, "user_ids": {}}
    with app.app_context():
        from app.models.org_role import OrgRole
        from app.models.organization import Organization
        from app.models.user import Role, User

        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()
        org = Organization(name="Launch Org %s" % suffix, slug="launch-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        out["org_id"] = org.id
        for role in (enterprise_role,) + tuple(extra_roles):
            email = "launch.%s.%s@example.com" % (role.replace("_", "-"), suffix)
            is_org_admin = bool(org_admin and role == enterprise_role)
            user = User(email=email, first_name="Launch", last_name=role[:12],
                        organization_id=org.id, enterprise_role=role, confirmed=True)
            user.role = architect_role
            user.is_org_admin = is_org_admin
            user.password = PASSWORD
            db.session.add(user)
            db.session.flush()
            user_id = user.id
            db.session.commit()
            if is_org_admin:
                OrgRole.set_role(org.id, user_id, "org_admin", granted_by_id=user_id)
                db.session.commit()
            out["emails"][role] = email
            out["user_ids"][role] = user_id
        db.session.remove()
    return out


def sign_in(page, base, email):
    """Sign in through the login form and wait until the login page is left."""
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def api(page, method, path, data=None):
    """Call the product's own JSON interface from the signed-in page.

    Goes through the page's own session and CSRF token, exactly as the
    screen's scripts do, and returns ``(status, parsed_json_or_text)``.
    """
    return page.evaluate(
        """async ([method, path, data]) => {
            const meta = document.querySelector('meta[name="csrf-token"]');
            const headers = {'Accept': 'application/json'};
            if (meta) headers['X-CSRFToken'] = meta.content;
            const init = {method, headers, credentials: 'same-origin'};
            if (data !== null) {
                headers['Content-Type'] = 'application/json';
                init.body = JSON.stringify(data);
            }
            const r = await fetch(path, init);
            const text = await r.text();
            let body = text;
            try { body = JSON.parse(text); } catch (e) { /* not JSON */ }
            return [r.status, body];
        }""",
        [method, path, data],
    )

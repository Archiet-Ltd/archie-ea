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


def add_administrator(org_id, suffix):
    """Give an organisation a separate Administrator and return that person's id.

    A persona under test often must not be an administrator, but every
    organisation with members needs one. This adds one more person, who is
    nobody's persona, so the persona keeps exactly the roles it asked for.
    Call it inside an app context.
    """
    from app import db
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services.billing_plans import set_contract_plan

    # The extra person must not be what takes the organisation past the people
    # its Community plan admits, or the personas it was added beside are refused.
    set_contract_plan(db.session.get(Organization, org_id), "enterprise", None)
    db.session.commit()
    admin = User(email="launch.admin.%s@example.com" % suffix, first_name="Launch", last_name="Admin",
                 organization_id=org_id, enterprise_role="platform_admin", confirmed=True)
    admin.role = Role.query.filter_by(name="Architect").one()
    admin.is_org_admin = True
    admin.password = PASSWORD
    db.session.add(admin)
    db.session.commit()
    OrgRole.set_role(org_id, admin.id, "org_admin", granted_by_id=admin.id)
    db.session.commit()
    return admin.id


def create_fresh_org(enterprise_role, *, org_admin=False, extra_roles=(), platform_role="Architect"):
    """Create an empty organisation and a person in it; return the ids.

    ``extra_roles`` adds more people to the same organisation (for a second
    persona a journey hands work to). ``platform_role`` names the platform
    role the first person holds; the others always hold Architect. Returns
    ``{"org_id", "suffix", "emails": {role: email}, "user_ids": {role: id}}``.
    When ``org_admin`` is false a separate Administrator person is added, so the
    organisation is never left without one and the personas keep their roles.
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
        first_role = Role.query.filter_by(name=platform_role).one()
        org = Organization(name="Launch Org %s" % suffix, slug="launch-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        out["org_id"] = org.id
        for role in (enterprise_role,) + tuple(extra_roles):
            email = "launch.%s.%s@example.com" % (role.replace("_", "-"), suffix)
            is_org_admin = bool(org_admin and role == enterprise_role)
            user = User(email=email, first_name="Launch", last_name=role[:12],
                        organization_id=org.id, enterprise_role=role, confirmed=True)
            user.role = first_role if role == enterprise_role else architect_role
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
        if not org_admin:
            out["administrator_id"] = add_administrator(org.id, suffix)
        db.session.remove()
    return out


def sign_in(page, base, email):
    """Sign in through the login form and wait until the login page is left."""
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, wait_until="commit", timeout=PAGE_TIMEOUT)
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


def list_date(value):
    """A date as the product's own date filter writes it on a page (day, month name, year)."""
    from app import create_app

    app = create_app("testing")
    with app.test_request_context():
        return app.jinja_env.filters["format_datetime"](value, "d MMM yyyy")

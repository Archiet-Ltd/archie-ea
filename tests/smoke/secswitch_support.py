"""Shared set-up for the security-switch journeys: two organisations, an
administrator with an authenticator, and members."""

import re
import uuid

import pyotp

PASSWORD = "SecSwitch!Journey2026"


def seed(app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    out = {"suffix": suffix}
    with app.app_context():
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").one()
        member_role = Role.query.filter_by(name="Architect").one()
        orgs = {}
        for tag in ("a", "b"):
            org = Organization(name="SecSwitch %s %s" % (tag.upper(), suffix), slug="secswitch-%s-%s" % (tag, suffix))
            db.session.add(org)
            db.session.flush()
            orgs[tag] = org
        db.session.commit()
        out["org_a"], out["org_b"] = orgs["a"].id, orgs["b"].id

        def make(kind, org, role, secret=None, admin=False):
            email = "secswitch.%s.%s@example.com" % (kind, suffix)
            user = User(email=email, first_name="Sec", last_name=kind, organization_id=org.id,
                        enterprise_role="solution_architect", confirmed=True)
            user.role = role
            user.is_platform_admin = admin
            user.is_org_admin = admin
            user.password = PASSWORD
            if secret:
                user.mfa_secret = secret
                user.mfa_enabled = True
            db.session.add(user)
            db.session.commit()
            out[kind] = email
            return user

        out["admin_secret"] = pyotp.random_base32()
        out["member_secret"] = pyotp.random_base32()
        make("admin", orgs["a"], admin_role, out["admin_secret"], admin=True)
        make("plain", orgs["a"], member_role)
        make("enrolled", orgs["a"], member_role, out["member_secret"])
        make("other", orgs["b"], member_role)
    return out


def sign_in(page, base, email):
    page.context.clear_cookies()
    page.goto(base + "/account/login", wait_until="domcontentloaded")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_load_state("domcontentloaded")


def pass_code(page, secret):
    page.fill("#mfa-code", pyotp.TOTP(secret).now())
    page.get_by_role("button", name="Verify").click()
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)


def open_security_tab(page, base):
    page.goto(base + "/settings", wait_until="domcontentloaded")
    page.get_by_role("tab", name="Security").click()


def save(page):
    with page.expect_response(lambda r: "/api/system-settings/save" in r.url) as info:
        page.get_by_test_id("btn-save-all").click()
    return info.value

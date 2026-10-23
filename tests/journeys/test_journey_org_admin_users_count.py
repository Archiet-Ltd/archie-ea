"""Journey: an organisation administrator opens the Users page and the count
they read equals the rows listed; another organisation's users never appear."""

import re

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey


def test_admin_users_page_count_equals_rows_listed(app, client):
    from app import db

    with app.app_context():
        a = make_org(db, "Dg126 Alpha")
        b = make_org(db, "Dg126 Beta")
        from app.models.organization import Organization

        for oid in (a, b):
            db.session.get(Organization, oid).plan = "startup"
        db.session.commit()
        admin_id = make_user(db, a, "alphaadmin", enterprise_role="platform_admin")
        # make_user sets platform_admin enterprise_role only; ensure plain org admin
        for i in range(2):
            make_user(db, a, "alpha%d" % i, enterprise_role="business_architect")
        beta_emails = []
        from app.models.user import User

        for i in range(5):
            uid = make_user(db, b, "beta%d" % i, enterprise_role="business_architect")
            beta_emails.append(db.session.get(User, uid).email)

    login(client, admin_id)
    resp = client.get("/admin/users")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "platform-wide" not in html
    m = re.search(r"\(\s*(\d+) users?\s*\)", html)
    assert m, "no user count on the page"
    count = int(m.group(1))
    assert count == 3
    rows = len(set(re.findall(r"[\w.+-]+@example\.com", html)))
    assert rows == count, "count %d but %d people listed" % (count, rows)
    assert not any(e in html for e in beta_emails)

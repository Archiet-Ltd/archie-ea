"""The startup credit's security conditions and its reminders, with two real organisations.

The holder of a credit is never named to another organisation, only an administrator of the
active organisation can claim, and the daily job reminds 14 and 3 days before the end, once each.
"""

import logging
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from .conftest import login, make_org, make_user
from .test_startup_credit import _claim, _founder_org, _founded_months_ago, _remove, _text

pytestmark = pytest.mark.journey


def _domain():
    return "acme-%s.test" % uuid.uuid4().hex[:8]


def _grant(app, org_id, user_id, domain, now=None):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.billing_offers import grant_startup_credit

    with app.app_context():
        org = db.session.get(Organization, org_id)
        result = grant_startup_credit(
            org, db.session.get(User, user_id),
            {"website": "https://%s" % domain, "founded": ((now or datetime.utcnow()) - timedelta(days=200)).strftime("%Y-%m"), "raised": "nothing"},
            now,
        )
        assert result.granted, result.reason
        db.session.commit()


def _audit_marks():
    from app import db

    marks = {}
    for table in ("soc2_audit_log", "audit_events"):
        marks[table] = db.session.execute(text("SELECT COALESCE(MAX(id), 0) FROM %s" % table)).scalar()  # nosec B608 - literal names
    return marks


def test_the_refusal_and_its_audit_rows_name_nothing_of_the_holder(app, client, caplog):
    from app import db

    domain = _domain()
    with app.app_context():
        a_org, a_admin = _founder_org(db, "Holder Secretco", "ana@%s" % domain, "https://%s" % domain)
        b_org, b_admin = _founder_org(db, "Asker", "bob@eu.%s" % domain, "https://eu.%s" % domain)
        from app.models.organization import Organization

        holder = db.session.get(Organization, a_org)
        name, slug = holder.name, holder.slug
    _grant(app, a_org, a_admin, domain)
    try:
        login(client, b_admin)
        with app.app_context():
            marks = _audit_marks()
        with caplog.at_level(logging.DEBUG):
            response = _claim(client, "https://eu.%s" % domain)
        body = response.get_data(as_text=True)
        assert "This company has already used its 90 days." in _text(response)
        assert name not in body and slug not in body
        assert "Secretco" not in body
        with app.app_context():
            for table, mark in marks.items():
                rows = db.session.execute(
                    text("SELECT * FROM %s WHERE id > :m" % table), {"m": mark}).mappings().all()  # nosec B608 - literal names
                for row in rows:
                    flat = " ".join(str(v) for v in row.values())
                    assert name not in flat and slug not in flat, "%s names the holder" % table
                    assert row.get("organization_id") != a_org, "%s row belongs to the holder" % table
        for record in caplog.records:
            line = record.getMessage()
            assert name not in line and slug not in line, line
            assert "organisation %s " % a_org not in (line + " ")
    finally:
        _remove(app, [a_org, b_org])


def test_a_viewer_in_the_active_organisation_cannot_claim_for_it(app, client):
    from app import db
    from app.models.org_role import OrgRole
    from app.models.subscription import Subscription

    domain = _domain()
    with app.app_context():
        home_org, _ = _founder_org(db, "Home Org", "home@%s" % domain, "https://%s" % domain)
        x_org, x_admin = _founder_org(db, "Active Org", "boss@%s" % domain, "https://%s" % domain)
        person = make_user(db, home_org, "Dual", "platform_admin", "Administrator")
        OrgRole.set_role(x_org, person, "viewer", granted_by_id=x_admin)
        from app.models.user import User

        db.session.get(User, person).mfa_enabled = True
        db.session.get(User, person).mfa_enrolled_at = datetime.utcnow()
        db.session.commit()
    try:
        login(client, person)
        with client.session_transaction() as sess:
            sess["current_org_id"] = x_org  # working in the organisation they only view
        with app.app_context():
            before = Subscription.query.filter_by(organization_id=x_org).first()
            before = (before.offer, before.plan) if before else None
        response = client.post(
            "/admin/billing/startup-credit",
            data={"website": "https://%s" % domain, "founded": _founded_months_ago(6), "raised": "nothing"},
        )
        assert response.status_code == 403
        with app.app_context():
            row = Subscription.query.filter_by(organization_id=x_org).first()
            assert ((row.offer, row.plan) if row else None) == before
            assert row is None or row.offer is None
            home = Subscription.query.filter_by(organization_id=home_org).first()
            assert home is None or home.offer is None
    finally:
        _remove(app, [home_org, x_org])


def test_reminders_arrive_14_and_3_days_before_the_end_once_each_and_the_bell_opens_billing(app, client):
    from app import db
    from app.models.models import Notification
    from app.models.subscription import Subscription
    from app.services.billing_offers import send_offer_reminders

    domain, other_domain = _domain(), _domain()
    granted = datetime(2026, 1, 10, 9, 0, 0)
    with app.app_context():
        a_org, a_admin = _founder_org(db, "Remind Alpha", "ana@%s" % domain, "https://%s" % domain)
        b_org, b_admin = _founder_org(db, "Remind Bravo", "bea@%s" % other_domain, "https://%s" % other_domain)
    _grant(app, a_org, a_admin, domain, granted)
    _grant(app, b_org, b_admin, other_domain, granted)
    end = granted + timedelta(days=90)
    try:
        def run(now):
            with app.app_context():
                send_offer_reminders(app, organization_ids=[a_org], now=now)
                db.session.remove()

        def notes(user_id):
            with app.app_context():
                return [(n.message, n.url, n.event_type) for n in
                        Notification.query.filter_by(user_id=user_id, event_type="startup_credit_ending").all()]

        run(end - timedelta(days=20))
        assert notes(a_admin) == [], "a reminder arrived 20 days before the end"
        run(end - timedelta(days=14) + timedelta(hours=1))
        first = notes(a_admin)
        assert len(first) == 1 and "14 days" in first[0][0] and "Buy Startup before then" in first[0][0]
        run(end - timedelta(days=14) + timedelta(hours=2))
        assert len(notes(a_admin)) == 1, "the 14-day reminder was sent twice"
        run(end - timedelta(days=3) + timedelta(hours=1))
        second = notes(a_admin)
        assert len(second) == 2 and any("3 days" in m for m, _, _ in second)
        run(end - timedelta(days=3) + timedelta(hours=5))
        assert len(notes(a_admin)) == 2, "the 3-day reminder was sent twice"
        assert notes(b_admin) == [], "another organisation was reminded by this run"

        with app.app_context():
            row = Subscription.query.filter_by(organization_id=a_org).one()
            assert [r["days"] for r in row.offer_terms["reminders"]] == [14, 3]
            other = Subscription.query.filter_by(organization_id=b_org).one()
            assert other.offer_terms["reminders"] == []

        # The bell's link opens the billing page with the end date shown. The end moves
        # forward in the fixture so the credit is still running when the page is read.
        with app.app_context():
            row = Subscription.query.filter_by(organization_id=a_org).one()
            row.current_period_end = datetime.utcnow() + timedelta(days=3)
            db.session.commit()
        link = second[0][1]
        login(client, a_admin)
        page = client.get(link, follow_redirects=True)
        shown = _text(page)
        assert page.status_code == 200
        assert "Free until %s" % (datetime.utcnow() + timedelta(days=3)).strftime("%d %b %Y").lstrip("0") in shown
    finally:
        _remove(app, [a_org, b_org])

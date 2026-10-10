"""Browser journey: a prospect opens the demonstration organisation, sees it
labelled, starts a trial, and lands in a separate organisation with the trial
plan's limits and none of the demonstration's data.

The journey commits to the shared database (the trial organisation, its person
and subscription; the demonstration organisation too when the database had none).
A cleanup fixture removes every row it added, whatever the outcome, so the file
can run twice, or after any other smoke file, and leaves no organisation behind.
"""
import uuid

import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TRIAL_PASSWORD = "Trial-Journey!2026"
_DISMISS_ONBOARDING_SCRIPT = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"


def _delete_organisation(org_id, user_ids):
    """Delete an organisation and every row that points at it or its people."""
    from sqlalchemy import text

    from app import db

    conn = db.session.connection()
    fks = conn.execute(
        text(
            """
            SELECT tc.table_name, kcu.column_name, ccu.table_name AS parent
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY' AND ccu.table_name IN ('users', 'organizations')
            """
        )
    ).fetchall()
    for _ in range(3):  # a second and third pass clear rows that pointed at rows just removed
        for table, column, parent in fks:
            if table in ("users", "organizations"):
                continue
            ids = user_ids if parent == "users" else [org_id]
            if not ids:
                continue
            try:
                with db.session.begin_nested():
                    conn.execute(
                        text('DELETE FROM "%s" WHERE "%s" = ANY(:ids)' % (table, column)),
                        {"ids": list(ids)},
                    )
            except ValueError:
                # An append-only evidence table refuses every delete. A journey
                # never writes to one, so there is nothing of ours in it.
                continue
    if user_ids:
        conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": list(user_ids)})
    conn.execute(text("DELETE FROM organizations WHERE id = :i"), {"i": org_id})
    db.session.commit()


@pytest.fixture
def demonstration_journey(app):
    """The demonstration organisation for the journey, and the cleanup of everything it adds."""
    from app import db
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services import demonstration_service as demo

    trial_email = "trial-journey-%s@example.com" % uuid.uuid4().hex[:8]
    state = {"trial_email": trial_email, "created_demo": False, "prior_settings": None}
    with app.app_context():
        Role.insert_roles()
        org = Organization.query.filter_by(slug=demo.DEMO_SLUG).first()
        if org is None:
            org = Organization(name="Lantern Quay Systems", slug=demo.DEMO_SLUG)
            db.session.add(org)
            db.session.flush()
            state["created_demo"] = True
        else:
            state["prior_settings"] = dict(org.settings or {})
        demo.mark_demonstration(org)
        viewer = User.query.filter_by(email=demo.DEMO_USER_EMAIL, organization_id=org.id).first()
        state["created_viewer"] = viewer is None
        if viewer is None:
            db.session.add(
                User(
                    email=demo.DEMO_USER_EMAIL,
                    first_name="Demo",
                    last_name="User",
                    organization_id=org.id,
                    confirmed=True,
                    role=Role.query.filter_by(name="Viewer").first(),
                )
            )
        db.session.commit()
        state["demo_org_id"] = org.id
    try:
        yield state
    finally:
        with app.app_context():
            db.session.rollback()
            trial_user = User.query.filter_by(email=trial_email).first()
            if trial_user is not None:
                _delete_organisation(trial_user.organization_id, [trial_user.id])
            if state["created_demo"]:
                viewers = [u.id for u in User.query.filter_by(organization_id=state["demo_org_id"]).all()]
                _delete_organisation(state["demo_org_id"], viewers)
            else:
                org = db.session.get(Organization, state["demo_org_id"])
                org.settings = state["prior_settings"]
                if state["created_viewer"]:
                    User.query.filter_by(
                        email=demo.DEMO_USER_EMAIL, organization_id=org.id
                    ).delete()
                db.session.commit()


def test_prospect_opens_the_demonstration_then_starts_a_trial(
    browser, live_server, app, demonstration_journey
):
    from app import db
    from app.models import ArchiMateElement
    from app.models.organization import Organization
    from app.models.subscription import SubscriptionStatus
    from app.models.user import User
    from app.services import billing_plans

    email = demonstration_journey["trial_email"]
    context = browser.new_context()
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = context.new_page()
    try:
        # From the pricing page into the demonstration.
        page.goto(live_server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("try-demonstration").click()
        page.wait_for_url("**/demonstration", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("demonstration-enter").click()
        page.wait_for_url(lambda u: "/demonstration" not in u, timeout=PAGE_TIMEOUT)

        # Labelled on this screen, on the composer, and after a reload.
        banner = page.get_by_test_id("demonstration-banner")
        banner.wait_for(timeout=PAGE_TIMEOUT)
        assert "not yours" in banner.inner_text()
        page.goto(live_server + "/archimate/composer", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        composer_banner = page.get_by_test_id("demonstration-banner")
        composer_banner.wait_for(timeout=PAGE_TIMEOUT)
        assert composer_banner.count() == 1
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("demonstration-banner").is_visible()

        # Start a trial from the banner.
        page.get_by_test_id("demonstration-banner-trial").click()
        page.wait_for_url("**/demonstration/trial", timeout=PAGE_TIMEOUT)
        page.get_by_role("heading", name="Start your trial").wait_for(timeout=PAGE_TIMEOUT)
        page.fill("#first_name", "Trial")
        page.fill("#last_name", "Journey")
        page.fill("#email", email)
        page.fill("#password", TRIAL_PASSWORD)
        page.fill("#password2", TRIAL_PASSWORD)
        page.get_by_role("button", name="Register").click()
        page.wait_for_url(lambda u: "/demonstration/trial" not in u, timeout=PAGE_TIMEOUT)

        # Reload: signed in to their own organisation, no demonstration label.
        page.goto(live_server + "/dashboard/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/account/login" not in page.url
        assert page.get_by_test_id("demonstration-banner").count() == 0

        with app.app_context():
            user = User.query.filter_by(email=email).one()
            org = db.session.get(Organization, user.organization_id)
            assert org.id != demonstration_journey["demo_org_id"]
            sub = billing_plans.current_subscription(org)
            assert sub.status == SubscriptionStatus.trialing
            status = billing_plans.user_limit_status(org.id)
            assert status["plan_key"] == "free" and status["limit"] == 3
            assert ArchiMateElement.query.filter_by(organization_id=org.id).count() == 0
            assert "demonstration" not in (org.settings or {})
    finally:
        context.close()

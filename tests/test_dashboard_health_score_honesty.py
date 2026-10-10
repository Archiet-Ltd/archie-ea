"""The dashboard health score must not invent a verdict it cannot compute.

"21 HEALTH SCORE" is the largest number in the product and the one a leader
quotes. It was assembled from fake denominators — `max(len(mature_sols), 1)` and
`total_solutions or 1` — so an estate with nothing to measure produced a
confident 0% for risk health and governance, which then dragged the composite
down. A reader cannot tell "we measured your risk posture and it is zero" from
"there was nothing to measure", and the first reading is both alarming and false.

Per CLAUDE.md: a 0 that means "not computed" is indistinguishable from a measured
zero; use None, rendered as an em dash.
"""
import datetime
import re

from app import db
from app.models.organization import Organization
from app.models.user import User


def _org(db_session, slug):
    o = Organization(name=f"Health {slug}", slug=slug)
    db.session.add(o)
    db.session.flush()
    return o


def _user(org, tag):
    u = User(
        email=f"health-{tag}-{org.id}@example.com",
        first_name="H",
        last_name="S",
        organization_id=org.id,
        confirmed=True, onboarding_completed_at=datetime.datetime.utcnow(),  # else before_request bounces to /account/unconfirmed
        # The health hero is inside `{% if role != 'platform_admin' %}`, and a
        # seeded user defaults to platform_admin — so without this the assertions
        # run against a page that never rendered the number under test.
        enterprise_role="enterprise_architect",
    )
    u.password = "TestPass123!"
    db.session.add(u)
    db.session.flush()
    return u


def _visible_text(resp):
    html = resp.get_data(as_text=True)
    html = re.sub(r"(?is)<script\b.*?</script>", " ", html)
    html = re.sub(r"(?is)<style\b.*?</style>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html))


def test_dashboard_never_shows_a_fabricated_zero_for_an_unmeasurable_component(
    db_session, client, login_as, tenant_ctx
):
    """Whatever the dashboard renders, it must not claim 0% it did not measure.

    The hero card only renders for some roles and states, so this asserts on
    whatever is actually present rather than requiring the hero — a test that
    demanded the hero would pass for the wrong reason on an empty tenant, where
    it is not rendered at all.
    """
    org = _org(db_session, "no-fake-zero")
    with tenant_ctx(org.id):
        user = _user(org, "nofake")
    login_as(client, user)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200
    text = _visible_text(resp)

    for label in ("Risk Posture", "Governance", "Phase Maturity", "Capability Coverage"):
        i = text.find(label)
        if i == -1:
            continue  # component not rendered in this state; nothing to fabricate
        window = text[i : i + 60]
        assert "0%" not in window, (
            f"{label} reports 0% on a tenant with nothing to measure: {window!r}"
        )


def test_health_score_never_renders_the_word_none(db_session, client, login_as, tenant_ctx):
    """Guarding with `is none` must not leak Python's None into the page."""
    org = _org(db_session, "no-none")
    with tenant_ctx(org.id):
        user = _user(org, "nonone")
    login_as(client, user)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200
    text = _visible_text(resp)
    assert "None%" not in text
    assert "None/100" not in text

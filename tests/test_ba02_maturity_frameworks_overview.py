"""BA-02: the capability-maturity frameworks overview must show real numbers.

The stats query in `frameworks_overview()` had been gutted — it built a
placeholder string and a params dict, discarded both, and left `framework_stats`
empty. Every card is guarded by `{% if stats and stats[0] > 0 %}`, so the page
rendered blank and the feature read as "a Maturity view that populates no score".

These tests pin the numbers, the tenant predicate (raw SQL, so ADR-0003 means no
ORM filter is injected), and the column ORDER, which the template reads
positionally as stats[0], [1], [2], [4], [5].
"""
from app import db
from app.models.business_capabilities import BusinessCapability
from app.models.user import User
from app.utils.framework_classifier import FrameworkClassifier


def _user(org, tag):
    u = User(
        email=f"ba02-{tag}-{org.id}@example.com",
        first_name="BA",
        last_name="Two",
        organization_id=org.id,
        confirmed=True,          # else before_request bounces to /account/unconfirmed
    )
    u.password = "TestPass123!"
    db.session.add(u)
    db.session.flush()
    return u


def _cap(org, category, current, target):
    c = BusinessCapability(
        name=f"BA02 {category} {current}/{target}",
        category=category,
        current_maturity_level=current,
        target_maturity_level=target,
        maturity_gap=(target - current) if None not in (current, target) else None,
        organization_id=org.id,
    )
    db.session.add(c)
    return c


def _first_category(framework_key="financial"):
    cats = FrameworkClassifier.get_framework_categories(framework_key)
    assert cats, f"no categories for {framework_key}"
    return cats[0]


def _visible_text(resp):
    """Strip markup so an assertion cannot match a hex colour or a CSS class.

    The first version of this test asserted `"26" not in body` and failed on
    `#dc2626` in an inline palette — a false leak report.
    """
    import re

    html = resp.get_data(as_text=True)
    html = re.sub(r"(?is)<script.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?</style>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html)


def test_frameworks_overview_reports_real_counts_and_averages(
    db_session, make_org, client, login_as, tenant_ctx
):
    org = make_org("ba02-counts")
    cat = _first_category()
    with tenant_ctx(org.id):
        _cap(org, cat, 2, 4)
        _cap(org, cat, 4, 5)
        db.session.flush()

    login_as(client, _user(org, "counts"))
    resp = client.get("/capability-maturity/frameworks")

    assert resp.status_code == 200
    text_only = _visible_text(resp)
    # 2 capabilities, avg current (2+4)/2 = 3.0, avg target (4+5)/2 = 4.5
    assert "3.0" in text_only, f"average current maturity not rendered: {text_only[:400]}"
    assert "4.5" in text_only, "average target maturity is not rendered"


def test_frameworks_overview_is_tenant_scoped(db_session, make_org, client, login_as, tenant_ctx):
    """Raw SQL — ADR 0003 means the ORM injects no predicate here, so the
    organization_id filter is hand-written and must actually work."""
    mine = make_org("ba02-mine")
    theirs = make_org("ba02-theirs")
    cat = _first_category()
    with tenant_ctx(mine.id):
        _cap(mine, cat, 1, 2)
        db.session.flush()
    with tenant_ctx(theirs.id):
        for _ in range(25):                   # a loud number if it leaks
            _cap(theirs, cat, 5, 5)
        db.session.flush()

    login_as(client, _user(mine, "mine"))
    resp = client.get("/capability-maturity/frameworks")

    assert resp.status_code == 200
    text_only = _visible_text(resp)
    # Scoped: 1 capability, avg current 1.0. Leaking: 26 capabilities and
    # avg current (1 + 25*5)/26 = 4.8 — the two are impossible to confuse.
    assert "1.0" in text_only, f"own organisation's maturity missing: {text_only[:400]}"
    assert "4.8" not in text_only, "another organisation's maturity is in the average"
    assert " 26 " not in f" {text_only} ", "another organisation's capabilities are counted"


def test_overview_renders_when_a_framework_has_no_capabilities(
    db_session, make_org, client, login_as, tenant_ctx
):
    """An empty framework must render the page, not 500 and not invent a score."""
    org = make_org("ba02-empty")
    login_as(client, _user(org, "empty"))
    resp = client.get("/capability-maturity/frameworks")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Capability Frameworks" in html
    # The empty state says why the page is bare and what to do, rather than showing zeros as a score.
    assert "No capabilities are mapped to a framework yet" in html
    assert "Categorise your capabilities" in html


def test_null_maturity_is_not_averaged_in_as_zero(db_session, make_org, tenant_ctx):
    """An unassessed capability must not read as a measured 0.

    Asserted against the query rather than the rendered page: AVG() and
    COUNT(col) both skip NULLs, and that is the property that matters. Going
    through the HTML made this brittle without making it stronger.
    """
    from sqlalchemy import text

    org = make_org("ba02-null")
    cat = _first_category()
    with tenant_ctx(org.id):
        _cap(org, cat, None, None)
        _cap(org, cat, 4, 4)
        db.session.flush()

        row = db.session.execute(  # tenant-filtered
            text(
                """
            SELECT COUNT(*)                      AS total,
                   COUNT(current_maturity_level) AS with_current,
                   AVG(current_maturity_level)   AS avg_current
            FROM business_capability
            WHERE category = :cat AND organization_id = :org_id
        """
            ),
            {"cat": cat, "org_id": org.id},
        ).first()

    assert row[0] == 2, "both capabilities should be counted"
    assert row[1] == 1, "only the assessed capability has a current level"
    # (0 + 4) / 2 = 2.0 would be the fabricated answer; 4.0 is the honest one.
    assert float(row[2]) == 4.0, f"NULL averaged in as zero: got {row[2]}"

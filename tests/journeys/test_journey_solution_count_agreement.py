"""Journey: the dashboard's "Total Solutions" and the Solutions list must answer the same question.

UX_IA_REVIEW.md finding 2 (Critical): /dashboard/health reported "Total Solutions: 2" while /solutions/
said "No solutions found" -- and ?status=all changed nothing. A new user who trusts the scorecard and
follows the sidebar link to open one of those solutions finds none, and concludes they should start over.

Root cause, traced before the fix. Two queries answered one question:

* the health scorecard counted EVERY Solution row (no role scope, no business-unit scope, `[DELETED]`
  rows and archived rows included);
* the list scoped to what the persona may open (an architect sees only their own, the enterprise
  roles see all), excluded `[DELETED]` rows, applied the business-unit scope, and by default hid
  archived rows and empty shells.

`?status=all` was never a recognised value: it filtered on `status == 'all'`, which matches nothing.

Asserted on what the routes hand their templates (template_rendered), not on scraped HTML, so a
markup change cannot make the comparison lie. Both screens are driven as the same logged-in user.
"""

import contextlib
import uuid

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

REAL_DESCRIPTION = "A design with a description comfortably longer than twenty characters."


@contextlib.contextmanager
def _captured(app):
    from flask import template_rendered

    seen = []

    def record(sender, template, context, **extra):
        seen.append((template.name, dict(context)))

    template_rendered.connect(record, app)
    try:
        yield seen
    finally:
        template_rendered.disconnect(record, app)


def _context(seen, template_name):
    matches = [ctx for name, ctx in seen if name == template_name]
    assert matches, "%s was not rendered (rendered: %s)" % (template_name, [n for n, _ in seen])
    return matches[-1]


@pytest.fixture
def portfolio(app):
    """One organisation: three personas, and five solutions covering every way a row can be excluded."""
    from app import db
    from app.models.solution_models import Solution

    with app.app_context():
        org_id = make_org(db, "SolCount")
        architect = make_user(db, org_id, "sa", "solution_architect", role_name="Architect")
        other = make_user(db, org_id, "other", "solution_architect", role_name="Architect")
        enterprise = make_user(db, org_id, "ea", "enterprise_architect", role_name="Architect")
        tag = uuid.uuid4().hex[:6]

        def add(owner, name, status="draft", description=REAL_DESCRIPTION):
            db.session.add(Solution(
                name="%s %s" % (name, tag), organization_id=org_id, created_by_id=owner,
                status=status, description=description,
            ))

        add(architect, "Own live design")
        add(architect, "Own archived design", status="archived")
        add(architect, "Own empty shell", description=None)
        add(architect, "[DELETED] Own removed design")
        add(other, "Someone else's design")
        db.session.commit()
    return {"architect": architect, "enterprise": enterprise}


# persona -> solutions that persona may open (non-deleted, role-scoped): the population both screens
# must report. The architect owns three non-deleted rows; the enterprise role sees those plus the
# other architect's row.
EXPECTED = [("architect", 3), ("enterprise", 4)]


@pytest.mark.parametrize("persona,expected", EXPECTED)
def test_dashboard_total_matches_the_solutions_list_total(app, client, portfolio, persona, expected):
    login(client, portfolio[persona])
    with _captured(app) as seen:
        assert client.get("/dashboard/health").status_code == 200
        assert client.get("/solutions/").status_code == 200

    tile = _context(seen, "dashboards/health.html")["total_solutions"]
    listed = _context(seen, "solutions/list.html")["stats"]["total"]
    assert tile == listed == expected, (
        "dashboard 'Total Solutions' = %r, Solutions list total = %r, expected %d for %s"
        % (tile, listed, expected, persona)
    )


@pytest.mark.parametrize("persona,expected", EXPECTED)
def test_status_all_shows_every_solution_the_persona_may_open(app, client, portfolio, persona, expected):
    login(client, portfolio[persona])
    with _captured(app) as seen:
        assert client.get("/solutions/?status=all").status_code == 200

    pagination = _context(seen, "solutions/list.html")["pagination"]
    assert pagination.total == expected, (
        "?status=all lists %d solution(s); the persona may open %d" % (pagination.total, expected)
    )


def test_default_list_says_how_many_it_is_hiding(app, client, portfolio):
    """The default view hides the archived row and the empty shell; it must say so, not imply 'all'."""
    login(client, portfolio["architect"])
    with _captured(app) as seen:
        assert client.get("/solutions/").status_code == 200

    ctx = _context(seen, "solutions/list.html")
    assert ctx["pagination"].total == 1
    assert ctx["hidden_by_default_filter"] == 2

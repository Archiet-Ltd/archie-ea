"""The ranked risk list: exposure from the one impact engine, plain-words reasons,
a bounded statement count, and the risk page fenced to its own organisation."""

import uuid

import pytest
from sqlalchemy import event

pytestmark = pytest.mark.usefixtures("db_session")


def _element(db_session, org_id, name):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _serves(db_session, org_id, source, target):
    from app.models.archimate_core import ArchiMateRelationship

    db_session.add(ArchiMateRelationship(
        type="Serving", source_id=source.id, target_id=target.id, organization_id=org_id))
    db_session.flush()


def _risk(db_session, org_id, title, likelihood, impact, element=None):
    from app.models.risk import Risk

    row = Risk(title=title, likelihood=likelihood, impact=impact, organization_id=org_id,
               archimate_element_id=element.id if element is not None else None)
    db_session.add(row)
    db_session.flush()
    return row


def _world(db_session, make_org):
    """Wide reaches 3 elements, three risks tie on it, Narrow reaches 1, and one risk connects to nothing."""
    org, other = make_org("a"), make_org("b")
    wide = _element(db_session, org.id, "Wide")
    narrow = _element(db_session, org.id, "Narrow")
    for name in ("d1", "d2", "d3"):
        _serves(db_session, org.id, wide, _element(db_session, org.id, name))
    _serves(db_session, org.id, narrow, _element(db_session, org.id, "d4"))
    risks = {
        "tie_low": _risk(db_session, org.id, "Bravo tie", 2, 2, wide),
        "tie_high": _risk(db_session, org.id, "Alpha tie", 4, 5, wide),
        "tie_same": _risk(db_session, org.id, "Zulu same", 2, 2, wide),
        "narrow": _risk(db_session, org.id, "Narrow risk", 5, 5, narrow),
        "bare": _risk(db_session, org.id, "Bare risk", 3, 3),
    }
    foreign_el = _element(db_session, other.id, "Foreign")
    foreign = _risk(db_session, other.id, "Foreign risk", 5, 5, foreign_el)
    return org, other, risks, foreign


def test_ranking_order_ties_and_unlinked(db_session, make_org, tenant_ctx):
    from app.services.risk_service import ranked_risks

    org, _other, risks, foreign = _world(db_session, make_org)
    with tenant_ctx(org.id):
        result = ranked_risks()
    titles = [r["title"] for r in result["rows"]]
    # Wide reaches 3: ties broken by score (Alpha 20), then title (Bravo before Zulu); Narrow reaches 1;
    # the risk with no element follows. The other organisation's risk never appears.
    assert titles == ["Alpha tie", "Bravo tie", "Zulu same", "Narrow risk", "Bare risk"]
    assert foreign.id not in {r["risk_id"] for r in result["rows"]}
    assert [r["rank"] for r in result["rows"]] == [1, 2, 3, 4, 5]
    assert result["total"] == 5


def test_reasons_are_plain_words(db_session, make_org, tenant_ctx):
    from app.services.risk_service import RANKING_RULE, ranked_risks

    org, _other, _risks, _foreign = _world(db_session, make_org)
    with tenant_ctx(org.id):
        result = ranked_risks()
    by_title = {r["title"]: r for r in result["rows"]}
    assert by_title["Alpha tie"]["reason"] == "Ranked 1 of 5: reaches 3 elements, likelihood 4 of 5, impact 5 of 5"
    assert by_title["Narrow risk"]["reason"] == "Ranked 4 of 5: reaches 1 element, likelihood 5 of 5, impact 5 of 5"
    bare = by_title["Bare risk"]
    assert bare["exposure"] is None
    assert bare["reason"].startswith("Ranked 5 of 5: no linked elements recorded")
    assert "recorded score of 9" in bare["reason"]
    assert RANKING_RULE == result["rule"]


def test_risk_with_missing_element_is_not_counted_as_zero(db_session, make_org, tenant_ctx):
    from app.services.risk_service import ranked_risks

    org, other = make_org("a"), make_org("b")
    foreign_el = _element(db_session, other.id, "Foreign element")
    risk = _risk(db_session, org.id, "Points elsewhere", 3, 3, foreign_el)
    with tenant_ctx(org.id):
        (row,) = ranked_risks()["rows"]
    assert row["risk_id"] == risk.id
    assert row["exposure"] is None
    assert "could not be worked out" in row["reason"]


def _count_statements(db_session, fn):
    from app import db

    counter = {"n": 0}
    engine = db.session.get_bind()

    def _hook(*_a, **_k):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _hook)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _hook)
    return counter["n"]


def test_statement_count_does_not_grow_with_the_number_of_risks(db_session, make_org, tenant_ctx):
    from app.services.risk_service import ranked_risks

    org = make_org("a")
    root = _element(db_session, org.id, "Root")
    _serves(db_session, org.id, root, _element(db_session, org.id, "Leaf"))
    _risk(db_session, org.id, "First", 3, 3, root)
    with tenant_ctx(org.id):
        small = _count_statements(db_session, ranked_risks)
    for i in range(12):
        _risk(db_session, org.id, "More %d" % i, 2, 2, root)
    with tenant_ctx(org.id):
        large = _count_statements(db_session, ranked_risks)
    assert large == small, (small, large)
    assert large <= 30


def test_risk_page_shows_reason_and_is_404_for_another_organisation(
    db_session, make_org, login_as, client
):
    from app import db
    from app.models.user import User

    org, other, risks, foreign = _world(db_session, make_org)
    user = User(email="r-%s@example.com" % uuid.uuid4().hex[:8], first_name="R", last_name="T",
                organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
    db.session.add(user)
    db.session.flush()
    login_as(client, user)
    own = client.get("/risks/%d" % risks["tie_high"].id)
    assert own.status_code == 200
    assert b"Ranked 1 of 5: reaches 3 elements" in own.data
    login_as(client, user)
    assert client.get("/risks/%d" % foreign.id).status_code == 404
    login_as(client, user)
    listing = client.get("/risks/")
    assert listing.status_code == 200
    assert b"Foreign risk" not in listing.data
    assert b"Top risks (5)" in listing.data


from app import db  # noqa: E402


def _app(db_session, org_id, name, element=None):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org_id,
                               archimate_element_id=element.id if element is not None else None)
    db_session.add(row)
    db_session.flush()
    if element is None:
        # The shared backbone gives every application an element on flush; a cleared pointer
        # (an element deleted or detached later) is written straight to the table.
        db_session.execute(db.text("UPDATE application_components SET archimate_element_id = NULL WHERE id = :i"),
                           {"i": row.id})
        db_session.expire(row)
    return row


def _map(db_session, org_id, risk, component):
    from app.models.risk_entity_link import RiskEntityLink

    db_session.add(RiskEntityLink(risk_id=risk.id, entity_type="application", entity_id=component.id,
                                  organization_id=org_id))
    db_session.flush()


def _only_row(org_id, tenant_ctx):
    from app.services.risk_service import ranked_risks

    with tenant_ctx(org_id):
        (row,) = ranked_risks()["rows"]
    return row


def test_mapping_an_already_reached_application_does_not_lower_exposure(db_session, make_org, tenant_ctx):
    org = make_org("a")
    root, shared = _element(db_session, org.id, "Root"), _element(db_session, org.id, "Shared app")
    _serves(db_session, org.id, root, shared)
    _serves(db_session, org.id, shared, _element(db_session, org.id, "Leaf"))
    risk = _risk(db_session, org.id, "Reaches shared", 3, 3, root)
    before = _only_row(org.id, tenant_ctx)["exposure"]
    assert before == 2
    _map(db_session, org.id, risk, _app(db_session, org.id, "Shared app", shared))
    assert _only_row(org.id, tenant_ctx)["exposure"] >= before


def test_distance_is_measured_from_each_seed(db_session, make_org, tenant_ctx):
    from app.services.risk_service import ranked_risks

    org = make_org("a")
    a, b = _element(db_session, org.id, "A"), _element(db_session, org.id, "B")
    hop1, hop2, target = (_element(db_session, org.id, n) for n in ("h1", "h2", "Target"))
    _serves(db_session, org.id, a, hop1)
    _serves(db_session, org.id, hop1, hop2)
    _serves(db_session, org.id, hop2, target)   # 3 hops from A
    _serves(db_session, org.id, b, target)      # 1 hop from B
    alone = _risk(db_session, org.id, "Only A", 3, 3, a)
    both = _risk(db_session, org.id, "A and B", 3, 3, a)
    _map(db_session, org.id, both, _app(db_session, org.id, "B app", b))
    with tenant_ctx(org.id):
        rows = {r["title"]: r for r in ranked_risks()["rows"]}
    # A and B are asked about in one call; Target is one hop from B but three from A,
    # so for the risk that only has A it must sort after h1 and h2.
    touches = [t["name"] for t in rows["Only A"]["touches"]]
    assert touches.index("h1") < touches.index("h2") < touches.index("Target")
    assert alone.id == rows["Only A"]["risk_id"]


def test_application_with_no_element_is_not_skipped(db_session, make_org, tenant_ctx):
    org = make_org("a")
    root = _element(db_session, org.id, "Root")
    _serves(db_session, org.id, root, _element(db_session, org.id, "Leaf"))
    risk = _risk(db_session, org.id, "Half known", 3, 3, root)
    _map(db_session, org.id, risk, _app(db_session, org.id, "Resolvable", _element(db_session, org.id, "Mirror")))
    _map(db_session, org.id, risk, _app(db_session, org.id, "Unmirrored"))
    row = _only_row(org.id, tenant_ctx)
    assert row["exposure"] is None
    assert row["worked_out"] is False
    assert "could not be worked out" in row["reason"]
    # An application with no element and no other link is not "no linked elements" either.
    risk2 = _risk(db_session, org.id, "Only unmirrored", 1, 1)
    _map(db_session, org.id, risk2, _app(db_session, org.id, "Unmirrored two"))
    from app.services.risk_service import ranked_risks
    with tenant_ctx(org.id):
        by_title = {r["title"]: r for r in ranked_risks()["rows"]}
    assert "could not be worked out" in by_title["Only unmirrored"]["reason"]


def test_statement_count_with_distinct_mirrors_grows_per_risk(db_session, make_org, tenant_ctx):
    """Measured, not hidden: each distinct seed costs one walk in the impact engine."""
    from app.services.risk_service import ranked_risks

    org = make_org("a")

    def add(n):
        for i in range(n):
            el = _element(db_session, org.id, "Mirror %d-%d" % (n, i))
            _serves(db_session, org.id, el, _element(db_session, org.id, "Leaf %d-%d" % (n, i)))
            _risk(db_session, org.id, "Risk %d-%d" % (n, i), 2, 2, el)

    add(1)
    with tenant_ctx(org.id):
        one = _count_statements(db_session, ranked_risks)
    add(9)
    with tenant_ctx(org.id):
        ten = _count_statements(db_session, ranked_risks)
    per_risk = (ten - one) / 9
    print("statements: 1 risk=%d, 10 risks=%d, per extra risk=%.1f" % (one, ten, per_risk))
    assert per_risk <= 14, per_risk


def test_risk_page_does_not_claim_no_links_when_it_could_not_read_them(
    db_session, make_org, login_as, client
):
    from app.models.user import User

    org = make_org("a")
    risk = _risk(db_session, org.id, "Unreadable links", 3, 3)
    _map(db_session, org.id, risk, _app(db_session, org.id, "Unmirrored"))
    user = User(email="u-%s@example.com" % uuid.uuid4().hex[:8], first_name="R", last_name="T",
                organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
    db.session.add(user)
    db.session.flush()
    login_as(client, user)
    page = client.get("/risks/%d" % risk.id)
    assert page.status_code == 200
    assert b"could not be worked out" in page.data
    assert b"No linked elements recorded" not in page.data

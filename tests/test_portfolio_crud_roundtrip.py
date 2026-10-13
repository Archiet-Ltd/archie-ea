"""End-to-end CRUD tests for the portfolio write paths.

WHY THIS FILE EXISTS
--------------------
tests/test_portfolio_delivery_chain.py asserts model shape, FK targets, derived
properties and route *registration*. None of that proves a form works. Three of
its assertions were worse than useless: they used inspect.getsource() to check a
string appears in the handler, which passes even when the code path never runs.
Those are deleted; this file replaces them with real requests.

A CRUD form that reports success and saves nothing is the same failure this
whole change set was written to remove — a UI asserting something the data does
not support. So every test here dispatches a genuine HTTP request through a
logged-in session and then re-queries the database to see what actually landed.

Conventions borrowed from tests/test_ba_tenant_and_authz.py, for the reasons
documented there at length:
  - Login must clear flask_login's g._login_user cache, or a second login in
    the same app context silently keeps the first user and every cross-tenant
    assertion exercises the wrong actor. Use the shared ``login_as`` fixture
    (tests/conftest.py) for this — it does exactly that.
  - Helpers hand back plain ids, never live ORM instances, so a test can carry
    an id across a request/response boundary without caring which session
    last touched the row.
  - Only redirect-only form endpoints are exercised over HTTP; routes that
    render layouts/admin_base.html are checked separately for status only,
    since full-page rendering pulls in sidebar context processors this suite
    does not otherwise exercise.

Fixtures: this file used to hand-roll its own module-scoped ``app``/``client``,
its own ``_login``, and its own ``_make_org_id`` — all committing real rows
with no cleanup, and all duplicating fixtures tests/conftest.py already
provides for exactly this purpose. It now uses the shared ``db_session``,
``make_org`` and ``login_as`` fixtures throughout: every row a test creates
sits inside the per-test savepoint that ``db_session`` always rolls back, so
nothing survives the test regardless of how it ends. The per-request round
trip through ``client.post(...)`` still behaves like a real commit from the
app's point of view (that is what ``db_session``'s savepoint join is for) —
only the outer, test-owning transaction is discarded.

One rule that matters for anyone adding a ``with app.app_context(): ...``
block of their own: that block opens its own, separate database session (see
``db_session`` and ``flask_sqlalchemy``'s per-app-context session scope) — a
write made inside it is invisible everywhere else, including to the test's own
``db_session``-held identity map, until that block's session commits before
the block exits. An object read earlier through ``db_session`` can also still
look stale afterwards (its attributes were cached before the nested block's
commit); call ``db_session.expire_all()`` once the nested block is done, or
re-query through ``db_session``, before relying on the mutated value again.
"""
import uuid

import pytest


def _make_user_id(db_session, org_id, label):
    """A user pinned explicitly to org_id.

    The User.before_insert listener reassigns an unset organization_id to the
    shared default org, which would defeat the isolation tests below. No
    canonical factory for this exists in tests/conftest.py (only ``make_org``
    does), so this stays a local helper.
    """
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        # Without this every request redirects to /account/unconfirmed before
        # reaching the handler — which is how a first run of this file appeared
        # to show a cross-tenant write succeeding (302) when in fact nothing had
        # executed at all.
        confirmed=True,
    )
    if hasattr(user, "set_password"):
        user.set_password("x" * 12)
    db_session.add(user)
    db_session.flush()
    return user.id


def _make_initiative_id(db_session, org_id, name="Portfolio CRUD Initiative"):
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    init = EnterpriseInitiative(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org_id)
    db_session.add(init)
    db_session.flush()
    return init.id


def _make_programme_id(db_session, org_id, owner_id, name="Portfolio CRUD Programme"):
    from app.models.strategic import StrategicInitiative

    programme = StrategicInitiative(
        name=f"{name} {uuid.uuid4().hex[:6]}",
        organization_id=org_id,
        owner_id=owner_id,
        record_kind="transformation_programme",
    )
    db_session.add(programme)
    db_session.flush()
    return programme.id


@pytest.fixture
def org_a(db_session, make_org):
    org = make_org("CrudA")
    user_id = _make_user_id(db_session, org.id, "CrudA")
    return {
        "org_id": org.id,
        "user_id": user_id,
        "initiative_id": _make_initiative_id(db_session, org.id),
        "programme_id": _make_programme_id(db_session, org.id, user_id),
    }


# ==========================================================================
# Demand: submit -> appears -> decide
# ==========================================================================

class TestDemandIntake:
    def test_submitting_a_demand_creates_a_row(self, app, client, org_a, login_as):
        from app import db
        from app.models.demand import Demand

        login_as(client, org_a["user_id"])
        title = f"Replace supplier onboarding {uuid.uuid4().hex[:6]}"

        resp = client.post("/portfolio/demands/new", data={
            "title": title,
            "description": "Manual today, 3 days per supplier.",
            "source": "business_unit",
            "business_value_score": "4",
            "urgency_score": "3",
        }, follow_redirects=False)

        assert resp.status_code in (302, 303), resp.status_code

        with app.app_context():
            row = db.session.query(Demand).filter_by(title=title).one_or_none()
            assert row is not None, "form redirected but saved nothing"
            assert row.status == "submitted"
            assert row.business_value_score == 4
            assert row.priority_score == 12
            assert row.organization_id == org_a["org_id"]
            db.session.delete(row)
            db.session.commit()

    def test_submitting_without_a_title_saves_nothing(self, app, client, org_a, login_as):
        """Validation must refuse, not silently drop the row."""
        from app import db
        from app.models.demand import Demand

        login_as(client, org_a["user_id"])
        with app.app_context():
            before = db.session.query(Demand).count()

        resp = client.post("/portfolio/demands/new", data={"title": "   "})
        assert resp.status_code == 400

        with app.app_context():
            assert db.session.query(Demand).count() == before

    def test_blank_scores_are_stored_as_null_not_zero(self, app, client, org_a, login_as):
        """0 and "not given" are different facts; the form must not conflate them."""
        from app import db
        from app.models.demand import Demand

        login_as(client, org_a["user_id"])
        title = f"No scores {uuid.uuid4().hex[:6]}"
        client.post("/portfolio/demands/new", data={
            "title": title, "business_value_score": "", "urgency_score": "",
        })

        with app.app_context():
            row = db.session.query(Demand).filter_by(title=title).one()
            assert row.business_value_score is None
            assert row.urgency_score is None
            assert row.priority_score is None
            db.session.delete(row)
            db.session.commit()

    def test_approving_records_the_decision(self, app, client, org_a, login_as):
        from app import db
        from app.models.demand import Demand

        with app.app_context():
            d = Demand(title=f"Approve me {uuid.uuid4().hex[:6]}",
                       organization_id=org_a["org_id"], status="submitted")
            db.session.add(d)
            db.session.commit()
            did = d.id

        login_as(client, org_a["user_id"])
        client.post(f"/portfolio/demands/{did}/decide",
                    data={"status": "approved", "decision_rationale": "Funded in Q3."})

        with app.app_context():
            row = db.session.get(Demand, did)
            assert row.status == "approved"
            assert row.decision_date is not None
            assert row.triaged_by_id == org_a["user_id"]
            db.session.delete(row)
            db.session.commit()

    def test_declining_without_a_rationale_is_refused(self, app, client, org_a, login_as):
        """An unexplained decline is the one that returns next quarter."""
        from app import db
        from app.models.demand import Demand

        with app.app_context():
            d = Demand(title=f"Decline me {uuid.uuid4().hex[:6]}",
                       organization_id=org_a["org_id"], status="submitted")
            db.session.add(d)
            db.session.commit()
            did = d.id

        login_as(client, org_a["user_id"])
        client.post(f"/portfolio/demands/{did}/decide", data={"status": "declined"})

        with app.app_context():
            row = db.session.get(Demand, did)
            assert row.status == "submitted", "declined without a rationale"
            assert row.decision_date is None
            db.session.delete(row)
            db.session.commit()


# ==========================================================================
# Benefit: create -> measure
# ==========================================================================

class TestBenefitLifecycle:
    def test_creating_a_benefit_persists_the_baseline(self, app, client, org_a, login_as):
        from app import db
        from app.models.benefit import Benefit

        login_as(client, org_a["user_id"])
        name = f"Retire duplicate licences {uuid.uuid4().hex[:6]}"

        client.post(f"/portfolio/programmes/{org_a['programme_id']}/benefits", data={
            "name": name, "benefit_type": "cost_saving", "unit": "GBP",
            "baseline_value": "100000", "target_value": "60000",
        })

        with app.app_context():
            row = db.session.query(Benefit).filter_by(name=name).one_or_none()
            assert row is not None, "benefit form saved nothing"
            assert float(row.baseline_value) == 100000
            assert row.status == "identified"
            assert row.realisation_percentage is None, "no actual yet -> must be None"
            assert row.strategic_initiative_id == org_a["programme_id"]
            assert row.legacy_enterprise_initiative_id is None
            db.session.delete(row)
            db.session.commit()

    def test_detail_form_selected_programme_creates_canonical_benefit(
        self, app, client, org_a, login_as
    ):
        from app import db
        from app.models.benefit import Benefit

        login_as(client, org_a["user_id"])
        name = f"Selected programme benefit {uuid.uuid4().hex[:6]}"
        response = client.post(
            "/portfolio/programmes/benefits",
            data={"programme_id": org_a["programme_id"], "name": name},
        )

        assert response.status_code in (302, 303)
        with app.app_context():
            row = db.session.query(Benefit).filter_by(name=name).one_or_none()
            assert row is not None
            assert row.strategic_initiative_id == org_a["programme_id"]
            assert row.legacy_enterprise_initiative_id is None
            db.session.delete(row)
            db.session.commit()

    def test_measuring_writes_the_actual_and_computes_realisation(
        self, app, client, org_a, login_as
    ):
        from app import db
        from app.models.benefit import Benefit

        with app.app_context():
            b = Benefit(name=f"Measure me {uuid.uuid4().hex[:6]}",
                        strategic_initiative_id=org_a["programme_id"], organization_id=org_a["org_id"],
                        baseline_value=100, target_value=50, status="identified")
            db.session.add(b)
            db.session.commit()
            bid = b.id

        login_as(client, org_a["user_id"])
        client.post(f"/portfolio/benefits/{bid}/measure", data={"actual_value": "75"})

        with app.app_context():
            row = db.session.get(Benefit, bid)
            assert float(row.actual_value) == 75
            assert row.realisation_percentage == 50.0
            assert row.status == "realising", "half-achieved must not claim realised"
            db.session.delete(row)
            db.session.commit()

    def test_status_becomes_realised_only_when_target_is_met(self, app, client, org_a, login_as):
        from app import db
        from app.models.benefit import Benefit

        with app.app_context():
            b = Benefit(name=f"Full {uuid.uuid4().hex[:6]}",
                        strategic_initiative_id=org_a["programme_id"], organization_id=org_a["org_id"],
                        baseline_value=100, target_value=50, status="identified")
            db.session.add(b)
            db.session.commit()
            bid = b.id

        login_as(client, org_a["user_id"])
        client.post(f"/portfolio/benefits/{bid}/measure", data={"actual_value": "50"})

        with app.app_context():
            row = db.session.get(Benefit, bid)
            assert row.realisation_percentage == 100.0
            assert row.status == "realised"
            db.session.delete(row)
            db.session.commit()

    def test_measurement_without_a_value_changes_nothing(self, app, client, org_a, login_as):
        from app import db
        from app.models.benefit import Benefit

        with app.app_context():
            b = Benefit(name=f"Blank {uuid.uuid4().hex[:6]}",
                        strategic_initiative_id=org_a["programme_id"], organization_id=org_a["org_id"],
                        baseline_value=100, target_value=50, status="identified")
            db.session.add(b)
            db.session.commit()
            bid = b.id

        login_as(client, org_a["user_id"])
        client.post(f"/portfolio/benefits/{bid}/measure", data={"actual_value": ""})

        with app.app_context():
            row = db.session.get(Benefit, bid)
            assert row.actual_value is None
            assert row.status == "identified"
            db.session.delete(row)
            db.session.commit()


# ==========================================================================
# Assumption: log -> resolve
# ==========================================================================

class TestAssumptionLifecycle:
    def test_logging_an_assumption_persists_exposure_inputs(self, app, client, org_a, login_as):
        from app import db
        from app.models.demand import Assumption

        login_as(client, org_a["user_id"])
        statement = f"Vendor API supports bulk export {uuid.uuid4().hex[:6]}"

        client.post(f"/portfolio/initiatives/{org_a['initiative_id']}/assumptions", data={
            "statement": statement, "impact_if_false": "4", "confidence": "2",
        })

        with app.app_context():
            row = db.session.query(Assumption).filter_by(statement=statement).one_or_none()
            assert row is not None, "assumption form saved nothing"
            assert row.status == "open"
            assert row.exposure == 16  # 4 * (6 - 2)
            db.session.delete(row)
            db.session.commit()

    def test_invalidating_keeps_the_row_and_the_note(self, app, client, org_a, login_as):
        """The assumption that proved false is the useful entry in the log."""
        from app import db
        from app.models.demand import Assumption

        with app.app_context():
            a = Assumption(statement=f"Wrong {uuid.uuid4().hex[:6]}",
                           initiative_id=org_a["initiative_id"],
                           organization_id=org_a["org_id"], status="open")
            db.session.add(a)
            db.session.commit()
            aid = a.id

        login_as(client, org_a["user_id"])
        client.post(f"/portfolio/assumptions/{aid}/resolve",
                    data={"status": "invalidated", "note": "Vendor confirmed no bulk export."})

        with app.app_context():
            row = db.session.get(Assumption, aid)
            assert row is not None, "invalidating must not delete the record"
            assert row.status == "invalidated"
            assert row.invalidated_note
            assert row.invalidated_date is not None
            db.session.delete(row)
            db.session.commit()

    def test_invalidating_without_a_note_is_refused(self, app, client, org_a, login_as):
        from app import db
        from app.models.demand import Assumption

        with app.app_context():
            a = Assumption(statement=f"NoNote {uuid.uuid4().hex[:6]}",
                           initiative_id=org_a["initiative_id"],
                           organization_id=org_a["org_id"], status="open")
            db.session.add(a)
            db.session.commit()
            aid = a.id

        login_as(client, org_a["user_id"])
        client.post(f"/portfolio/assumptions/{aid}/resolve", data={"status": "invalidated"})

        with app.app_context():
            row = db.session.get(Assumption, aid)
            assert row.status == "open"
            assert row.invalidated_date is None
            db.session.delete(row)
            db.session.commit()


# ==========================================================================
# Tenant isolation on the write paths
# ==========================================================================

class TestWritePathTenantIsolation:
    def test_cannot_add_a_benefit_to_another_orgs_initiative(
        self, app, client, org_a, db_session, make_org, login_as
    ):
        """The check that stops one tenant writing into another's programme."""
        from app import db
        from app.models.benefit import Benefit

        org_b = make_org("CrudB")
        attacker = _make_user_id(db_session, org_b.id, "CrudB")

        login_as(client, attacker)
        name = f"Injected {uuid.uuid4().hex[:6]}"
        resp = client.post(f"/portfolio/programmes/{org_a['programme_id']}/benefits",
                           data={"name": name})

        assert resp.status_code == 404, (
            f"org B reached org A's initiative (got {resp.status_code})"
        )
        with app.app_context():
            assert db.session.query(Benefit).filter_by(name=name).one_or_none() is None

    def test_legacy_initiative_benefit_write_blocked_until_linked(
        self, app, client, org_a, login_as
    ):
        """Unlinked, the legacy URL still refuses to write — see
        test_legacy_initiative_benefit_write_works_once_linked for the bridged
        path (2 Sep 2026, EnterpriseInitiative.linked_strategic_initiative_id)."""
        from app import db
        from app.models.benefit import Benefit

        login_as(client, org_a["user_id"])
        name = f"Legacy write blocked {uuid.uuid4().hex[:6]}"
        response = client.post(
            f"/portfolio/initiatives/{org_a['initiative_id']}/benefits",
            data={"name": name},
        )

        assert response.status_code == 409
        with app.app_context():
            assert db.session.query(Benefit).filter_by(name=name).one_or_none() is None

    def test_legacy_initiative_benefit_write_works_once_linked(
        self, app, client, org_a, db_session, login_as
    ):
        """The bridge this session added: once a human confirms this legacy
        EnterpriseInitiative and a StrategicInitiative describe the same real
        programme, the legacy URL routes through to that programme instead of
        permanently refusing."""
        from app import db
        from app.models.benefit import Benefit
        from app.models.vendor.vendor_organization import EnterpriseInitiative

        with app.app_context():
            initiative = db.session.get(EnterpriseInitiative, org_a["initiative_id"])
            initiative.linked_strategic_initiative_id = org_a["programme_id"]
            db.session.commit()
        # The block above wrote through its own, separate nested session (see
        # the module docstring). Without this, db_session's identity map can
        # still hand back the pre-link EnterpriseInitiative object it already
        # held, and the assertions below would only happen to pass because
        # login_as's own commit (via mint_test_sid) coincidentally expires it
        # first — order-dependent and not something to rely on.
        db_session.expire_all()

        login_as(client, org_a["user_id"])
        name = f"Bridged benefit {uuid.uuid4().hex[:6]}"
        response = client.post(
            f"/portfolio/initiatives/{org_a['initiative_id']}/benefits",
            data={"name": name},
        )

        assert response.status_code == 302, response.status_code
        with app.app_context():
            benefit = db.session.query(Benefit).filter_by(name=name).one_or_none()
            assert benefit is not None
            assert benefit.strategic_initiative_id == org_a["programme_id"]

    def test_link_programme_round_trips_via_form_and_page(self, app, client, org_a, login_as):
        """The picker itself: link, then confirm the link is visible both via
        an independent re-fetch and via the actual rendered detail page."""
        from app import db
        from app.models.vendor.vendor_organization import EnterpriseInitiative

        login_as(client, org_a["user_id"])
        response = client.post(
            f"/portfolio/initiatives/{org_a['initiative_id']}/link-programme",
            data={"programme_id": org_a["programme_id"]},
        )
        assert response.status_code == 302, response.status_code

        with app.app_context():
            initiative = db.session.get(EnterpriseInitiative, org_a["initiative_id"])
            assert initiative.linked_strategic_initiative_id == org_a["programme_id"]

        page = client.get(f"/portfolio/{org_a['initiative_id']}")
        assert page.status_code == 200
        assert b"Remove link" in page.data


# ==========================================================================
# The read pages actually render
#
# Template parsing was already gated; parsing is not rendering. A page can parse
# and still 500 on an undefined variable, a bad filter argument or a None that
# reaches a comparison. These render the real templates with real rows.
# ==========================================================================

class TestPagesRender:
    def test_portfolio_index_renders(self, app, client, org_a, login_as):
        login_as(client, org_a["user_id"])
        resp = client.get("/portfolio/")
        assert resp.status_code == 200, resp.status_code

    def test_initiative_detail_renders(self, app, client, org_a, login_as):
        login_as(client, org_a["user_id"])
        resp = client.get(f"/portfolio/{org_a['initiative_id']}")
        assert resp.status_code == 200, resp.status_code

    def test_initiative_detail_uses_canonical_programme_benefit_form(
        self, app, client, org_a, login_as
    ):
        login_as(client, org_a["user_id"])
        response = client.get(f"/portfolio/{org_a['initiative_id']}")

        assert response.status_code == 200
        assert b'action="/portfolio/programmes/benefits"' in response.data
        assert (
            f'action="/portfolio/initiatives/{org_a["initiative_id"]}/benefits"'.encode()
            not in response.data
        )
        assert b'name="programme_id"' in response.data
        assert f'value="{org_a["programme_id"]}"'.encode() in response.data

    def test_initiative_detail_without_programme_has_honest_unavailable_state(
        self, app, client, org_a, db_session, login_as
    ):
        from app import db
        from app.models.strategic import StrategicInitiative

        with app.app_context():
            programme = db.session.get(StrategicInitiative, org_a["programme_id"])
            programme.record_kind = None
            db.session.commit()
        # Same reason as test_legacy_initiative_benefit_write_works_once_linked
        # above: this mutation ran through its own separate nested session.
        db_session.expire_all()

        login_as(client, org_a["user_id"])
        response = client.get(f"/portfolio/{org_a['initiative_id']}")

        assert response.status_code == 200
        assert b'action="/portfolio/programmes/benefits"' not in response.data
        assert (
            f'action="/portfolio/initiatives/{org_a["initiative_id"]}/benefits"'.encode()
            not in response.data
        )
        assert b"no transformation programme is available" in response.data.lower()

    def test_demand_queue_and_form_render(self, app, client, org_a, login_as):
        login_as(client, org_a["user_id"])
        assert client.get("/portfolio/demands").status_code == 200
        assert client.get("/portfolio/demands/new").status_code == 200

    def test_detail_renders_with_a_measured_benefit_and_open_assumption(
        self, app, client, org_a, login_as
    ):
        """The populated path — em dashes, badges and the resolve form all engage."""
        from app import db
        from app.models.benefit import Benefit
        from app.models.demand import Assumption

        with app.app_context():
            b = Benefit(name=f"Rendered {uuid.uuid4().hex[:6]}",
                        strategic_initiative_id=org_a["programme_id"], organization_id=org_a["org_id"],
                        benefit_type="cost_saving", baseline_value=100, target_value=50,
                        actual_value=75, status="realising")
            a = Assumption(statement=f"Rendered {uuid.uuid4().hex[:6]}",
                           initiative_id=org_a["initiative_id"], organization_id=org_a["org_id"],
                           impact_if_false=4, confidence=2, status="open")
            db.session.add_all([b, a])
            db.session.commit()
            bid, aid = b.id, a.id

        login_as(client, org_a["user_id"])
        resp = client.get(f"/portfolio/{org_a['initiative_id']}")
        assert resp.status_code == 200, resp.status_code

        with app.app_context():
            for model, rid in ((Benefit, bid), (Assumption, aid)):
                row = db.session.get(model, rid)
                if row:
                    db.session.delete(row)
            db.session.commit()

    def test_index_renders_when_an_initiative_has_no_figures(
        self, app, client, org_a, db_session, login_as
    ):
        """The em-dash path: nulls must not raise on a comparison or a format."""
        bare = _make_initiative_id(db_session, org_a["org_id"], name="Bare")

        login_as(client, org_a["user_id"])
        assert client.get("/portfolio/").status_code == 200
        assert client.get(f"/portfolio/{bare}").status_code == 200

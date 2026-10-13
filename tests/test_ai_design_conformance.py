"""R1-B55: AI design conformance review.

A submitted design is checked against the approved pattern list, the fencing
rule and the oversight requirements; deviations are listed, never silently
passed; the board's decision is an ArchitectureDecision row linked to the AI
system; one organisation's reviews are invisible to another.

Uses the shared fixtures in tests/conftest.py (db_session rolls back).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.architecture_decision import ArchitectureDecision
from app.modules.ai_chat.services import ai_design_conformance_service as svc
from app.modules.enterprise_genome.services.ai_systems_seed import register_ai_system

CLEAN = {
    "pattern": "human-in-the-loop-copilot",
    "ingests_untrusted_content": False,
    "untrusted_input_fenced": None,
}


def _user(db_session, org, role_name, label, enterprise_role=None):
    from app.models.user import Role, User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        pytest.skip(f"no {role_name} role seeded")
    u = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.test", first_name=label,
        organization_id=org.id, confirmed=True, role=role, enterprise_role=enterprise_role,
    )
    u.password = uuid.uuid4().hex
    db_session.add(u)
    db_session.flush()
    return u


def _system(db_session, org, *, owner=None, **profile):
    """An AI system with a fully governed profile unless the test overrides it."""
    fields = dict(
        provider="anthropic", model_id="claude-opus-5", autonomy_level="human-in-loop",
        data_sensitivity="internal", approval_gate=True, human_review=True,
    )
    fields.update(profile)
    el = register_ai_system(db_session, org.id, name=f"Design System {uuid.uuid4().hex[:6]}", **fields)
    db_session.flush()
    if owner is not None:
        from app.services.element_ownership_service import set_element_owner

        set_element_owner(element_type="ai_system", element_id=el.id, user_id=owner.id,
                          organization_id=org.id, assigned_by=owner.id)
        db_session.flush()
    return el


def _submit(el, org, submitter, declaration=None, title="Design under review"):
    return svc.submit_design(
        element_id=el.id, organization_id=org.id, submitter_id=submitter.id, title=title,
        context="How the system is built.", declaration=declaration or dict(CLEAN),
    )


def _codes(deviations):
    return {d["code"] for d in deviations}


def _raw_status(db_session, review_id):
    return db_session.execute(
        text("SELECT status FROM architecture_decisions WHERE id = :i"), {"i": review_id}
    ).scalar_one()


# --- Deviations are listed, not silently passed --------------------------------


def test_a_clean_design_lists_no_deviation(db_session, make_org):
    org = make_org("dc1")
    owner = _user(db_session, org, "Architect", "own")
    el = _system(db_session, org, owner=owner)
    assert svc.review_deviations(el, dict(CLEAN)) == []


def test_a_fencing_deviation_is_listed(db_session, make_org):
    org = make_org("dc2")
    owner = _user(db_session, org, "Architect", "own")
    el = _system(db_session, org, owner=owner)
    deviations = svc.review_deviations(el, {**CLEAN, "ingests_untrusted_content": True,
                                            "untrusted_input_fenced": False})
    assert _codes(deviations) == {"unfenced-untrusted-input"}
    assert deviations[0]["area"] == "fencing"


def test_unanswered_fencing_is_listed_not_assumed_safe(db_session, make_org):
    org = make_org("dc3")
    owner = _user(db_session, org, "Architect", "own")
    el = _system(db_session, org, owner=owner)
    deviations = svc.review_deviations(el, {"pattern": CLEAN["pattern"]})
    assert _codes(deviations) == {"fencing-not-declared"}


def test_fenced_untrusted_input_passes_the_fencing_rule(db_session, make_org):
    org = make_org("dc4")
    owner = _user(db_session, org, "Architect", "own")
    el = _system(db_session, org, owner=owner)
    assert svc.review_deviations(el, {**CLEAN, "ingests_untrusted_content": True,
                                      "untrusted_input_fenced": True}) == []


@pytest.mark.parametrize("pattern,code", [
    (None, "pattern-not-declared"), ("", "pattern-not-declared"),
    ("other", "pattern-not-approved"), ("agent-swarm", "pattern-not-approved"),
])
def test_a_pattern_off_the_approved_list_is_listed(db_session, make_org, pattern, code):
    org = make_org("dc5")
    owner = _user(db_session, org, "Architect", "own")
    el = _system(db_session, org, owner=owner)
    deviations = svc.review_deviations(el, {**CLEAN, "pattern": pattern})
    assert _codes(deviations) == {code}


def test_oversight_deviations_come_from_the_systems_recorded_profile(db_session, make_org):
    org = make_org("dc6")
    el = _system(db_session, org, autonomy_level="autonomous", approval_gate=False,
                 data_sensitivity="regulated", human_review=False,
                 model_id="claude-3-5-sonnet-20241022")  # stale-model-ok: exercising the retired-model deviation
    codes = _codes(svc.review_deviations(el, dict(CLEAN)))
    assert codes == {"approval-gate-missing", "human-review-missing",
                     "no-accountable-owner", "retired-model"}


def test_unrecorded_oversight_facts_are_listed_not_passed(db_session, make_org):
    org = make_org("dc7")
    owner = _user(db_session, org, "Architect", "own")
    el = _system(db_session, org, owner=owner, autonomy_level=None, data_sensitivity=None,
                 approval_gate=None, human_review=None)
    codes = _codes(svc.review_deviations(el, dict(CLEAN)))
    assert codes == {"autonomy-not-recorded", "data-sensitivity-not-recorded"}


# --- Submitting: the decision record is an ArchitectureDecision --------------


def test_a_submitted_design_is_an_architecture_decision_linked_to_the_system(db_session, make_org):
    org = make_org("dc8")
    submitter = _user(db_session, org, "Architect", "sub")
    el = _system(db_session, org)
    row = _submit(el, org, submitter)
    assert isinstance(row, ArchitectureDecision)
    assert row.ai_system_id == el.id and row.organization_id == org.id
    assert row.status == "under_review" and row.created_by_id == submitter.id
    assert row.archimate_element_ids == [el.id]
    assert row.decision_id and row.decision_id.startswith("AD-")
    assert row.ai_design_review["declaration"]["pattern"] == CLEAN["pattern"]


def test_one_open_submission_per_system(db_session, make_org):
    org = make_org("dc9")
    submitter = _user(db_session, org, "Architect", "sub")
    el = _system(db_session, org)
    _submit(el, org, submitter)
    with pytest.raises(svc.DesignReviewError, match="already under review"):
        _submit(el, org, submitter, title="Second")


@pytest.mark.parametrize("title", ["", "   "])
def test_a_design_needs_a_title(db_session, make_org, title):
    org = make_org("dc10")
    submitter = _user(db_session, org, "Architect", "sub")
    el = _system(db_session, org)
    with pytest.raises(svc.DesignReviewError):
        _submit(el, org, submitter, title=title)


def test_a_non_ai_element_in_the_same_organisation_cannot_be_submitted(db_session, make_org):
    from app.models.models import ArchiMateElement

    org = make_org("dc11")
    submitter = _user(db_session, org, "Architect", "sub")
    plain = ArchiMateElement(name="Plain", type="ApplicationComponent", layer="application",
                             organization_id=org.id, custom_properties={})
    db_session.add(plain)
    db_session.flush()
    with pytest.raises(svc.DesignReviewError, match="not found"):
        svc.submit_design(element_id=plain.id, organization_id=org.id, submitter_id=submitter.id,
                          title="x", context="", declaration=dict(CLEAN))


# --- Two organisations -------------------------------------------------------


def test_a_review_recorded_in_one_organisation_is_invisible_to_another(db_session, make_org):
    org_a, org_b = make_org("dc12a"), make_org("dc12b")
    sub_a = _user(db_session, org_a, "Architect", "suba")
    row = _submit(_system(db_session, org_a), org_a, sub_a)
    assert svc.get_review(row.id, org_a.id).id == row.id
    assert svc.get_review(row.id, org_b.id) is None
    assert [r.id for r in svc.list_reviews(org_a.id)] == [row.id]
    assert svc.list_reviews(org_b.id) == []


def test_another_organisation_cannot_submit_for_or_decide_this_organisations_review(db_session, make_org):
    org_a, org_b = make_org("dc13a"), make_org("dc13b")
    sub_a = _user(db_session, org_a, "Architect", "suba")
    member_b = _user(db_session, org_b, "Architect", "memb", "arb_member")
    el_a = _system(db_session, org_a)
    row = _submit(el_a, org_a, sub_a)
    with pytest.raises(svc.DesignReviewError, match="not found"):
        svc.submit_design(element_id=el_a.id, organization_id=org_b.id, submitter_id=member_b.id,
                          title="x", context="", declaration=dict(CLEAN))
    with pytest.raises(svc.DesignReviewError, match="not found"):
        svc.record_decision(decision_id=row.id, organization_id=org_b.id, decider_id=member_b.id,
                            outcome="rejected", rationale="no")
    assert _raw_status(db_session, row.id) == "under_review"


def test_with_no_organisation_nothing_is_read_and_nothing_is_written(db_session, make_org):
    org = make_org("dc14")
    sub = _user(db_session, org, "Architect", "sub")
    member = _user(db_session, org, "Architect", "memb", "arb_member")
    el = _system(db_session, org)
    row = _submit(el, org, sub)
    assert svc.get_review(row.id, None) is None
    assert svc.list_reviews(None) == []
    with pytest.raises(svc.DesignReviewError):
        svc.submit_design(element_id=el.id, organization_id=None, submitter_id=sub.id,
                          title="x", context="", declaration=dict(CLEAN))
    with pytest.raises(svc.DesignReviewError):
        svc.record_decision(decision_id=row.id, organization_id=None, decider_id=member.id,
                            outcome="rejected", rationale="no")
    assert _raw_status(db_session, row.id) == "under_review"


def test_a_foreign_review_aimed_at_this_systems_id_neither_blocks_nor_shows(db_session, make_org):
    """A row in organisation A that names organisation B's element id: B's own
    submission for that element is not blocked by it, and B never lists it."""
    org_a, org_b = make_org("dc12c"), make_org("dc12d")
    sub_a = _user(db_session, org_a, "Architect", "suba")
    sub_b = _user(db_session, org_b, "Architect", "subb")
    el_b = _system(db_session, org_b)
    foreign = ArchitectureDecision(
        organization_id=org_a.id, decision_id=f"AD-F{uuid.uuid4().hex[:6]}", title="Foreign",
        status="under_review", ai_system_id=el_b.id, created_by_id=sub_a.id,
    )
    db_session.add(foreign)
    db_session.flush()
    assert ArchitectureDecision.query.filter_by(id=foreign.id).count() == 1  # the foreign row exists
    assert svc.list_reviews(org_b.id) == []
    assert svc.get_review(foreign.id, org_b.id) is None
    mine = _submit(el_b, org_b, sub_b)  # not "already under review"
    assert [r.id for r in svc.list_reviews(org_b.id)] == [mine.id]


def test_a_pattern_name_over_the_limit_is_refused(db_session, make_org):
    org = make_org("dc12e")
    sub = _user(db_session, org, "Architect", "sub")
    el = _system(db_session, org)
    with pytest.raises(svc.DesignReviewError, match="80 characters"):
        _submit(el, org, sub, {**CLEAN, "pattern": "x" * 81})


# --- Recording the decision --------------------------------------------------


def test_accepting_a_design_with_deviations_needs_a_rationale_and_keeps_the_list(db_session, make_org):
    org = make_org("dc15")
    sub = _user(db_session, org, "Architect", "sub")
    member = _user(db_session, org, "Architect", "memb", "arb_member")
    owner = _user(db_session, org, "Architect", "own")
    el = _system(db_session, org, owner=owner)
    row = _submit(el, org, sub, {**CLEAN, "ingests_untrusted_content": True,
                                 "untrusted_input_fenced": False})
    with pytest.raises(svc.DesignReviewError, match="rationale is required"):
        svc.record_decision(decision_id=row.id, organization_id=org.id, decider_id=member.id,
                            outcome="accepted", rationale="  ")
    assert _raw_status(db_session, row.id) == "under_review"
    svc.record_decision(decision_id=row.id, organization_id=org.id, decider_id=member.id,
                        outcome="accepted", rationale="Fence lands in the next release.")
    db_session.expire_all()
    done = svc.get_review(row.id, org.id)
    assert done.status == "accepted" and done.decided_by_id == member.id
    assert done.approved_by_id == member.id and done.decided_at is not None
    assert done.rationale == "Fence lands in the next release."
    assert _codes(done.ai_design_review["deviations_at_decision"]) == {"unfenced-untrusted-input"}


def test_a_clean_design_can_be_accepted_without_a_rationale(db_session, make_org):
    org = make_org("dc16")
    sub = _user(db_session, org, "Architect", "sub")
    member = _user(db_session, org, "Architect", "memb", "arb_member")
    owner = _user(db_session, org, "Architect", "own")
    row = _submit(_system(db_session, org, owner=owner), org, sub)
    svc.record_decision(decision_id=row.id, organization_id=org.id, decider_id=member.id,
                        outcome="accepted", rationale="")
    assert _raw_status(db_session, row.id) == "accepted"


def test_a_rejection_always_needs_a_reason(db_session, make_org):
    org = make_org("dc17")
    sub = _user(db_session, org, "Architect", "sub")
    member = _user(db_session, org, "Architect", "memb", "arb_member")
    owner = _user(db_session, org, "Architect", "own")
    row = _submit(_system(db_session, org, owner=owner), org, sub)
    with pytest.raises(svc.DesignReviewError, match="rationale is required"):
        svc.record_decision(decision_id=row.id, organization_id=org.id, decider_id=member.id,
                            outcome="rejected", rationale="")
    svc.record_decision(decision_id=row.id, organization_id=org.id, decider_id=member.id,
                        outcome="rejected", rationale="Wrong pattern for the data.")
    db_session.expire_all()
    done = svc.get_review(row.id, org.id)
    assert done.status == "rejected" and done.rejection_reason == "Wrong pattern for the data."
    assert done.approved_by_id is None


def test_a_decided_review_can_never_be_decided_again(db_session, make_org):
    org = make_org("dc18")
    sub = _user(db_session, org, "Architect", "sub")
    member = _user(db_session, org, "Architect", "memb", "arb_member")
    owner = _user(db_session, org, "Architect", "own")  # a third person, also able to decide
    other = owner
    row = _submit(_system(db_session, org, owner=owner), org, sub)
    svc.record_decision(decision_id=row.id, organization_id=org.id, decider_id=member.id,
                        outcome="rejected", rationale="No.")
    for outcome in ("accepted", "rejected"):
        with pytest.raises(svc.DesignReviewError, match="already decided"):
            svc.record_decision(decision_id=row.id, organization_id=org.id, decider_id=other.id,
                                outcome=outcome, rationale="Changed my mind.")
    db_session.expire_all()
    done = svc.get_review(row.id, org.id)
    assert done.status == "rejected" and done.decided_by_id == member.id


def test_the_submitter_cannot_decide_their_own_design(db_session, make_org):
    org = make_org("dc19")
    sub = _user(db_session, org, "Architect", "sub", "arb_member")
    owner = _user(db_session, org, "Architect", "own")
    row = _submit(_system(db_session, org, owner=owner), org, sub)
    with pytest.raises(svc.DesignReviewError, match="submitted this design"):
        svc.record_decision(decision_id=row.id, organization_id=org.id, decider_id=sub.id,
                            outcome="accepted", rationale="Fine.")
    assert _raw_status(db_session, row.id) == "under_review"


def test_the_decision_must_be_accept_or_reject(db_session, make_org):
    org = make_org("dc20")
    sub = _user(db_session, org, "Architect", "sub")
    member = _user(db_session, org, "Architect", "memb", "arb_member")
    row = _submit(_system(db_session, org), org, sub)
    for bad in (None, "", "approved", "deferred"):
        with pytest.raises(svc.DesignReviewError, match="accept or reject"):
            svc.record_decision(decision_id=row.id, organization_id=org.id, decider_id=member.id,
                                outcome=bad, rationale="x")


def test_an_ordinary_decision_is_not_an_ai_design_review(db_session, make_org):
    """get_review only answers for rows linked to an AI system; a plain
    architecture decision with a guessed id is not reachable through it."""
    org = make_org("dc21")
    plain = ArchitectureDecision(organization_id=org.id, decision_id=f"AD-T{uuid.uuid4().hex[:6]}",
                                 title="Plain ADR", status="proposed")
    db_session.add(plain)
    db_session.flush()
    assert svc.get_review(plain.id, org.id) is None


# --- Routes --------------------------------------------------------------------


def test_arb_member_opens_the_design_sees_deviations_records_a_decision_and_reloads(
    app, db_session, make_org, client, login_as
):
    org = make_org("dc22")
    submitter = _user(db_session, org, "Architect", "ea", "enterprise_architect")
    member = _user(db_session, org, "Architect", "arb", "arb_member")
    owner = _user(db_session, org, "Architect", "own")
    el = _system(db_session, org, owner=owner)

    login_as(client, submitter)
    r = client.post("/architecture/ai-design-reviews/submit", data={
        "ai_system_id": str(el.id), "title": "Claims triage design", "context": "Triage claims.",
        "pattern": "other", "ingests_untrusted_content": "yes", "untrusted_input_fenced": "no"})
    assert r.status_code == 302
    review = ArchitectureDecision.query.filter_by(organization_id=org.id, ai_system_id=el.id).one()
    assert r.headers["Location"].endswith(f"/architecture/ai-design-reviews/{review.id}")

    login_as(client, member)
    page = client.get(f"/architecture/ai-design-reviews/{review.id}").get_data(as_text=True)
    assert 'data-testid="deviation-pattern-not-approved"' in page
    assert 'data-testid="deviation-unfenced-untrusted-input"' in page
    assert 'data-testid="decision-form"' in page

    # Deviations are listed, so a bare acceptance is refused and nothing changes.
    r = client.post(f"/architecture/ai-design-reviews/{review.id}/decision",
                    data={"decision": "accept", "rationale": ""})
    assert r.status_code == 302
    db_session.expire_all()
    assert _raw_status(db_session, review.id) == "under_review"

    r = client.post(f"/architecture/ai-design-reviews/{review.id}/decision",
                    data={"decision": "accept", "rationale": "Accepted with the fence due in Q4."})
    assert r.status_code == 302
    db_session.expire_all()
    assert _raw_status(db_session, review.id) == "accepted"

    login_as(client, member)
    reloaded = client.get(f"/architecture/ai-design-reviews/{review.id}").get_data(as_text=True)
    import re

    assert re.search(r'data-testid="review-status">\s*accepted\s*<', reloaded)
    assert "Accepted with the fence due in Q4." in reloaded
    assert 'data-testid="decision-form"' not in reloaded
    listing = client.get("/architecture/ai-design-reviews/").get_data(as_text=True)
    assert "Claims triage design" in listing


def test_the_decision_route_is_for_the_board_member_and_never_the_submitter(
    app, db_session, make_org, client, login_as
):
    org = make_org("dc23")
    submitter = _user(db_session, org, "Architect", "ea", "arb_member")
    ea = _user(db_session, org, "Architect", "ea2", "enterprise_architect")
    owner = _user(db_session, org, "Architect", "own")
    row = _submit(_system(db_session, org, owner=owner), org, submitter)
    login_as(client, ea)
    assert client.post(f"/architecture/ai-design-reviews/{row.id}/decision",
                       data={"decision": "accept"}).status_code == 403
    login_as(client, submitter)
    page = client.get(f"/architecture/ai-design-reviews/{row.id}").get_data(as_text=True)
    assert 'data-testid="decision-form"' not in page
    client.post(f"/architecture/ai-design-reviews/{row.id}/decision", data={"decision": "accept"})
    db_session.expire_all()
    assert _raw_status(db_session, row.id) == "under_review"


def test_another_organisations_user_gets_404_and_cannot_decide(
    app, db_session, make_org, client, login_as
):
    org_a, org_b = make_org("dc24a"), make_org("dc24b")
    sub = _user(db_session, org_a, "Architect", "sub")
    row = _submit(_system(db_session, org_a), org_a, sub)
    member_b = _user(db_session, org_b, "Architect", "memb", "arb_member")
    login_as(client, member_b)
    assert client.get(f"/architecture/ai-design-reviews/{row.id}").status_code == 404
    client.post(f"/architecture/ai-design-reviews/{row.id}/decision",
                data={"decision": "reject", "rationale": "mine now"})
    listing = client.get("/architecture/ai-design-reviews/").get_data(as_text=True)
    assert row.title not in listing
    assert _raw_status(db_session, row.id) == "under_review"


def test_a_persona_outside_governance_is_refused_everywhere(app, db_session, make_org, client, login_as):
    org = make_org("dc25")
    outsider = _user(db_session, org, "Architect", "fin", "finance")
    sub = _user(db_session, org, "Architect", "sub")
    row = _submit(_system(db_session, org), org, sub)
    login_as(client, outsider)
    assert client.get("/architecture/ai-design-reviews/").status_code == 403
    assert client.get(f"/architecture/ai-design-reviews/{row.id}").status_code == 403
    assert client.post("/architecture/ai-design-reviews/submit", data={}).status_code == 403


def test_the_routes_refuse_when_the_organisation_is_unknown(
    app, db_session, make_org, client, login_as, monkeypatch
):
    from app.modules.ai_chat.routes import ai_design_review_routes

    org = make_org("dc26")
    member = _user(db_session, org, "Architect", "memb", "arb_member")
    sub = _user(db_session, org, "Architect", "sub")
    el = _system(db_session, org)
    row = _submit(el, org, sub)
    login_as(client, member)
    monkeypatch.setattr(ai_design_review_routes, "_org_id", lambda: None)
    assert client.get(f"/architecture/ai-design-reviews/{row.id}").status_code == 404
    assert client.post("/architecture/ai-design-reviews/submit", data={
        "ai_system_id": str(el.id), "title": "x"}).status_code == 403
    assert client.post(f"/architecture/ai-design-reviews/{row.id}/decision",
                       data={"decision": "reject", "rationale": "x"}).status_code == 403
    assert row.title not in client.get("/architecture/ai-design-reviews/").get_data(as_text=True)
    assert _raw_status(db_session, row.id) == "under_review"


def test_a_decision_writes_an_audit_row_only_when_it_is_recorded(
    app, db_session, make_org, client, login_as
):
    org = make_org("dc27")
    sub = _user(db_session, org, "Architect", "sub")
    member = _user(db_session, org, "Architect", "memb", "arb_member")
    owner = _user(db_session, org, "Architect", "own")
    row = _submit(_system(db_session, org, owner=owner), org, sub)

    def audit_rows():
        return db_session.execute(text(
            "SELECT count(*) FROM soc2_audit_log WHERE action = 'ai_design_decision' "
            "AND record_id = :i"), {"i": row.id}).scalar_one()

    login_as(client, member)
    client.post(f"/architecture/ai-design-reviews/{row.id}/decision",
                data={"decision": "reject", "rationale": ""})  # refused: no reason
    assert audit_rows() == 0
    client.post(f"/architecture/ai-design-reviews/{row.id}/decision",
                data={"decision": "reject", "rationale": "Wrong pattern."})
    assert audit_rows() == 1


def test_an_ordinary_decision_id_is_404_on_the_review_routes(app, db_session, make_org, client, login_as):
    """The review routes are keyed by the decision's id, which is a different
    number from the element's: a plain decision with that id in the caller's own
    organisation is not an AI design review."""
    org = make_org("dc28")
    member = _user(db_session, org, "Architect", "memb", "arb_member")
    plain = ArchitectureDecision(organization_id=org.id, decision_id=f"AD-T{uuid.uuid4().hex[:6]}",
                                 title="Plain ADR", status="proposed")
    db_session.add(plain)
    db_session.flush()
    login_as(client, member)
    assert client.get(f"/architecture/ai-design-reviews/{plain.id}").status_code == 404
    client.post(f"/architecture/ai-design-reviews/{plain.id}/decision",
                data={"decision": "reject", "rationale": "x"})
    assert _raw_status(db_session, plain.id) == "proposed"


def test_the_register_detail_links_an_ai_design_decision_to_its_review(
    app, db_session, make_org, client, login_as
):
    org = make_org("dc29")
    sub = _user(db_session, org, "Architect", "sub", "arb_member")
    row = _submit(_system(db_session, org), org, sub)
    login_as(client, sub)
    page = client.get(f"/architecture/decisions/{row.id}").get_data(as_text=True)
    assert 'data-testid="ai-design-review-link"' in page
    assert f"/architecture/ai-design-reviews/{row.id}" in page

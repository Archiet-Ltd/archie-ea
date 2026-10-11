"""Relationship keys a business case's options, outcomes and plan items need,
pinned in both validity authorities.

``app/models/archimate_core.py`` ``VALID_RELATIONSHIPS`` is the coarse
``(relationship, source_layer, target_layer)`` authority. ``ArchimateValidityService``
is the fine-grained, element-type authority the picker route
(``GET /archimate/api/valid-relationship-types``) actually calls. Eight keys
were missing from the coarse authority for the pairs a business case's
options, outcomes and plan items need to link:

  option <-> plan item                 (association, strategy <-> implementation)
  capability / resource hierarchies    (aggregation, strategy, strategy)
  outcome <-> work package             (association, motivation <-> implementation)
  resource assigned to capability      (assignment, strategy, strategy)
  key partner <-> resource/capability  (association, business <-> strategy)

Six of these eight (source_type, target_type, relationship) combinations
already validated at the fine-grained level before this change — association
is unconditionally valid (ArchiMate 3.2 §5.2.4: "association may connect any
two concepts"), and the same-type aggregation and the Resource -> Capability
assignment rules already existed. The fix is the coarse table catching up
with the fine one, not new relationship logic — see the comment on
``ArchimateValidityService._strategy_rules``.

This module pins: the eight keys in both authorities; a representative set
of the canvas zones' other relationships, so this change is proven not to
have moved them; that the two authorities cannot silently drift back apart;
and one refused combination per relationship type this change touches.
"""
from __future__ import annotations

import pytest

from app.models.archimate_core import VALID_RELATIONSHIPS
from app.services.archimate_validity_service import ArchimateValidityService, _layer


@pytest.fixture(scope="module")
def svc():
    return ArchimateValidityService()


# -- The eight keys this change adds to VALID_RELATIONSHIPS ------------------
# (label, relationship, source_type, target_type, expected_source_layer, expected_target_layer)
NEW_KEYS = [
    ("option -> plan item", "association", "CourseOfAction", "WorkPackage", "strategy", "implementation"),
    ("plan item -> option", "association", "WorkPackage", "CourseOfAction", "implementation", "strategy"),
    ("capability hierarchy", "aggregation", "Capability", "Capability", "strategy", "strategy"),
    ("resource hierarchy", "aggregation", "Resource", "Resource", "strategy", "strategy"),
    ("outcome -> work package", "association", "Outcome", "WorkPackage", "motivation", "implementation"),
    ("work package -> outcome", "association", "WorkPackage", "Outcome", "implementation", "motivation"),
    ("resource assigned to capability", "assignment", "Resource", "Capability", "strategy", "strategy"),
    ("key partner -> resource", "association", "BusinessActor", "Resource", "business", "strategy"),
    ("key partner -> capability", "association", "BusinessActor", "Capability", "business", "strategy"),
    ("resource -> key partner", "association", "Resource", "BusinessActor", "strategy", "business"),
    ("capability -> key partner", "association", "Capability", "BusinessActor", "strategy", "business"),
]


@pytest.mark.parametrize(
    "label,rel,source,target,src_layer,tgt_layer", NEW_KEYS, ids=[r[0] for r in NEW_KEYS]
)
def test_new_key_is_in_both_authorities(svc, label, rel, source, target, src_layer, tgt_layer):
    assert (rel, src_layer, tgt_layer) in VALID_RELATIONSHIPS, (
        f"{label}: ({rel!r}, {src_layer!r}, {tgt_layer!r}) missing from VALID_RELATIONSHIPS"
    )
    assert svc.is_valid(source, target, rel), (
        f"{label}: ArchimateValidityService.is_valid({source!r}, {target!r}, {rel!r}) is False"
    )
    assert _layer(source) == src_layer, f"{label}: {source} layers as {_layer(source)}, expected {src_layer}"
    assert _layer(target) == tgt_layer, f"{label}: {target} layers as {_layer(target)}, expected {tgt_layer}"


# -- A representative set of the canvas zones' other relationships, proven ---
# -- unaffected by the eight new keys ----------------------------------------
EXISTING_ROWS = [
    ("problem -> customer segment", "association", "Driver", "Stakeholder", "motivation", "motivation"),
    ("value proposition -> customer segment", "association", "Value", "Stakeholder", "motivation", "motivation"),
    ("capability realises solution requirement", "realization", "Capability", "Requirement", "strategy", "motivation"),
    ("key metric -> value proposition", "association", "Outcome", "Value", "motivation", "motivation"),
    ("channel -> customer segment", "association", "BusinessInterface", "Stakeholder", "business", "motivation"),
    ("unfair advantage assigned to key activity", "assignment", "Resource", "Capability", "strategy", "strategy"),
    ("customer relationship -> customer segment", "association", "BusinessService", "Stakeholder", "business", "motivation"),
    ("key activity serves value stream", "serving", "Capability", "ValueStream", "strategy", "strategy"),
    ("key resource assigned to key activity", "assignment", "Resource", "Capability", "strategy", "strategy"),
    ("key partnership -> resource it supplies", "association", "BusinessActor", "Resource", "business", "strategy"),
    ("assessment -> driver", "association", "Assessment", "Driver", "motivation", "motivation"),
    ("option realises goal", "realization", "CourseOfAction", "Goal", "strategy", "motivation"),
    ("option -> capability it configures", "association", "CourseOfAction", "Capability", "strategy", "strategy"),
    ("option -> resource it configures", "association", "CourseOfAction", "Resource", "strategy", "strategy"),
    ("option realises benefit", "realization", "CourseOfAction", "Outcome", "strategy", "motivation"),
    ("option realises disbenefit", "realization", "CourseOfAction", "Outcome", "strategy", "motivation"),
    ("plan item -> target plateau", "association", "WorkPackage", "Plateau", "implementation", "implementation"),
    ("risk -> option", "association", "Assessment", "CourseOfAction", "motivation", "strategy"),
    ("risk -> outcome", "association", "Assessment", "Outcome", "motivation", "motivation"),
]
# Two rows the mapping's prose also names are deliberately left out of this
# table rather than pinned as passing: an outcome realising its goal, and a
# plan item realising the outcome it delivers, neither validate today
# (motivation-internal and implementation -> motivation realization are not
# offered by ArchimateValidityService for these types) and fixing that is a
# different, wider change than the eight keys above.


@pytest.mark.parametrize(
    "label,rel,source,target,src_layer,tgt_layer", EXISTING_ROWS, ids=[r[0] for r in EXISTING_ROWS]
)
def test_existing_row_still_valid(svc, label, rel, source, target, src_layer, tgt_layer):
    assert (rel, src_layer, tgt_layer) in VALID_RELATIONSHIPS, (
        f"{label}: ({rel!r}, {src_layer!r}, {tgt_layer!r}) missing from VALID_RELATIONSHIPS"
    )
    assert svc.is_valid(source, target, rel), (
        f"{label}: ArchimateValidityService.is_valid({source!r}, {target!r}, {rel!r}) is False"
    )


# -- Drift: the two authorities must not disagree on any of the eight new keys
def test_the_two_authorities_agree_on_every_new_key(svc):
    """For each new key, the coarse table and the fine-grained service must
    give the same answer for the key's representative element types. A rule
    removed from the service without its VALID_RELATIONSHIPS key also being
    removed — or the reverse — shows up here as a disagreement."""
    disagreements = []
    for label, rel, source, target, src_layer, tgt_layer in NEW_KEYS:
        in_coarse = (rel, src_layer, tgt_layer) in VALID_RELATIONSHIPS
        in_fine = svc.is_valid(source, target, rel)
        if in_coarse != in_fine:
            disagreements.append(
                {"row": label, "key": (rel, src_layer, tgt_layer), "coarse": in_coarse, "fine": in_fine}
            )
    assert not disagreements, disagreements


# -- Negative rows: one per relationship type this change touches ------------


def test_aggregation_is_not_valid_between_different_strategy_types(svc):
    """The new ("aggregation","strategy","strategy") key covers same-type
    hierarchies only (Capability/Capability, Resource/Resource). Two
    different strategy types stay refused."""
    assert ("aggregation", "strategy", "strategy") in VALID_RELATIONSHIPS
    assert not svc.is_valid("Resource", "Capability", "aggregation")


def test_assignment_is_not_valid_in_reverse(svc):
    """The new ("assignment","strategy","strategy") key covers Resource ->
    Capability. The reverse direction stays refused."""
    assert ("assignment", "strategy", "strategy") in VALID_RELATIONSHIPS
    assert not svc.is_valid("Capability", "Resource", "assignment")


def test_realization_still_refuses_an_active_structure_target(svc):
    """Association's universal validity does not extend to realization: a
    course of action may not realise an active-structure element such as a
    business actor (ArchiMate 3.2 §5.1.3 — realization targets a MORE
    ABSTRACT entity, never a concrete performer)."""
    assert not svc.is_valid("CourseOfAction", "BusinessActor", "realization")


def test_association_key_absence_from_the_coarse_table_does_not_mean_invalid(svc):
    """Association is unconditionally valid in the fine authority (§5.2.4:
    "association may connect any two concepts"), so the coarse table is
    deliberately narrower for it — a layer pair the coarse table has never
    enumerated can still validate at the fine-grained level. Association is
    the one relationship type for which "not in VALID_RELATIONSHIPS" is not
    itself a negative signal, and this test names that explicitly rather
    than leaving it to be rediscovered as a false regression later."""
    assert ("association", "physical", "strategy") not in VALID_RELATIONSHIPS
    assert svc.is_valid("Equipment", "Capability", "association")


# -- The picker contract: list, create, and cross-tenant refusal -------------


def _make_user(db_session, org_id, label):
    import uuid

    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        organization_id=org_id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


class TestPickerContract:
    def test_option_to_plan_item_association_through_the_create_route(
        self, app, db_session, make_org, client, login_as
    ):
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

        org_a = make_org("cv0-picker-a")
        org_b = make_org("cv0-picker-b")
        user_a = _make_user(db_session, org_a.id, "PickerOwnerA")
        user_b = _make_user(db_session, org_b.id, "PickerAttackerB")

        option = ArchiMateElement(
            name="Expand through partners", type="CourseOfAction",
            layer="strategy", organization_id=org_a.id,
        )
        plan_item = ArchiMateElement(
            name="Partner onboarding", type="WorkPackage",
            layer="implementation", organization_id=org_a.id,
        )
        b_element = ArchiMateElement(
            name="Org B's own option", type="CourseOfAction",
            layer="strategy", organization_id=org_b.id,
        )
        db_session.add_all([option, plan_item, b_element])
        db_session.flush()
        option_id, plan_item_id, b_element_id = option.id, plan_item.id, b_element.id
        # Captured now, as plain ints: db_session.expunge_all() below detaches
        # every ORM instance still in this session, including user_a and
        # user_b, and login_as(client, <detached User>) then raises
        # DetachedInstanceError trying to read .id off an expired instance.
        # login_as accepts a raw id (tests/conftest.py::_login) precisely so
        # a caller past an expunge can still identify who to log in as.
        user_a_id, user_b_id = user_a.id, user_b.id

        login_as(client, user_a)

        types_resp = client.get(
            f"/archimate/api/valid-relationship-types?source_id={option_id}&target_id={plan_item_id}"
        )
        assert types_resp.status_code == 200, types_resp.get_data(as_text=True)
        assert "association" in types_resp.get_json()["valid_types"]

        create_resp = client.post("/archimate/api/relationships", json={
            "source_element_id": option_id,
            "target_element_id": plan_item_id,
            "relationship_type": "association",
        })
        assert create_resp.status_code == 201, create_resp.get_data(as_text=True)

        rows = db_session.query(ArchiMateRelationship).filter_by(
            source_id=option_id, target_id=plan_item_id, type="association",
        ).all()
        assert len(rows) == 1

        # Force the next db.session.get() to hit the database rather than this
        # session's identity map (test_tenant_isolation.py::
        # test_get_by_id_is_NOT_scoped_on_an_identity_map_hit): a single real
        # HTTP request only ever sees one tenant on a fresh session, but this
        # test drives two "sessions" worth of requests through the one
        # db_session fixture keeps open, and a map hit would return org A's
        # element to org B without ever consulting the tenant predicate —
        # a false pass that proves nothing about the route.
        db_session.expunge_all()

        # B's session, naming A's ids: the route's own 404, nothing written.
        login_as(client, user_b_id)
        cross_resp = client.post("/archimate/api/relationships", json={
            "source_element_id": option_id,
            "target_element_id": plan_item_id,
            "relationship_type": "association",
        })
        assert cross_resp.status_code == 404, cross_resp.get_data(as_text=True)

        # login_as clears the cached g.current_org_id (see its docstring on
        # db_session holding one app context for the whole test) as well as
        # the identity cookie. Without this, the tenant filter the request
        # above left behind (do_orm_execute -> _add_tenant_filter, keyed off
        # that same g.current_org_id) would still be org B's when the plain
        # query below runs, and it would silently filter A's own row out of
        # its own count — a false "nothing changed" for the wrong reason.
        login_as(client, user_a_id)
        rows_after_b = db_session.query(ArchiMateRelationship).filter_by(
            source_id=option_id, target_id=plan_item_id, type="association",
        ).all()
        assert len(rows_after_b) == 1  # unchanged — no second row, no row stolen

        db_session.expunge_all()

        # A's session, naming B's element: also the route's own 404, nothing written.
        naming_b_resp = client.post("/archimate/api/relationships", json={
            "source_element_id": option_id,
            "target_element_id": b_element_id,
            "relationship_type": "association",
        })
        assert naming_b_resp.status_code == 404, naming_b_resp.get_data(as_text=True)
        rows_naming_b = db_session.query(ArchiMateRelationship).filter_by(
            source_id=option_id, target_id=b_element_id,
        ).all()
        assert rows_naming_b == []


# -- Hardening: the create route now refuses what the validity service ------
# -- rejects, after its existing checks --------------------------------------


class TestCreateRelationshipHardening:
    def test_create_relationship_refuses_a_type_the_validity_service_rejects(
        self, app, db_session, make_org, client, login_as
    ):
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

        org = make_org("cv0-hardening")
        user = _make_user(db_session, org.id, "HardeningOwner")

        business_el = ArchiMateElement(
            name="Claims Handling", type="BusinessProcess",
            layer="business", organization_id=org.id,
        )
        tech_el = ArchiMateElement(
            name="Claims Database", type="Node",
            layer="technology", organization_id=org.id,
        )
        db_session.add_all([business_el, tech_el])
        db_session.flush()
        business_id, tech_id = business_el.id, tech_el.id

        login_as(client, user)
        resp = client.post("/archimate/api/relationships", json={
            "source_element_id": business_id,
            "target_element_id": tech_id,
            "relationship_type": "composition",
        })
        assert resp.status_code == 400, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["success"] is False

        rows = db_session.query(ArchiMateRelationship).filter_by(
            source_id=business_id, target_id=tech_id, type="composition",
        ).all()
        assert rows == []

    def test_create_relationship_still_accepts_a_type_the_validity_service_allows(
        self, app, db_session, make_org, client, login_as
    ):
        """The hardening refuses the invalid pair without breaking the valid
        one — a control so a validator that rejected everything would not
        pass silently."""
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

        org = make_org("cv0-hardening-control")
        user = _make_user(db_session, org.id, "HardeningControlOwner")

        option = ArchiMateElement(
            name="Renegotiate supplier terms", type="CourseOfAction",
            layer="strategy", organization_id=org.id,
        )
        plan_item = ArchiMateElement(
            name="Supplier negotiation", type="WorkPackage",
            layer="implementation", organization_id=org.id,
        )
        db_session.add_all([option, plan_item])
        db_session.flush()
        option_id, plan_item_id = option.id, plan_item.id

        login_as(client, user)
        resp = client.post("/archimate/api/relationships", json={
            "source_element_id": option_id,
            "target_element_id": plan_item_id,
            "relationship_type": "association",
        })
        assert resp.status_code == 201, resp.get_data(as_text=True)

        rows = db_session.query(ArchiMateRelationship).filter_by(
            source_id=option_id, target_id=plan_item_id, type="association",
        ).all()
        assert len(rows) == 1

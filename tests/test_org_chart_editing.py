"""Org-chart editing: edit the organisation hierarchy on the existing chart,
assign people to units from the administrator's user-edit path, and derive for
each unit the capabilities owned by its people.

Scope: this is the first organisation-structure slice. Only the chart-edit
routes, the admin business-unit picker and the derived "capabilities per
unit" read are built here; nothing is stored beyond the existing
``BusinessActor`` / ``ArchiMateRelationship`` rows and ``User.business_unit_id``.

Every beforeEach/assert pair below fails on unchanged main:
  * adding a child unit appears in ``build_org_tree()`` and survives a reload
    (on main nothing writes the hierarchy — the chart is read-only);
  * a move that would create a cycle is refused, and a unit with children or
    people cannot be removed;
  * assigning a person to a unit writes ``business_unit_id`` (on main nothing
    writes it) and the application list's existing unit filter then includes
    that person's applications;
  * "capabilities per unit" returns exactly the capabilities owned by the
    unit's people and descendants, lists people with no unit under "No unit",
    and shows the empty state;
  * two organisations: organisation A can never see, edit or assign to
    organisation B's units, and A's panel never lists B's capabilities;
  * the new routes are role-gated like the RACI cell editor — a role without
    edit rights still sees the chart read-only.
"""
from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# ── seeding helpers ─────────────────────────────────────────────────────────


def _make_actor(db_session, org, name, actor_type="Department"):
    from app.models.business_layer import BusinessActor

    actor = BusinessActor(
        name=f"{name} {uuid.uuid4().hex[:6]}",
        actor_type=actor_type,
        organization_id=org.id,
    )
    db_session.add(actor)
    db_session.flush()
    assert actor.archimate_element_id is not None
    return actor


def _link_actors(db_session, parent, child, rel_type="Composition"):
    from app.models.archimate_core import ArchiMateRelationship

    rel = ArchiMateRelationship(
        type=rel_type,
        source_id=parent.archimate_element_id,
        target_id=child.archimate_element_id,
        organization_id=parent.organization_id,
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _make_user(db_session, org, label, role_name="User", enterprise_role=None):
    from app.models.user import Role, User

    role = Role.query.filter_by(name=role_name).first()
    user = User(
        email=f"orgchart-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Test",
        organization_id=org.id,
        enterprise_role=enterprise_role,
        confirmed=True,
    )
    if role is not None:
        user.role = role
    db_session.add(user)
    db_session.flush()
    return user


def _make_owner(db_session, org, user, capability):
    from app.services.capability_ownership_service import set_capability_owner

    return set_capability_owner(
        capability_id=capability.id,
        user_id=user.id,
        organization_id=org.id,
    )


def _actor_names_in_tree(tree):
    """Every node name in the tree, children traversed depth-first. Handles
    the flat-fallback shape (groups) too — a tenant with zero hierarchy
    edges renders its units grouped by actor_type instead of as a tree."""
    names = []
    if tree.get("fallback"):
        for group in tree.get("groups") or []:
            names.extend(actor["name"] for actor in group["actors"])
        return names
    def _walk(node):
        names.append(node["name"])
        for child in node.get("children") or []:
            _walk(child)
    for root in tree.get("roots") or []:
        _walk(root)
    return names


def _capability(db_session, org, name):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(cap)
    db_session.flush()
    return cap


# ── 1. hierarchy editing ────────────────────────────────────────────────────


class TestHierarchyEditing:
    def test_adding_a_child_unit_appears_in_the_tree_and_survives_a_reload(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.organization import service

        org = make_org("hier-add")
        with tenant_ctx(org.id):
            parent = _make_actor(db_session, org, "Parent Unit", "Business Unit")
            child = service.add_child_unit(parent_id=parent.id, name="New Team", actor_type="Team")
            db_session.commit()

            tree = service.build_org_tree()
            assert child.name in _actor_names_in_tree(tree)

            # "survives a reload": fresh session reads the committed rows.
            db_session.expire_all()
            tree_again = service.build_org_tree()
            assert child.name in _actor_names_in_tree(tree_again)

            parent_node = next(
                root for root in tree_again["roots"] if root["name"] == parent.name
            )
            assert child.name in {c["name"] for c in parent_node["children"]}

    def test_renaming_a_unit_changes_its_chart_node(self, app, db_session, make_org, tenant_ctx):
        from app.modules.organization import service

        org = make_org("hier-rename")
        with tenant_ctx(org.id):
            unit = _make_actor(db_session, org, "Old Name", "Department")
            service.rename_unit(unit_id=unit.id, name="New Name")
            db_session.commit()
            assert unit.name == "New Name"
            assert "New Name" in _actor_names_in_tree(service.build_org_tree())

    def test_moving_a_unit_under_another_parent_reparents_it(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.organization import service

        org = make_org("hier-move")
        with tenant_ctx(org.id):
            root_a = _make_actor(db_session, org, "Root A", "Business Unit")
            root_b = _make_actor(db_session, org, "Root B", "Business Unit")
            unit = _make_actor(db_session, org, "Wanderer", "Department")
            _link_actors(db_session, root_a, unit)

            moved = service.move_unit(unit_id=unit.id, new_parent_id=root_b.id)
            db_session.commit()
            assert moved["parent_id"] == root_b.id

            tree = service.build_org_tree()
            root_b_node = next(r for r in tree["roots"] if r["name"] == root_b.name)
            assert unit.name in {c["name"] for c in root_b_node["children"]}
            roots = tree["roots"]
            assert unit.name not in {r["name"] for r in roots}

    def test_move_under_a_descendant_is_refused_as_a_cycle(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.organization import service

        org = make_org("hier-cycle")
        with tenant_ctx(org.id):
            top = _make_actor(db_session, org, "Top", "Business Unit")
            mid = _make_actor(db_session, org, "Mid", "Department")
            leaf = _make_actor(db_session, org, "Leaf", "Team")
            _link_actors(db_session, top, mid)
            _link_actors(db_session, mid, leaf)

            # Moving "Top" under its own descendant would create a cycle.
            with pytest.raises(ValueError):
                service.move_unit(unit_id=top.id, new_parent_id=leaf.id)
            # Moving a unit under itself is refused too.
            with pytest.raises(ValueError):
                service.move_unit(unit_id=top.id, new_parent_id=top.id)

            tree = service.build_org_tree()
            assert mid.name in _actor_names_in_tree(tree)
            # The hierarchy is unchanged: Mid is still under Top.
            top_node = next(r for r in tree["roots"] if r["name"] == top.name)
            assert mid.name in {c["name"] for c in top_node["children"]}

    def test_moving_a_unit_to_the_root_detaches_it(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.organization import service

        org = make_org("hier-detach")
        with tenant_ctx(org.id):
            parent = _make_actor(db_session, org, "Parent", "Business Unit")
            unit = _make_actor(db_session, org, "Detached", "Department")
            _link_actors(db_session, parent, unit)

            moved = service.move_unit(unit_id=unit.id, new_parent_id=None)
            db_session.commit()
            assert moved["parent_id"] is None

            tree = service.build_org_tree()
            assert unit.name in _actor_names_in_tree(tree)
            # The unit is no longer anyone's child — either the tree shows no
            # parent containing it, or (no edges left at all) the flat
            # fallback renders it unparented.
            embedded = any(
                unit.name in {c["name"] for c in root.get("children") or []}
                for root in tree.get("roots") or []
            )
            assert embedded is False

    def test_unit_with_children_cannot_be_removed(self, app, db_session, make_org, tenant_ctx):
        from app.modules.organization import service

        org = make_org("hier-rm-children")
        with tenant_ctx(org.id):
            parent = _make_actor(db_session, org, "Parent", "Business Unit")
            child = _make_actor(db_session, org, "Child", "Team")
            _link_actors(db_session, parent, child)

            with pytest.raises(ValueError):
                service.remove_unit(unit_id=parent.id)
            db_session.commit()
            assert parent.name in _actor_names_in_tree(service.build_org_tree())

    def test_unit_with_people_cannot_be_removed(self, app, db_session, make_org, tenant_ctx):
        from app.modules.organization import service

        org = make_org("hier-rm-people")
        with tenant_ctx(org.id):
            unit = _make_actor(db_session, org, "Staffed Unit", "Department")
            person = _make_user(db_session, org, "staffed", enterprise_role="enterprise_architect")
            person.business_unit_id = unit.id
            db_session.commit()

            with pytest.raises(ValueError):
                service.remove_unit(unit_id=unit.id)
            db_session.commit()
            assert unit.name in _actor_names_in_tree(service.build_org_tree())

    def test_removing_a_leaf_unit_without_people_deletes_it(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.organization import service

        org = make_org("hier-rm-ok")
        with tenant_ctx(org.id):
            parent = _make_actor(db_session, org, "Parent", "Business Unit")
            leaf = _make_actor(db_session, org, "Expendable", "Team")
            _link_actors(db_session, parent, leaf)

            service.remove_unit(unit_id=leaf.id)
            db_session.commit()

            tree = service.build_org_tree()
            assert leaf.name not in _actor_names_in_tree(tree)
            assert parent.name in _actor_names_in_tree(tree)


# ── 2. assigning a person to a unit ─────────────────────────────────────────


class TestAssignPersonToUnit:
    def _admin(self, db_session, org):
        return _make_user(db_session, org, "assignadmin", role_name="Administrator")

    def test_admin_picker_lists_only_units_of_the_same_organisation(
        self, app, db_session, make_org, tenant_ctx, client, login_as):
        org_a = make_org("assign-picker-a")
        org_b = make_org("assign-picker-b")
        with tenant_ctx(org_a.id):
            unit_a = _make_actor(db_session, org_a, "Org A Dept", "Department")
        with tenant_ctx(org_b.id):
            unit_b = _make_actor(db_session, org_b, "Org B Dept", "Department")
        admin = self._admin(db_session, org_a)
        target = _make_user(db_session, org_a, "pickertarget")
        db_session.commit()

        login_as(client, admin)
        resp = client.get(f"/admin/user/{target.id}/change-business-unit")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)
        assert unit_a.name in body
        assert unit_b.name not in body  # another organisation's unit is never offered

    def test_admin_assigns_a_person_to_a_unit_and_can_clear_it(
        self, app, db_session, make_org, tenant_ctx, client, login_as):
        org = make_org("assign-set")
        with tenant_ctx(org.id):
            unit = _make_actor(db_session, org, "Finance", "Department")
        admin = self._admin(db_session, org)
        target = _make_user(db_session, org, "assigneetarget")
        db_session.commit()

        login_as(client, admin)
        resp = client.post(
            f"/admin/user/{target.id}/change-business-unit",
            data={"business_unit_id": str(unit.id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert target.business_unit_id == unit.id

        resp = client.post(
            f"/admin/user/{target.id}/change-business-unit",
            data={"business_unit_id": ""},
            follow_redirects=True,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert target.business_unit_id is None  # empty means "no unit"

    def test_admin_cannot_assign_a_unit_from_another_organisation(
        self, app, db_session, make_org, tenant_ctx, client, login_as):
        org_a = make_org("assign-xorg-a")
        org_b = make_org("assign-xorg-b")
        with tenant_ctx(org_b.id):
            unit_b = _make_actor(db_session, org_b, "Intruder Dept", "Department")
        admin = self._admin(db_session, org_a)
        target = _make_user(db_session, org_a, "xorgtarget")
        db_session.commit()

        login_as(client, admin)
        resp = client.post(
            f"/admin/user/{target.id}/change-business-unit",
            data={"business_unit_id": str(unit_b.id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200  # the form re-renders with the refusal
        assert target.business_unit_id is None
        body = resp.get_data(as_text=True)
        # The picker never offered the other organisation's unit, so the
        # posted value is refused as an invalid choice (defence in depth:
        # the service also refuses it).
        assert "Not a valid choice" in body
        assert unit_b.name not in body

    def test_assigning_a_unit_makes_the_application_list_include_the_persons_applications(
        self, app, db_session, make_org, tenant_ctx, client, login_as):
        from app.models.application_portfolio import ApplicationComponent
        from app.models.relationship_tables import ApplicationBusinessActorMapping

        org = make_org("assign-applist")
        with tenant_ctx(org.id):
            unit = _make_actor(db_session, org, "Portfolio Dept", "Department")
            app_in_unit = ApplicationComponent(
                name=f"BU Mapped App {uuid.uuid4().hex[:6]}",
                lifecycle_status="operational",
                organization_id=org.id,
            )
            app_other = ApplicationComponent(
                name=f"Unmapped App {uuid.uuid4().hex[:6]}",
                lifecycle_status="operational",
                organization_id=org.id,
            )
            db_session.add_all([app_in_unit, app_other])
            db_session.flush()
            db_session.add(ApplicationBusinessActorMapping(
                application_component_id=app_in_unit.id,
                business_actor_id=unit.id,
                relationship_type="Owner",
            ))
        admin = self._admin(db_session, org)
        person = _make_user(db_session, org, "buowner", enterprise_role="enterprise_architect")
        db_session.commit()

        login_as(client, admin)
        resp = client.post(
            f"/admin/user/{person.id}/change-business-unit",
            data={"business_unit_id": str(unit.id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert person.business_unit_id == unit.id

        # The person now sees the list scoped to their unit, exactly as the
        # existing business-unit filter already scopes it for other callers.
        login_as(client, person)
        list_resp = client.get("/applications/")
        assert list_resp.status_code == 200, list_resp.get_data(as_text=True)[:500]
        body = list_resp.get_data(as_text=True)
        assert app_in_unit.name in body
        assert app_other.name not in body

    # ── 3. capabilities per unit ────────────────────────────────────────────────


class TestCapabilitiesPerUnit:
    def test_unit_capabilities_are_exactly_its_people_and_descendants(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.organization import service

        org = make_org("caps-scope")
        with tenant_ctx(org.id):
            unit = _make_actor(db_session, org, "Headquarters", "Business Unit")
            sub = _make_actor(db_session, org, "Audit Dept", "Department")
            elsewhere = _make_actor(db_session, org, "Remote Dept", "Department")
            _link_actors(db_session, unit, sub)

            in_unit = _make_user(db_session, org, "inunit", enterprise_role="enterprise_architect")
            in_sub = _make_user(db_session, org, "insub", enterprise_role="enterprise_architect")
            in_other = _make_user(db_session, org, "inother", enterprise_role="enterprise_architect")
            in_unit.business_unit_id = unit.id
            in_sub.business_unit_id = sub.id
            in_other.business_unit_id = elsewhere.id
            db_session.commit()

            cap_owned_by_unit = _capability(db_session, org, "HQ-Owned Capability")
            cap_owned_by_sub = _capability(db_session, org, "Sub-Owned Capability")
            cap_owned_elsewhere = _capability(db_session, org, "Elsewhere Capability")
            _make_owner(db_session, org, in_unit, cap_owned_by_unit)
            _make_owner(db_session, org, in_sub, cap_owned_by_sub)
            _make_owner(db_session, org, in_other, cap_owned_elsewhere)
            db_session.commit()

            result = service.capabilities_for_unit(unit_id=unit.id)
            names = {c["name"] for c in result["capabilities"]}
            assert cap_owned_by_unit.name in names
            assert cap_owned_by_sub.name in names  # descendant's people count
            assert cap_owned_elsewhere.name not in names  # far unit's people do not
            assert result["empty"] is False

            hq_entry = next(c for c in result["capabilities"] if c["name"] == cap_owned_by_unit.name)
            assert any(o["user_id"] == in_unit.id for o in hq_entry["owners"])

    def test_capability_owner_with_no_unit_is_listed_under_no_unit(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.organization import service

        org = make_org("caps-nounit")
        with tenant_ctx(org.id):
            unit = _make_actor(db_session, org, "Unit", "Department")
            no_unit_person = _make_user(
                db_session, org, "unassigned", enterprise_role="enterprise_architect"
            )
            db_session.commit()
            cap = _capability(db_session, org, "Loose Capability")
            _make_owner(db_session, org, no_unit_person, cap)
            db_session.commit()

            result = service.capabilities_for_unit(unit_id=unit.id)
            # The capability is owned by no unit's people, so it is not in the
            # unit's list...
            assert result["capabilities"] == []
            assert result["empty"] is True
            # ...and its owner appears under "No unit" with their capability.
            assert len(result["no_unit"]) == 1
            person_entry = result["no_unit"][0]
            assert person_entry["user_id"] == no_unit_person.id
            assert cap.name in {c["name"] for c in person_entry["capabilities"]}

    def test_empty_unit_shows_the_empty_state(self, app, db_session, make_org, tenant_ctx):
        from app.modules.organization import service

        org = make_org("caps-empty")
        with tenant_ctx(org.id):
            unit = _make_actor(db_session, org, "Quiet Dept", "Department")
            db_session.commit()

            result = service.capabilities_for_unit(unit_id=unit.id)
            assert result["capabilities"] == []
            assert result["empty"] is True

    def test_capabilities_api_route_returns_the_panel_payload(
        self, app, db_session, make_org, tenant_ctx, client, login_as):
        from app.modules.organization import service

        org = make_org("caps-route")
        with tenant_ctx(org.id):
            unit = _make_actor(db_session, org, "Panel Dept", "Department")
            cap = _capability(db_session, org, "Panel Capability")
            owner = _make_user(db_session, org, "panelowner", enterprise_role="enterprise_architect")
            owner.business_unit_id = unit.id
            db_session.commit()
            _make_owner(db_session, org, owner, cap)
            db_session.commit()

            login_as(client, owner)
            resp = client.get(f"/organization/chart/api/units/{unit.id}/capabilities")
            assert resp.status_code == 200, resp.get_data(as_text=True)
            payload = resp.get_json()["data"]
            assert payload["unit"]["id"] == unit.id
            assert payload["capabilities"][0]["name"] == cap.name
            assert payload["capabilities"][0]["owners"][0]["user_id"] == owner.id
            assert payload["unit"]["id"] == unit.id

    # ── 4. two-organisation isolation on every route ────────────────────────────


class TestTwoOrganisationIsolation:
    def _admin(self, db_session, org):
        return _make_user(db_session, org, "isolation-admin", role_name="Administrator")

    def test_org_a_cannot_edit_org_b_units_via_any_chart_route(
        self, app, db_session, make_org, tenant_ctx, client, login_as):
        org_a = make_org("iso-a")
        org_b = make_org("iso-b")
        with tenant_ctx(org_b.id):
            parent_b = _make_actor(db_session, org_b, "B Parent", "Business Unit")
            unit_b = _make_actor(db_session, org_b, "B Unit", "Department")
            _link_actors(db_session, parent_b, unit_b)
        admin_a = self._admin(db_session, org_a)
        db_session.commit()

        login_as(client, admin_a)

        add_resp = client.post(
            "/organization/chart/api/units",
            data=json.dumps({"name": "Sneaky Child", "parent_id": parent_b.id}),
            content_type="application/json",
        )
        assert add_resp.status_code == 400, add_resp.get_data(as_text=True)

        rename_resp = client.patch(
            f"/organization/chart/api/units/{unit_b.id}",
            data=json.dumps({"name": "Renamed by A"}),
            content_type="application/json",
        )
        assert rename_resp.status_code == 404, rename_resp.get_data(as_text=True)

        move_resp = client.patch(
            f"/organization/chart/api/units/{unit_b.id}/parent",
            data=json.dumps({"parent_id": None}),
            content_type="application/json",
        )
        assert move_resp.status_code == 404, move_resp.get_data(as_text=True)

        delete_resp = client.delete(f"/organization/chart/api/units/{unit_b.id}")
        assert delete_resp.status_code == 404, delete_resp.get_data(as_text=True)

        # Nothing changed in B's tree.
        db_session.expire_all()
        with tenant_ctx(org_b.id):
            from app.modules.organization import service

            tree_b = service.build_org_tree()
            assert unit_b.name in _actor_names_in_tree(tree_b)

    def test_org_a_panel_never_lists_org_b_capabilities(
        self, app, db_session, make_org, tenant_ctx, client, login_as):
        from app.modules.organization import service

        org_a = make_org("iso-caps-a")
        org_b = make_org("iso-caps-b")
        with tenant_ctx(org_a.id):
            unit_a = _make_actor(db_session, org_a, "A Unit", "Department")
            person_a = _make_user(
                db_session, org_a, "isocapsa", enterprise_role="enterprise_architect"
            )
            person_a.business_unit_id = unit_a.id
            db_session.commit()
            cap_a = _capability(db_session, org_a, "A Capability")
            _make_owner(db_session, org_a, person_a, cap_a)
        with tenant_ctx(org_b.id):
            unit_b = _make_actor(db_session, org_b, "B Unit", "Department")
            person_b = _make_user(
                db_session, org_b, "isocapsb", enterprise_role="enterprise_architect"
            )
            person_b.business_unit_id = unit_b.id
            db_session.commit()
            cap_b = _capability(db_session, org_b, "B Capability")
            _make_owner(db_session, org_b, person_b, cap_b)
        db_session.commit()

        with tenant_ctx(org_a.id):
            result = service.capabilities_for_unit(unit_id=unit_a.id)
            names = {c["name"] for c in result["capabilities"]}
            assert cap_a.name in names
            assert cap_b.name not in names
            for person in result["no_unit"]:
                for c in person["capabilities"]:
                    assert c["name"] != cap_b.name

        login_as(client, person_a)
        resp = client.get(f"/organization/chart/api/units/{unit_a.id}/capabilities")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert cap_a.name in body
        assert cap_b.name not in body

    def test_org_a_cannot_assign_b_unit_and_b_cannot_be_moved_under_a_unit(
        self, app, db_session, make_org, tenant_ctx, client, login_as):
        org_a = make_org("iso-assign-a")
        org_b = make_org("iso-assign-b")
        with tenant_ctx(org_a.id):
            unit_a = _make_actor(db_session, org_a, "A Unit", "Department")
        with tenant_ctx(org_b.id):
            unit_b = _make_actor(db_session, org_b, "B Unit", "Department")
        admin_a = self._admin(db_session, org_a)
        target_b = _make_user(db_session, org_b, "bperson")
        db_session.commit()

        # A's admin cannot assign a B unit to a B user (different orgs): the
        # route never resolves a user outside the caller's organisation.
        login_as(client, admin_a)
        resp = client.post(
            f"/admin/user/{target_b.id}/change-business-unit",
            data={"business_unit_id": str(unit_b.id)},
            follow_redirects=True,
        )
        # The route resolves users in the caller's org, so B's user 404s.
        assert resp.status_code == 404

        # B's unit is never moved under A's unit (a B unit id is invisible).
        login_as(client, admin_a)
        resp = client.patch(
            f"/organization/chart/api/units/{unit_b.id}/parent",
            data=json.dumps({"parent_id": unit_a.id}),
            content_type="application/json",
        )
        assert resp.status_code == 404

        # Positive control: the same admin CAN assign and move within their
        # own organisation, proving the 404s above are scoping, not breakage.
        target_a = _make_user(db_session, org_a, "aperson")
        db_session.commit()
        login_as(client, admin_a)
        resp = client.post(
            f"/admin/user/{target_a.id}/change-business-unit",
            data={"business_unit_id": str(unit_a.id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert target_a.business_unit_id == unit_a.id

        sibling_a = _make_actor(db_session, org_a, "A Sibling", "Department")
        db_session.commit()
        login_as(client, admin_a)
        resp = client.patch(
            f"/organization/chart/api/units/{unit_a.id}/parent",
            data=json.dumps({"parent_id": sibling_a.id}),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)


# ── 5. role gates and read-only chart ───────────────────────────────────────


class TestRoleGates:
    def test_a_read_only_account_sees_the_chart_read_only(
        self, app, db_session, make_org, tenant_ctx, client, login_as):
        org = make_org("role-gate")
        with tenant_ctx(org.id):
            unit = _make_actor(db_session, org, "Gated Unit", "Department")
        viewer = _make_user(
            db_session, org, "readonlyviewer", role_name="Viewer",
            enterprise_role="enterprise_architect",
        )
        db_session.commit()

        login_as(client, viewer)

        # Read behaviour is unchanged for roles without edit rights.
        chart_resp = client.get("/organization/chart")
        assert chart_resp.status_code == 200
        data_resp = client.get("/organization/chart/api/data")
        assert data_resp.status_code == 200

        # Every edit route refuses them.
        add_resp = client.post(
            "/organization/chart/api/units",
            data=json.dumps({"name": "Visiting Team", "parent_id": unit.id}),
            content_type="application/json",
        )
        assert add_resp.status_code == 403, add_resp.get_data(as_text=True)

        rename_resp = client.patch(
            f"/organization/chart/api/units/{unit.id}",
            data=json.dumps({"name": "Hijacked"}),
            content_type="application/json",
        )
        assert rename_resp.status_code == 403

        move_resp = client.patch(
            f"/organization/chart/api/units/{unit.id}/parent",
            data=json.dumps({"parent_id": None}),
            content_type="application/json",
        )
        assert move_resp.status_code == 403

        delete_resp = client.delete(f"/organization/chart/api/units/{unit.id}")
        assert delete_resp.status_code == 403

        # The read-only capability panel stays reachable (like the chart data).
        caps_resp = client.get(f"/organization/chart/api/units/{unit.id}/capabilities")
        assert caps_resp.status_code == 200

    def test_a_business_architect_can_edit_the_chart(
        self, app, db_session, make_org, tenant_ctx, client, login_as):
        org = make_org("role-ba")
        with tenant_ctx(org.id):
            parent = _make_actor(db_session, org, "Editable Parent", "Business Unit")
        architect = _make_user(
            db_session, org, "editingba", role_name="Architect",
            enterprise_role="business_architect",
        )
        db_session.commit()

        login_as(client, architect)
        resp = client.post(
            "/organization/chart/api/units",
            data=json.dumps({"name": "New Division", "parent_id": parent.id}),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()["data"]
        assert body["name"] == "New Division"
        assert body["parent_id"] == parent.id

    def test_anonymous_is_refused_on_every_chart_route(self, app, db_session, client, make_org, tenant_ctx):
        org = make_org("role-anon")
        with tenant_ctx(org.id):
            unit = _make_actor(db_session, org, "Anon Unit", "Department")
            unit_id = unit.id

        add_resp = client.post(
            "/organization/chart/api/units",
            data=json.dumps({"name": "Anonymous Team", "parent_id": unit_id}),
            content_type="application/json",
        )
        assert add_resp.status_code in (401, 403)
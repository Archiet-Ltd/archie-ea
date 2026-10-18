"""Org-chart editing, second round: loops in the hierarchy, removal that keeps
unrelated relationships, input validation on the edit routes, role-aware edit
controls, native-dialog detection, and the shared business-unit assignment
helpers used by both admin route layers."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.test_org_chart_editing import (
    _link_actors,
    _make_actor,
    _make_user,
)

pytestmark = pytest.mark.usefixtures("db_session")

ROOT = Path(__file__).resolve().parent.parent


def _loop(db_session, org):
    top = _make_actor(db_session, org, "Loop top", "Business Unit")
    mid = _make_actor(db_session, org, "Loop mid")
    leaf = _make_actor(db_session, org, "Loop leaf", "Team")
    _link_actors(db_session, top, mid)
    _link_actors(db_session, mid, leaf)
    _link_actors(db_session, leaf, top)
    return top, mid, leaf


def _architect(db_session, org, label):
    return _make_user(db_session, org, label, role_name="Architect",
                      enterprise_role="business_architect")


# ── loops in the hierarchy ──────────────────────────────────────────────────


class TestHierarchyLoop:
    def test_capabilities_panel_answers_for_a_unit_inside_a_loop(
        self, app, db_session, make_org, tenant_ctx, client, login_as
    ):
        org = make_org("loop-panel")
        with tenant_ctx(org.id):
            top, _mid, _leaf = _loop(db_session, org)
        architect = _architect(db_session, org, "looppanel")
        db_session.commit()
        login_as(client, architect)
        resp = client.get(f"/organization/chart/api/units/{top.id}/capabilities")
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_a_unit_in_a_loop_can_be_moved_out_of_it(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.organization import service

        org = make_org("loop-move")
        with tenant_ctx(org.id):
            top, mid, leaf = _loop(db_session, org)
            outside = _make_actor(db_session, org, "Outside", "Business Unit")
            db_session.flush()
            # A move of a unit in the loop to a unit outside it works.
            result = service.move_unit(unit_id=mid.id, new_parent_id=outside.id)
            assert result["parent_id"] == outside.id
            # Detaching another unit of the loop works too.
            result = service.move_unit(unit_id=top.id, new_parent_id=None)
            assert result["parent_id"] is None

    def test_a_move_into_a_descendant_is_still_refused_inside_a_loop(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.organization import service

        org = make_org("loop-cycle")
        with tenant_ctx(org.id):
            top, mid, _leaf = _loop(db_session, org)
            with pytest.raises(ValueError):
                service.move_unit(unit_id=mid.id, new_parent_id=mid.id)


# ── removal keeps unrelated relationships ───────────────────────────────────


class TestRemoveKeepsOtherRelationships:
    def _serving(self, db_session, org, source, target):
        from app.models.archimate_core import ArchiMateRelationship

        rel = ArchiMateRelationship(
            type="Serving",
            source_id=source.archimate_element_id,
            target_id=target.archimate_element_id,
            organization_id=org.id,
        )
        db_session.add(rel)
        db_session.flush()
        return rel

    def test_removal_is_refused_and_every_relationship_survives(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.models.archimate_core import ArchiMateRelationship
        from app.modules.organization import service

        org = make_org("rm-keep")
        with tenant_ctx(org.id):
            parent = _make_actor(db_session, org, "Parent", "Business Unit")
            unit = _make_actor(db_session, org, "Leaf unit", "Team")
            other = _make_actor(db_session, org, "Other", "Team")
            _link_actors(db_session, parent, unit)
            serving = self._serving(db_session, org, unit, other)
            incoming = self._serving(db_session, org, other, unit)
            serving_id, incoming_id = serving.id, incoming.id
            db_session.commit()

            with pytest.raises(ValueError) as exc:
                service.remove_unit(unit_id=unit.id)
            assert "relationship" in str(exc.value).lower()
            assert db_session.get(ArchiMateRelationship, serving_id) is not None
            assert db_session.get(ArchiMateRelationship, incoming_id) is not None
            assert service.get_org_unit(unit.id) is not None

    def test_a_plain_leaf_with_only_its_parent_link_is_removed(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.organization import service

        org = make_org("rm-plain")
        with tenant_ctx(org.id):
            parent = _make_actor(db_session, org, "Parent", "Business Unit")
            unit = _make_actor(db_session, org, "Plain leaf", "Team")
            _link_actors(db_session, parent, unit)
            db_session.commit()
            unit_id = unit.id
            service.remove_unit(unit_id=unit_id)
            assert service.get_org_unit(unit_id) is None
            assert service.get_org_unit(parent.id) is not None


# ── input validation ────────────────────────────────────────────────────────


class TestParentIdValidation:
    @pytest.mark.parametrize("bad", ["12", "abc", True, {"a": 1}, [1], 1.5])
    def test_non_integer_parent_id_is_a_400_on_create_and_move(
        self, app, db_session, make_org, tenant_ctx, client, login_as, bad
    ):
        org = make_org("pid")
        with tenant_ctx(org.id):
            top = _make_actor(db_session, org, "Top", "Business Unit")
            mid = _make_actor(db_session, org, "Mid")
            _link_actors(db_session, top, mid)
        architect = _architect(db_session, org, "pid")
        db_session.commit()

        login_as(client, architect)
        resp = client.post(
            "/organization/chart/api/units",
            data=json.dumps({"parent_id": bad, "name": "x"}),
            content_type="application/json",
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)
        login_as(client, architect)
        resp = client.patch(
            f"/organization/chart/api/units/{top.id}/parent",
            data=json.dumps({"parent_id": bad}),
            content_type="application/json",
        )
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_null_parent_still_detaches_to_root(
        self, app, db_session, make_org, tenant_ctx, client, login_as
    ):
        org = make_org("pid-null")
        with tenant_ctx(org.id):
            top = _make_actor(db_session, org, "Top", "Business Unit")
            mid = _make_actor(db_session, org, "Mid")
            _link_actors(db_session, top, mid)
        architect = _architect(db_session, org, "pidnull")
        db_session.commit()
        login_as(client, architect)
        resp = client.patch(
            f"/organization/chart/api/units/{mid.id}/parent",
            data=json.dumps({"parent_id": None}),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)


# ── edit controls follow the edit roles ─────────────────────────────────────


class TestEditControlsFollowRoles:
    def test_read_only_account_does_not_get_the_edit_controls(
        self, app, db_session, make_org, tenant_ctx, client, login_as
    ):
        org = make_org("ctl-ro")
        viewer = _make_user(db_session, org, "ctlro", role_name="User")
        db_session.commit()
        login_as(client, viewer)
        page = client.get("/organization/chart")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "org-chart-add-child-form" not in html
        assert "org-chart-remove-unit" not in html

    def test_business_architect_gets_the_edit_controls(
        self, app, db_session, make_org, tenant_ctx, client, login_as
    ):
        org = make_org("ctl-ba")
        architect = _architect(db_session, org, "ctlba")
        db_session.commit()
        login_as(client, architect)
        html = client.get("/organization/chart").get_data(as_text=True)
        assert "org-chart-add-child-form" in html
        assert "org-chart-remove-unit" in html


# ── native dialogs ──────────────────────────────────────────────────────────


class TestNativeDialogs:
    @pytest.mark.parametrize("call", ["window.confirm('x')", "window.alert('x')",
                                      "window.prompt('x')", "confirm('x')"])
    def test_ui_contract_flags_window_qualified_native_dialogs(self, tmp_path, call):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "check_ui_contract", ROOT / "scripts" / "check_ui_contract.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sample = tmp_path / "sample.js"
        sample.write_text(f"if ({call}) {{ go(); }}\n", encoding="utf-8")
        found = mod._scan_line_rules(sample, "js")
        assert any(f[0] == "native-dialog" for f in found), found

    def test_platform_modal_confirm_is_not_flagged(self, tmp_path):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "check_ui_contract", ROOT / "scripts" / "check_ui_contract.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sample = tmp_path / "sample.js"
        sample.write_text("if (!(await Platform.modal.confirm('x'))) return;\n",
                          encoding="utf-8")
        assert mod._scan_line_rules(sample, "js") == []

    def test_org_chart_remove_uses_the_platform_modal(self):
        source = (ROOT / "app/static/js/organization/org_chart.js").read_text(
            encoding="utf-8")
        assert not re.search(r"window\.(alert|confirm|prompt)\s*\(", source)
        assert "Platform.modal.confirm" in source


# ── shared business-unit assignment helpers ─────────────────────────────────


class TestSharedAssignmentHelpers:
    def test_choices_list_only_this_organisations_units_with_a_no_unit_entry(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.admin.services.admin_user_service import AdminUserService

        org = make_org("asg-a")
        other = make_org("asg-b")
        with tenant_ctx(other.id):
            theirs = _make_actor(db_session, other, "Theirs", "Team")
        with tenant_ctx(org.id):
            mine = _make_actor(db_session, org, "Mine", "Team")
            choices = AdminUserService.business_unit_choices()
        assert choices[0] == ("", "No unit")
        values = [value for value, _label in choices]
        assert str(mine.id) in values
        assert str(theirs.id) not in values

    def test_assign_returns_the_success_message_and_clears_on_empty(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.admin.services.admin_user_service import AdminUserService

        org = make_org("asg-msg")
        with tenant_ctx(org.id):
            unit = _make_actor(db_session, org, "Ops", "Team")
            user = _make_user(db_session, org, "asgmsg")
            db_session.commit()
            ok, message = AdminUserService.assign_business_unit_and_describe(
                user, str(unit.id))
            assert ok is True
            assert unit.name in message and user.full_name() in message
            assert user.business_unit_id == unit.id
            ok, message = AdminUserService.assign_business_unit_and_describe(user, "")
            assert ok is True and "no unit" in message
            assert user.business_unit_id is None

    def test_assign_refuses_a_foreign_unit_with_its_reason(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.modules.admin.services.admin_user_service import AdminUserService

        org = make_org("asg-ref")
        other = make_org("asg-ref2")
        with tenant_ctx(other.id):
            theirs = _make_actor(db_session, other, "Theirs", "Team")
        with tenant_ctx(org.id):
            user = _make_user(db_session, org, "asgref")
            db_session.commit()
            ok, message = AdminUserService.assign_business_unit_and_describe(
                user, str(theirs.id))
            assert ok is False
            assert "does not belong" in message
            assert user.business_unit_id is None

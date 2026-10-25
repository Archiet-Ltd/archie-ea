"""
Tests for the Business Model Canvas + Operating Model module (Business-Architect
signature artifact — Osterwalder/Pigneur BMC + Ross/Weill operating model).

Covers:
- Model import + column shape (9 BMC blocks + operating_model_type + tenant/
  timestamp columns).
- get_block/set_block/to_dict helpers on the model.
- The module's register(app) + service helpers import cleanly.
- The blueprint registers on a bare Flask app (no full create_app() boot —
  this module is self-contained and doesn't need the whole ORM graph) and
  business_model.index / detail / block-save routes resolve.
"""

import pytest


class TestBusinessModelCanvasModel:
    def test_model_importable(self):
        from app.models.business_model import (
            CANVAS_BLOCKS,
            OPERATING_MODEL_TYPES,
            BusinessModelCanvas,
        )

        assert BusinessModelCanvas is not None
        assert CANVAS_BLOCKS == (
            "key_partners",
            "key_activities",
            "key_resources",
            "value_propositions",
            "customer_relationships",
            "channels",
            "customer_segments",
            "cost_structure",
            "revenue_streams",
        )
        assert OPERATING_MODEL_TYPES == (
            "coordination",
            "unification",
            "replication",
            "diversification",
        )

    def test_table_name(self):
        from app.models.business_model import BusinessModelCanvas

        assert BusinessModelCanvas.__tablename__ == "business_model_canvases"

    def test_has_all_nine_block_columns(self):
        from app.models.business_model import CANVAS_BLOCKS, BusinessModelCanvas

        columns = BusinessModelCanvas.__table__.columns.keys()
        for block in CANVAS_BLOCKS:
            assert block in columns

    def test_has_operating_model_type_column(self):
        from app.models.business_model import BusinessModelCanvas

        assert "operating_model_type" in BusinessModelCanvas.__table__.columns.keys()

    def test_has_tenant_and_timestamp_columns(self):
        from app.models.business_model import BusinessModelCanvas

        columns = BusinessModelCanvas.__table__.columns.keys()
        for col in (
            "id",
            "organization_id",
            "name",
            "description",
            "created_at",
            "updated_at",
        ):
            assert col in columns

    def test_all_new_columns_are_nullable(self):
        """Only organization_id (TenantMixin, defaulted via _default_org_id) may be
        NOT NULL — every column this module adds must be nullable so create_all()
        never conflicts with the reconcile-schema drift-repair step."""
        from app.models.business_model import CANVAS_BLOCKS, BusinessModelCanvas

        table = BusinessModelCanvas.__table__
        for col_name in list(CANVAS_BLOCKS) + [
            "name",
            "description",
            "operating_model_type",
            "created_at",
            "updated_at",
        ]:
            assert table.columns[col_name].nullable is True, f"{col_name} must be nullable"

    def test_get_set_block_round_trip(self):
        from app.models.business_model import BusinessModelCanvas

        canvas = BusinessModelCanvas(name="Test")
        canvas.set_block("key_partners", "Partner A\nPartner B")
        assert canvas.get_block("key_partners") == "Partner A\nPartner B"

    def test_get_set_block_rejects_unknown_key(self):
        from app.models.business_model import BusinessModelCanvas

        canvas = BusinessModelCanvas(name="Test")
        with pytest.raises(ValueError):
            canvas.get_block("not_a_real_block")
        with pytest.raises(ValueError):
            canvas.set_block("not_a_real_block", "x")

    def test_to_dict_includes_all_blocks_and_meta(self):
        from app.models.business_model import CANVAS_BLOCKS, BusinessModelCanvas

        canvas = BusinessModelCanvas(name="Test", operating_model_type="unification")
        data = canvas.to_dict()
        for block in CANVAS_BLOCKS:
            assert block in data
        assert data["name"] == "Test"
        assert data["operating_model_type"] == "unification"


class TestBusinessModelCanvasModule:
    def test_module_register_function_importable(self):
        from app.modules.business_model_canvas import business_model_bp, register

        assert callable(register)
        assert business_model_bp.name == "business_model"
        assert business_model_bp.url_prefix == "/business-model"

    def test_service_importable(self):
        from app.modules.business_model_canvas import service

        assert hasattr(service, "list_canvases")
        assert hasattr(service, "get_canvas_or_none")
        assert hasattr(service, "create_canvas")
        assert hasattr(service, "update_canvas_meta")
        assert not hasattr(service, "save_block"), "block text is no longer written"
        assert hasattr(service, "delete_canvas")


class TestBusinessModelBlueprintRoutes:
    """Register the blueprint on a bare Flask app (no full create_app() boot)
    to verify routing without paying for the whole ORM/app-factory import."""

    @pytest.fixture(scope="module")
    def bare_app(self):
        from flask import Flask

        from app.modules.business_model_canvas.routes import business_model_bp

        flask_app = Flask(__name__)
        flask_app.register_blueprint(business_model_bp)
        return flask_app

    def test_index_endpoint_resolves(self, bare_app):
        with bare_app.test_request_context():
            from flask import url_for

            assert url_for("business_model.index") == "/business-model/"

    def test_detail_endpoint_resolves(self, bare_app):
        with bare_app.test_request_context():
            from flask import url_for

            assert url_for("business_model.detail", canvas_id=1) == "/business-model/1"

    def test_block_text_write_route_is_gone_and_entry_routes_exist(self, bare_app):
        endpoints = {r.endpoint for r in bare_app.url_map.iter_rules()}
        assert "business_model.save_block" not in endpoints
        for name in ("add_entry", "remove_entry", "rename_entry", "add_price", "remove_price", "value_proposition"):
            assert f"business_model.{name}" in endpoints, name

    def test_create_update_delete_routes_registered(self, bare_app):
        endpoints = {r.endpoint for r in bare_app.url_map.iter_rules()}
        assert "business_model.create" in endpoints
        assert "business_model.update_canvas" in endpoints
        assert "business_model.delete_canvas" in endpoints

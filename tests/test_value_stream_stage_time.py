"""
Tests for Value Stream Stage time, frequency and answer rules.

Covers:
- Duration unit is stored and rendered with its unit
- Frequency is stored and rendered
- End-to-end time computation sums durations normalised to minutes
- Missing durations are never treated as zero
- Slowest stage is identified with its capabilities
- Downstream impact lists stages after a given stage
"""

import uuid

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


class TestDurationUnit:
    """Duration unit is stored and rendered with its unit."""

    def test_duration_unit_column_exists(self, app):
        from app.models.unified_capability import ValueStreamStage

        assert hasattr(ValueStreamStage, "duration_unit")
        assert hasattr(ValueStreamStage, "frequency_count")
        assert hasattr(ValueStreamStage, "frequency_period")

    def test_duration_unit_stored_and_retrieved(self, app):
        from app import db
        from app.models.unified_capability import ValueStream, ValueStreamStage
        from app.models.organization import Organization

        suffix = uuid.uuid4().hex[:8]
        with app.test_request_context("/"):
            from flask import g

            org = Organization(name=f"Dur Test Org {suffix}", slug=f"dur-test-{suffix}")
            db.session.add(org)
            db.session.flush()
            g.current_org_id = org.id

            vs = ValueStream(name=f"Dur Test VS {suffix}")
            db.session.add(vs)
            db.session.flush()

            stage = ValueStreamStage(
                name="Test Stage",
                value_stream_id=vs.id,
                stage_order=1,
                target_duration=5,
                duration_unit="days",
                frequency_count=2,
                frequency_period="per_month",
            )
            db.session.add(stage)
            db.session.commit()

            try:
                loaded = ValueStreamStage.query.get(stage.id)
                assert loaded.duration_unit == "days"
                assert loaded.target_duration == 5
                assert loaded.frequency_count == 2
                assert loaded.frequency_period == "per_month"
            finally:
                db.session.delete(stage)
                db.session.delete(vs)
                db.session.delete(org)
                db.session.commit()

    def test_duration_unit_defaults_to_none(self, app):
        from app import db
        from app.models.unified_capability import ValueStream, ValueStreamStage
        from app.models.organization import Organization

        suffix = uuid.uuid4().hex[:8]
        with app.test_request_context("/"):
            from flask import g

            org = Organization(name=f"Dur Def Org {suffix}", slug=f"dur-def-{suffix}")
            db.session.add(org)
            db.session.flush()
            g.current_org_id = org.id

            vs = ValueStream(name=f"Dur Def VS {suffix}")
            db.session.add(vs)
            db.session.flush()

            stage = ValueStreamStage(
                name="Default Stage",
                value_stream_id=vs.id,
                stage_order=1,
            )
            db.session.add(stage)
            db.session.commit()

            try:
                loaded = ValueStreamStage.query.get(stage.id)
                assert loaded.duration_unit is None
                assert loaded.frequency_count is None
                assert loaded.frequency_period is None
            finally:
                db.session.delete(stage)
                db.session.delete(vs)
                db.session.delete(org)
                db.session.commit()


class TestEndToEndTime:
    """End-to-end time computation."""

    def test_e2e_time_sums_durations(self, app):
        from app import db
        from app.models.unified_capability import ValueStream, ValueStreamStage
        from app.models.organization import Organization
        from app.modules.capabilities.services import value_stream_service as vs_service

        suffix = uuid.uuid4().hex[:8]
        with app.test_request_context("/"):
            from flask import g

            org = Organization(name=f"E2E Org {suffix}", slug=f"e2e-{suffix}")
            db.session.add(org)
            db.session.flush()
            g.current_org_id = org.id

            vs = ValueStream(name=f"E2E VS {suffix}")
            db.session.add(vs)
            db.session.flush()

            stage1 = ValueStreamStage(
                name="Stage 1", value_stream_id=vs.id, stage_order=1,
                target_duration=2, duration_unit="hours",
            )
            stage2 = ValueStreamStage(
                name="Stage 2", value_stream_id=vs.id, stage_order=2,
                target_duration=30, duration_unit="minutes",
            )
            stage3 = ValueStreamStage(
                name="Stage 3", value_stream_id=vs.id, stage_order=3,
                target_duration=1, duration_unit="days",
            )
            db.session.add_all([stage1, stage2, stage3])
            db.session.commit()

            try:
                result = vs_service.compute_end_to_end_time(vs.id)
                # 2 hours = 120 min, 30 min, 1 day = 1440 min => total = 1590
                assert result["total_minutes"] == 1590
                assert result["stages_used"] == 3
                assert result["stages_total"] == 3
                assert result["stages_not_recorded"] == []
                assert result["slowest_stage"]["name"] == "Stage 3"
                assert result["slowest_stage"]["duration"] == "1 days"
            finally:
                ValueStreamStage.query.filter_by(value_stream_id=vs.id).delete()
                db.session.delete(vs)
                db.session.delete(org)
                db.session.commit()

    def test_e2e_time_missing_duration_not_zero(self, app):
        from app import db
        from app.models.unified_capability import ValueStream, ValueStreamStage
        from app.models.organization import Organization
        from app.modules.capabilities.services import value_stream_service as vs_service

        suffix = uuid.uuid4().hex[:8]
        with app.test_request_context("/"):
            from flask import g

            org = Organization(name=f"E2E2 Org {suffix}", slug=f"e2e2-{suffix}")
            db.session.add(org)
            db.session.flush()
            g.current_org_id = org.id

            vs = ValueStream(name=f"E2E2 VS {suffix}")
            db.session.add(vs)
            db.session.flush()

            stage1 = ValueStreamStage(
                name="Recorded", value_stream_id=vs.id, stage_order=1,
                target_duration=60, duration_unit="minutes",
            )
            stage2 = ValueStreamStage(
                name="Not Recorded", value_stream_id=vs.id, stage_order=2,
            )
            db.session.add_all([stage1, stage2])
            db.session.commit()

            try:
                result = vs_service.compute_end_to_end_time(vs.id)
                assert result["total_minutes"] == 60
                assert result["stages_used"] == 1
                assert result["stages_total"] == 2
                assert "Not Recorded" in result["stages_not_recorded"]
                assert "Recorded" not in result["stages_not_recorded"]
            finally:
                ValueStreamStage.query.filter_by(value_stream_id=vs.id).delete()
                db.session.delete(vs)
                db.session.delete(org)
                db.session.commit()

    def test_e2e_time_all_missing_returns_none(self, app):
        from app import db
        from app.models.unified_capability import ValueStream, ValueStreamStage
        from app.models.organization import Organization
        from app.modules.capabilities.services import value_stream_service as vs_service

        suffix = uuid.uuid4().hex[:8]
        with app.test_request_context("/"):
            from flask import g

            org = Organization(name=f"E2E3 Org {suffix}", slug=f"e2e3-{suffix}")
            db.session.add(org)
            db.session.flush()
            g.current_org_id = org.id

            vs = ValueStream(name=f"E2E3 VS {suffix}")
            db.session.add(vs)
            db.session.flush()

            stage = ValueStreamStage(
                name="No Duration", value_stream_id=vs.id, stage_order=1,
            )
            db.session.add(stage)
            db.session.commit()

            try:
                result = vs_service.compute_end_to_end_time(vs.id)
                assert result["total_minutes"] is None
                assert result["stages_used"] == 0
                assert result["stages_total"] == 1
            finally:
                ValueStreamStage.query.filter_by(value_stream_id=vs.id).delete()
                db.session.delete(vs)
                db.session.delete(org)
                db.session.commit()


class TestDownstreamImpact:
    """Downstream impact computation."""

    def test_downstream_lists_stages_after_trigger(self, app):
        from app import db
        from app.models.unified_capability import ValueStream, ValueStreamStage
        from app.models.organization import Organization
        from app.modules.capabilities.services import value_stream_service as vs_service

        suffix = uuid.uuid4().hex[:8]
        with app.test_request_context("/"):
            from flask import g

            org = Organization(name=f"DS Org {suffix}", slug=f"ds-{suffix}")
            db.session.add(org)
            db.session.flush()
            g.current_org_id = org.id

            vs = ValueStream(name=f"DS VS {suffix}")
            db.session.add(vs)
            db.session.flush()

            stage1 = ValueStreamStage(
                name="Intake", value_stream_id=vs.id, stage_order=1,
            )
            stage2 = ValueStreamStage(
                name="Process", value_stream_id=vs.id, stage_order=2,
            )
            stage3 = ValueStreamStage(
                name="Deliver", value_stream_id=vs.id, stage_order=3,
            )
            db.session.add_all([stage1, stage2, stage3])
            db.session.commit()

            try:
                result = vs_service.stages_downstream_of(vs.id, "Intake")
                assert result["trigger_stage"] == "Intake"
                assert len(result["downstream_stages"]) == 2
                assert result["downstream_stages"][0]["name"] == "Process"
                assert result["downstream_stages"][1]["name"] == "Deliver"
            finally:
                ValueStreamStage.query.filter_by(value_stream_id=vs.id).delete()
                db.session.delete(vs)
                db.session.delete(org)
                db.session.commit()

    def test_downstream_last_stage_returns_empty(self, app):
        from app import db
        from app.models.unified_capability import ValueStream, ValueStreamStage
        from app.models.organization import Organization
        from app.modules.capabilities.services import value_stream_service as vs_service

        suffix = uuid.uuid4().hex[:8]
        with app.test_request_context("/"):
            from flask import g

            org = Organization(name=f"DS2 Org {suffix}", slug=f"ds2-{suffix}")
            db.session.add(org)
            db.session.flush()
            g.current_org_id = org.id

            vs = ValueStream(name=f"DS2 VS {suffix}")
            db.session.add(vs)
            db.session.flush()

            stage1 = ValueStreamStage(
                name="Only", value_stream_id=vs.id, stage_order=1,
            )
            db.session.add(stage1)
            db.session.commit()

            try:
                result = vs_service.stages_downstream_of(vs.id, "Only")
                assert result["trigger_stage"] == "Only"
                assert len(result["downstream_stages"]) == 0
            finally:
                ValueStreamStage.query.filter_by(value_stream_id=vs.id).delete()
                db.session.delete(vs)
                db.session.delete(org)
                db.session.commit()

    def test_downstream_unknown_stage_returns_error(self, app):
        from app import db
        from app.models.unified_capability import ValueStream
        from app.models.organization import Organization
        from app.modules.capabilities.services import value_stream_service as vs_service

        suffix = uuid.uuid4().hex[:8]
        with app.test_request_context("/"):
            from flask import g

            org = Organization(name=f"DS3 Org {suffix}", slug=f"ds3-{suffix}")
            db.session.add(org)
            db.session.flush()
            g.current_org_id = org.id

            vs = ValueStream(name=f"DS3 VS {suffix}")
            db.session.add(vs)
            db.session.commit()

            try:
                result = vs_service.stages_downstream_of(vs.id, "Nonexistent")
                assert "error" in result
            finally:
                db.session.delete(vs)
                db.session.delete(org)
                db.session.commit()


class TestServiceStageFields:
    """Service-level stage CRUD handles new fields."""

    def test_create_stage_with_duration_unit(self, app):
        from app import db
        from app.models.unified_capability import ValueStream, ValueStreamStage
        from app.models.organization import Organization
        from app.modules.capabilities.services import value_stream_service as vs_service

        suffix = uuid.uuid4().hex[:8]
        with app.test_request_context("/"):
            from flask import g

            org = Organization(name=f"CR Org {suffix}", slug=f"cr-{suffix}")
            db.session.add(org)
            db.session.flush()
            g.current_org_id = org.id

            vs = ValueStream(name=f"CR VS {suffix}")
            db.session.add(vs)
            db.session.flush()

            try:
                stage = vs_service.create_stage(vs.id, {
                    "name": "Test Stage",
                    "target_duration": "3",
                    "duration_unit": "hours",
                    "frequency_count": "4",
                    "frequency_period": "per_month",
                })
                assert stage.duration_unit == "hours"
                assert stage.target_duration == 3
                assert stage.frequency_count == 4
                assert stage.frequency_period == "per_month"
            finally:
                ValueStreamStage.query.filter_by(value_stream_id=vs.id).delete()
                db.session.delete(vs)
                db.session.delete(org)
                db.session.commit()

    def test_update_stage_duration_unit(self, app):
        from app import db
        from app.models.unified_capability import ValueStream, ValueStreamStage
        from app.models.organization import Organization
        from app.modules.capabilities.services import value_stream_service as vs_service

        suffix = uuid.uuid4().hex[:8]
        with app.test_request_context("/"):
            from flask import g

            org = Organization(name=f"UP Org {suffix}", slug=f"up-{suffix}")
            db.session.add(org)
            db.session.flush()
            g.current_org_id = org.id

            vs = ValueStream(name=f"UP VS {suffix}")
            db.session.add(vs)
            db.session.flush()

            stage = ValueStreamStage(
                name="Updatable", value_stream_id=vs.id, stage_order=1,
            )
            db.session.add(stage)
            db.session.commit()

            try:
                updated = vs_service.update_stage(stage.id, {
                    "target_duration": "120",
                    "duration_unit": "minutes",
                    "frequency_count": "1",
                    "frequency_period": "per_order",
                })
                assert updated.duration_unit == "minutes"
                assert updated.target_duration == 120
                assert updated.frequency_count == 1
                assert updated.frequency_period == "per_order"
            finally:
                ValueStreamStage.query.filter_by(value_stream_id=vs.id).delete()
                db.session.delete(vs)
                db.session.delete(org)
                db.session.commit()

    def test_stage_to_dict_includes_new_fields(self, app):
        from app import db
        from app.models.unified_capability import ValueStream, ValueStreamStage
        from app.models.organization import Organization
        from app.modules.capabilities.services.value_stream_service import _stage_to_dict

        suffix = uuid.uuid4().hex[:8]
        with app.test_request_context("/"):
            from flask import g

            org = Organization(name=f"D Org {suffix}", slug=f"d-{suffix}")
            db.session.add(org)
            db.session.flush()
            g.current_org_id = org.id

            vs = ValueStream(name=f"D VS {suffix}")
            db.session.add(vs)
            db.session.flush()

            stage = ValueStreamStage(
                name="Dict Test", value_stream_id=vs.id, stage_order=1,
                target_duration=4, duration_unit="days",
                frequency_count=3, frequency_period="per_quarter",
            )
            db.session.add(stage)
            db.session.commit()

            try:
                d = _stage_to_dict(stage)
                assert d["duration_unit"] == "days"
                assert d["frequency_count"] == 3
                assert d["frequency_period"] == "per_quarter"
                assert d["target_duration"] == 4
            finally:
                db.session.delete(stage)
                db.session.delete(vs)
                db.session.delete(org)
                db.session.commit()
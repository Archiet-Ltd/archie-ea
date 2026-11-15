"""vsedit01: value stream detail editors open on their first field, and the
first and last stage read as trigger and outcome.

Covers the two acceptance criteria:
- each editor dialog (stage create, stage edit, value stream edit) opens with
  focus on its first field, never on Close;
- the first stage reads as the stream's trigger and the last as the outcome,
  in words a person reads on the detail page.
"""

import uuid

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app, db
    from app.models.user import Role

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.app_context():
        db.create_all()
        Role.insert_roles()

    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _make_org(db, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(
        name="%s Org %s" % (label, suffix),
        slug="%s-org-%s" % (label.lower().replace(" ", "-"), suffix),
    )
    db.session.add(org)
    db.session.flush()
    db.session.commit()
    return org.id


def _make_user(db, org_id, label, enterprise_role, role_name="Administrator"):
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email="%s-%s@example.com" % (label.lower(), suffix),
        first_name=label,
        last_name="Journey",
        organization_id=org_id,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    db.session.add(user)
    db.session.flush()

    if role_name:
        role = Role.query.filter_by(name=role_name).first()
        if role is None:
            Role.insert_roles()
            role = Role.query.filter_by(name=role_name).first()
        user.role = role

    db.session.commit()
    return user.id


def _login(client, user_id, app):
    from flask import g, has_app_context
    from tests._session_test_helpers import mint_test_sid

    _sid = mint_test_sid(user_id, app=client.application)
    assert _sid, "test login did not register a session"
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        sess["_sid"] = _sid

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)


def _seed_value_stream_with_stages(app, org_id):
    """Create a value stream with three stages and return (vs_id, stage_ids)."""
    from app import db
    from app.models.unified_capability import ValueStream, ValueStreamStage
    from app.modules.capabilities.services import value_stream_service as vs_service

    suffix = uuid.uuid4().hex[:8]

    with app.test_request_context():
        from flask import g as _g

        _g.current_org_id = org_id
        vs = vs_service.create_value_stream(
            {"name": f"Test Value Stream {suffix}", "code": f"TVS-{suffix}"}
        )

    stage_ids = []
    for order, name in enumerate(["Identify", "Fulfill", "Close"], start=1):
        stage = ValueStreamStage(
            name=name, value_stream_id=vs.id, stage_order=order
        )
        db.session.add(stage)
        stage_ids.append(stage.id)
    db.session.commit()

    return vs.id, stage_ids


class TestEditorsOpenOnFirstField:
    """Each editor dialog's first field carries autofocus so focus lands there,
    not on the Close button."""

    def test_stage_create_dialog_autofocuses_name_field(self, app, client):
        """The Add Stage dialog opens with focus on the Name field."""
        with app.app_context():
            from app import db
            org_id = _make_org(db, "VsCreate")
            user_id = _make_user(db, org_id, "vscreate", "enterprise_architect")
        _login(client, user_id, app)

        with app.app_context():
            from app import db
            vs_id, _ = _seed_value_stream_with_stages(app, org_id)

        page = client.get(f"/value-streams/{vs_id}")
        assert page.status_code == 200
        body = page.get_data(as_text=True)

        # The stage-create-modal's first field (Name) must carry autofocus.
        assert 'id="stage-name"' in body
        assert 'autofocus' in body
        # autofocus must be on the Name input element, not on the Close button.
        # The Close button is a <button> with data-modal-close; the input is an
        # <input> with id="stage-name". autofocus must appear on the input.
        import re
        # Find the input tag containing id="stage-name" and check it has autofocus
        name_input_match = re.search(r'<input[^>]*id="stage-name"[^>]*>', body)
        assert name_input_match is not None, "stage-name input tag not found"
        assert "autofocus" in name_input_match.group(0), (
            "autofocus must be on the stage-name input element"
        )
        # The Close button must NOT have autofocus
        close_match = re.search(
            r'<button[^>]*data-modal-close="stage-create-modal"[^>]*>', body
        )
        assert close_match is not None, "stage-create-modal Close button not found"
        assert "autofocus" not in close_match.group(0), (
            "Close button must not have autofocus"
        )

    def test_stage_edit_dialog_autofocuses_name_field(self, app, client):
        """The Edit Stage dialog opens with focus on the Name field."""
        with app.app_context():
            from app import db
            org_id = _make_org(db, "VsEdit")
            user_id = _make_user(db, org_id, "vseditedit", "enterprise_architect")
        _login(client, user_id, app)

        with app.app_context():
            from app import db
            vs_id, _ = _seed_value_stream_with_stages(app, org_id)

        page = client.get(f"/value-streams/{vs_id}")
        assert page.status_code == 200
        body = page.get_data(as_text=True)

        # The stage-edit-modal's first field (Name) must carry autofocus.
        assert 'id="stage-edit-name"' in body
        assert 'autofocus' in body
        import re
        name_input_match = re.search(r'<input[^>]*id="stage-edit-name"[^>]*>', body)
        assert name_input_match is not None, "stage-edit-name input tag not found"
        assert "autofocus" in name_input_match.group(0), (
            "autofocus must be on the stage-edit-name input element"
        )
        close_match = re.search(
            r'<button[^>]*data-modal-close="stage-edit-modal"[^>]*>', body
        )
        assert close_match is not None, "stage-edit-modal Close button not found"
        assert "autofocus" not in close_match.group(0), (
            "Close button must not have autofocus"
        )

    def test_value_stream_edit_dialog_autofocuses_name_field(self, app, client):
        """The Edit Value Stream dialog opens with focus on the Name field."""
        with app.app_context():
            from app import db
            org_id = _make_org(db, "VsStreamEdit")
            user_id = _make_user(db, org_id, "vsstreamedit", "enterprise_architect")
        _login(client, user_id, app)

        with app.app_context():
            from app import db
            vs_id, _ = _seed_value_stream_with_stages(app, org_id)

        page = client.get(f"/value-streams/{vs_id}")
        assert page.status_code == 200
        body = page.get_data(as_text=True)

        # The vs-edit-modal's first field (Name) must carry autofocus.
        assert 'id="vs-edit-name"' in body
        assert 'autofocus' in body
        import re
        name_input_match = re.search(r'<input[^>]*id="vs-edit-name"[^>]*>', body)
        assert name_input_match is not None, "vs-edit-name input tag not found"
        assert "autofocus" in name_input_match.group(0), (
            "autofocus must be on the vs-edit-name input element"
        )
        close_match = re.search(
            r'<button[^>]*data-modal-close="vs-edit-modal"[^>]*>', body
        )
        assert close_match is not None, "vs-edit-modal Close button not found"
        assert "autofocus" not in close_match.group(0), (
            "Close button must not have autofocus"
        )


class TestFirstStageIsTriggerLastIsOutcome:
    """The first stage reads as the stream's trigger and the last as the
    outcome, in words a person reads on the detail page."""

    def test_trigger_before_first_stage_and_outcome_after_last(
        self, app, client
    ):
        """Trigger appears before the first stage chevron, Outcome after the
        last stage chevron."""
        with app.app_context():
            from app import db
            org_id = _make_org(db, "VsTriggerOutcome")
            user_id = _make_user(db, org_id, "vstrigger", "enterprise_architect")
        _login(client, user_id, app)

        with app.app_context():
            from app import db
            vs_id, stage_ids = _seed_value_stream_with_stages(app, org_id)

        page = client.get(f"/value-streams/{vs_id}")
        assert page.status_code == 200
        body = page.get_data(as_text=True)

        # Trigger and Outcome must both appear on the page.
        assert "Trigger" in body, "Trigger label missing from detail page"
        assert "Outcome" in body, "Outcome label missing from detail page"

        # Trigger must appear before the first stage card.
        trigger_pos = body.index("Trigger")
        first_card_pos = body.index("data-stage-card")
        assert trigger_pos < first_card_pos, (
            "Trigger must appear before the first stage chevron"
        )

        # Outcome must appear after the last stage card.
        outcome_pos = body.rindex("Outcome")
        last_card_pos = body.rindex("data-stage-card")
        assert outcome_pos > last_card_pos, (
            "Outcome must appear after the last stage chevron"
        )

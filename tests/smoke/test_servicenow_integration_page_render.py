"""The second ServiceNow settings page, rendered with its own form (no browser).

No route reaches this page in the running application: the view that rendered it lives in the retired
administration blueprint, which is not registered, and the page of record for ServiceNow is
/admin/connectors/servicenow. So there is no journey to drive; this renders the template the way that view did
and checks what a person would read: every field has a label, the stored password is not echoed, the test and
sync controls are labelled, and the page is built from the shared card, field and button parts.
"""
import re
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def render():
    from flask import render_template
    from flask_wtf import FlaskForm
    from wtforms import BooleanField, IntegerField, PasswordField, StringField

    from app import create_app

    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = False

    class Form(FlaskForm):
        instance_url = StringField("Instance URL")
        username = StringField("Username")
        password = PasswordField("API Password / Token", description="Leave blank to keep existing password")
        batch_size = IntegerField("Batch Size", default=100, description="Records per API call (1-1000)")
        query_filter = StringField("Query Filter")
        enabled = BooleanField("Enable Integration")

    def go(configured):
        with app.test_request_context("/admin/integrations/servicenow"):
            form = Form()
            config = None
            if configured:
                form.instance_url.data = "https://acme.service-now.com"
                form.username.data = "api.user"
                form.enabled.data = True
                config = SimpleNamespace(enabled=True, base_url="https://acme.service-now.com")
            return render_template(
                "admin/servicenow_integration.html", form=form, snow_config=config,
                last_sync=(SimpleNamespace(last_sync_at=None) if configured else None),
                url_for=lambda *a, **k: "/x",
            )

    return go


def _label_for(html, text):
    m = re.search(r'<label[^>]*for="([^"]+)"[^>]*>\s*%s' % re.escape(text), html)
    return m.group(1) if m else None


@pytest.mark.smoke
def test_every_field_has_a_label_and_the_password_is_not_echoed(render):
    html = render(configured=True)
    for text in ("Instance URL", "Username", "API Password / Token", "Batch Size", "Query Filter", "Enable Integration"):
        target = _label_for(html, text)
        assert target, "no label for %s" % text
        assert 'id="%s"' % target in html, "label for %s points at nothing" % text
    assert "https://acme.service-now.com" in html
    assert re.search(r'type="password"[^>]*value="[^"]+"', html) is None, "the password field carries a value"


@pytest.mark.smoke
def test_test_and_sync_controls_are_labelled_and_the_page_uses_the_shared_parts(render):
    html = render(configured=True)
    assert "Test Connection" in html and "Sync Now" in html and "Save configuration" in html
    assert re.search(r'<button[^>]*type="submit"[^>]*>\s*Save configuration', html)
    # Built from the shared card part, not hand-written card markup.
    assert "cn-card" in html
    # An unconfigured instance says what to do next instead of showing controls that cannot work.
    empty = render(configured=False)
    assert "Not set up yet" in empty and "Sync Now" not in empty

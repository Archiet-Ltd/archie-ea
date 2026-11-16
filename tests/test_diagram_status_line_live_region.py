"""The shared diagram status line keeps its polite live region for the editors that rely on it (process, entity);
only the Composer, whose keyboard walk is announced by the shared announcer, turns it off."""
import re


def _status_text_tag(app, **kwargs):
    with app.test_request_context("/"):
        tpl = app.jinja_env.get_template("components/diagram/status_line.html")
        html = str(tpl.make_module({}).diagram_status_line(**kwargs))
    return re.search(r'<span[^>]*data-testid="status-text"[^>]*>', html).group(0)


def test_process_and_entity_editors_announce_their_messages(app):
    tag = _status_text_tag(app, noun="step", noun_plural="steps", relation_noun="flow", relation_noun_plural="flows")
    assert 'role="status"' in tag and 'aria-live="polite"' in tag


def test_composer_status_line_is_not_a_second_live_region(app):
    tag = _status_text_tag(app, live=False)
    assert "aria-live" not in tag and 'role="status"' not in tag

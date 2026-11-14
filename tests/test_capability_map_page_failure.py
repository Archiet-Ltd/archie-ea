"""A capability map that cannot be built is a server error, not a page with a gap in it."""

import pytest


@pytest.fixture
def failing_count(app, monkeypatch):
    """The capability count raises; exceptions reach the error handler as they do when served."""
    from app.modules.capabilities.services import capability_count_service

    def broken():
        raise RuntimeError("database did not answer")

    monkeypatch.setattr(capability_count_service, "count_capabilities", broken)
    monkeypatch.setitem(app.config, "LOGIN_DISABLED", True)
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)


def test_capability_map_answers_500_with_the_plain_error_page_when_the_count_fails(app, failing_count):
    reply = app.test_client().get("/capability-map/", headers={"Accept": "text/html"})

    body = reply.get_data(as_text=True)
    assert reply.status_code == 500
    assert "Something went wrong on our side" in body
    assert "capability-total" not in body

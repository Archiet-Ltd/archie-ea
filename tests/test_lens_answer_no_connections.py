"""An item that reaches nothing says so in words with the next step, never "0 connections reached"."""

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def test_empty_reach_is_explained(app):
    with app.test_request_context("/"):
        from flask import render_template

        def card(rows):
            return render_template(
                "ai_chat/_lens_answer.html", lens="risk", element={"name": "Billing"},
                data={"risks": [{
                    "risk_id": 1, "name": "Lock-in", "title": "Lock-in", "affected_rows": rows,
                    "touches": [], "status": "open"}]})

        empty, two = card([]), card([{"name": "a"}, {"name": "b"}])
    assert "No connections are recorded for this yet" in empty
    assert "0 connection" not in empty
    assert "2 connections reached" in two

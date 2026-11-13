"""The portfolio says plainly when every initiative serves an objective, and never prints a bare zero.

"0 initiatives serving no objective" was a link to an empty list. When nothing is unlinked the line now states
the good news in words; an organisation that does have unlinked initiatives still gets the count as a link.
"""
from tests.test_initiative_goal_links import _clear_login_cache, _goal, _initiative, world  # noqa: F401
from tests.test_initiative_goal_links import _post


def test_every_initiative_linked_reads_as_a_sentence_and_the_other_organisation_keeps_its_count(world, db_session):  # noqa: F811
    org_a, client_a, _ = world["a"]
    org_b, client_b, _ = world["b"]
    goal, initiative = _goal(db_session, org_a), _initiative(db_session, org_a)
    _post(client_a, "/portfolio/initiatives/%d/goals" % initiative.id, {"goal_id": goal.id})
    _initiative(db_session, org_b)

    _clear_login_cache()
    mine = client_a.get("/portfolio/").get_data(as_text=True)
    _clear_login_cache()
    theirs = client_b.get("/portfolio/").get_data(as_text=True)

    assert "Every initiative serves an objective" in mine
    assert "serving no objective" not in mine
    assert "1 initiative serving no objective" in theirs
    assert "Every initiative serves an objective" not in theirs

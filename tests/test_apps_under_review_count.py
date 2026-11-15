"""The Apps Under Review card states every application under review, even when the list beneath shows only ten."""
import re

from tests.test_requirement_scores_need_inputs import _architect


def _applications(db_session, org, count, stage="under_review"):
    from app.models.application_portfolio import ApplicationComponent

    for n in range(count):
        db_session.add(ApplicationComponent(
            name="Review %s %02d" % (org.slug, n), organization_id=org.id, lifecycle_status=stage))
    db_session.flush()


def test_the_card_counts_every_application_under_review_and_says_the_list_is_cut(
        db_session, make_org, client, login_as):
    ours, theirs = make_org("review-ours"), make_org("review-theirs")
    user = _architect(db_session, ours, "review")
    _applications(db_session, ours, 12)
    _applications(db_session, theirs, 5)

    login_as(client, user)
    page = client.get("/enterprise/analysis/ai-architecture")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    card = re.search(r'data-testid="ai-analysis-apps-review"(.{0,3000})', html, re.S)
    words = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", card.group(0)))
    assert re.search(r"\b12\b", words) and not re.search(r"\b10\b", words), words
    assert "Showing the first 10 of 12 applications." in html
    assert "review-theirs" not in html

"""The opening screen reads this tenant's portfolio — and every state is designed.

/ai-chat/recommendations answers 500 with {"error": ..., "alerts": [],
"recommendations": [], "summary": {"total": 0}}. That body is indistinguishable
from "nothing to report" unless the status is checked, so an outage would render
as a clean portfolio and a health score of 0%.

CLAUDE.md names this exact failure: fetch does not reject on 404/500, and
`if (response.ok)` with no `else` leaves metrics at their 0 initialiser, where a
0 meaning "not computed" cannot be told apart from a measured zero.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _panels():
    return (ROOT / "app/static/js/ai_chat/panels.js").read_text(encoding="utf-8")


def test_a_failed_load_is_not_rendered_as_an_empty_portfolio():
    js = _panels()
    # Both loaders now go through Platform.fetch, which raises on any non-2xx,
    # so the explicit `if (!resp.ok) throw` is gone. The CONTRACT is unchanged and
    # is what this asserts: a non-2xx must not reach the render path, because the
    # endpoint's own 500 body carries empty arrays -- exactly the shape of
    # "nothing to report".
    assert js.count("await Platform.fetch") >= 2, (
        "both the briefing and the alerts loader must use the wrapper that throws "
        "on non-2xx, rather than rendering an error body's empty arrays as a clean "
        "portfolio"
    )
    assert js.count("This is an error, not an empty portfolio") >= 2, (
        "each failed state must say so; a silent empty panel is a lie about the data"
    )


def test_an_uncomputed_health_score_renders_as_an_em_dash():
    """0% is a legitimate measured value, so it cannot double as 'unknown'."""
    js = _panels()
    assert "healthScore.textContent = score === null ? '—'" in js
    assert "typeof data.health_score === 'number'" in js, (
        "|| 0 would turn a missing score into a measured zero"
    )


def test_the_empty_tenant_gets_an_onboarding_path_not_a_blank_panel():
    """Day one, zero applications — the moment the product must be most persuasive."""
    js = _panels()
    assert "Nothing flagged in your portfolio yet" in js
    assert "briefing-start-solution" in js, (
        "the empty state must offer the path that needs no portfolio at all"
    )
    assert "/import" in js, "the empty state must offer to import a portfolio"


def test_the_denominator_is_shown():
    """Three items out of an unstated total is an editorial claim.

    The user cannot see what was ranked above what, or how much was left out.
    """
    js = _panels()
    assert "open finding" in js and "ranked by impact" in js
    assert "summary.total" in js, "the total is not read from the payload"


def test_the_briefing_does_not_invent_a_total():
    """When the payload carries no total, say how many are shown — not a made-up N."""
    js = _panels()
    assert "total === null" in js, (
        "a missing total must fall back to the shown count, never to a fabricated one"
    )


def test_briefing_items_are_escaped():
    """Titles and descriptions are database content going into innerHTML."""
    js = _panels()
    assert "ArchieChat.render.escapeForHtml(question)" in js
    assert js.count("escapeForHtml") >= 3


def test_the_slot_exists_and_announces_itself():
    tpl = (ROOT / "app/templates/ai_chat/index.html").read_text(encoding="utf-8")
    assert 'id="portfolio-briefing"' in tpl
    assert 'aria-busy="true"' in tpl, (
        "a panel that populates asynchronously must announce that it is loading"
    )


def test_expertise_chips_use_contrast_safe_tint_tokens():
    """Normal-size blue chip text must clear WCAG AA in both themes."""
    js = _panels()
    assert "bg-info/10 text-info-emphasis" in js
    assert "bg-primary/10 text-primary rounded-full" not in js

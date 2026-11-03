"""Totals over figures nobody recorded stay unknown; a recorded zero stays zero."""
from app.utils.recorded_figures import as_figure, recorded_sum, sum_recorded


def test_a_total_over_nothing_recorded_is_unknown():
    assert sum_recorded([None, None]) is None
    assert sum_recorded([]) is None
    assert recorded_sum(None, None, None) is None
    assert as_figure(None) is None


def test_a_recorded_zero_and_partial_figures_are_kept():
    assert sum_recorded([0, None]) == 0
    assert recorded_sum(1200, None, 300.5) == 1500.5
    assert as_figure(0) == 0.0

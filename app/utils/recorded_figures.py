"""Totals over figures that may not have been recorded.

A sum over rows where nobody recorded a value is not 0: it is unknown, and a page that shows 0 invents a
measurement. These helpers return ``None`` when no row carried a value, so the page can say "Not recorded".
A real zero that someone recorded still sums to 0.
"""

from __future__ import annotations


def recorded_sum(*values):
    """The sum of the values that were recorded, or ``None`` when none were."""
    known = [float(v) for v in values if v is not None]
    return sum(known) if known else None


def sum_recorded(values):
    """``recorded_sum`` over an iterable; ``None`` when it holds no recorded value."""
    return recorded_sum(*values)


def as_figure(value):
    """A recorded figure as a float, or ``None`` when it was not recorded; a recorded 0 stays 0."""
    return None if value is None else float(value)


def coverage_text(value):
    """A capability coverage as words: "40%", or "Not recorded" when nobody entered one (a recorded 0 is "0%")."""
    if value is None:
        return "Not recorded"
    number = float(value)
    return f"{int(number)}%" if number == int(number) else f"{number:g}%"

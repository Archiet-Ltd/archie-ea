"""Tests for the shared WSJF score function."""
from __future__ import annotations

import pytest
from app.services.wsjf_service import compute_wsjf_score


class TestComputeWsjfScore:
    """WSJF = (BV + TC + RR) / JS, each component defaulting to 1."""

    def test_all_defaults(self):
        """All components default to 1: (1+1+1)/1 = 3.0."""
        assert compute_wsjf_score() == 3.0

    def test_typical_values(self):
        """(10 + 5 + 3) / 2 = 9.0."""
        assert compute_wsjf_score(10, 5, 3, 2) == 9.0

    def test_large_job_size(self):
        """(10 + 10 + 10) / 20 = 1.5."""
        assert compute_wsjf_score(10, 10, 10, 20) == 1.5

    def test_minimum_values(self):
        """All values at minimum 1: (1+1+1)/1 = 3.0."""
        assert compute_wsjf_score(1, 1, 1, 1) == 3.0

    def test_zeros_are_treated_as_one(self):
        """Zero values are treated as 1 to avoid division by zero."""
        assert compute_wsjf_score(0, 0, 0, 0) == 3.0

    def test_none_values_default_to_one(self):
        """None values are treated as 1."""
        assert compute_wsjf_score(None, None, None, None) == 3.0  # type: ignore[arg-type]

    def test_rounding(self):
        """(7 + 3 + 2) / 3 = 4.0 (exact)."""
        assert compute_wsjf_score(7, 3, 2, 3) == 4.0

    def test_fractional_result(self):
        """(5 + 5 + 5) / 7 = 2.14 (rounded to 2 decimal places)."""
        assert compute_wsjf_score(5, 5, 5, 7) == 2.14

    def test_high_values(self):
        """(100 + 80 + 60) / 50 = 4.8."""
        assert compute_wsjf_score(100, 80, 60, 50) == 4.8
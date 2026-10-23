"""Shared WSJF (Weighted Shortest Job First) score function.

The formula is (business_value + time_criticality + risk_reduction) / job_size,
with each component defaulting to 1. This is the one place the formula lives;
both the Requirement model and the element profile service call it.
"""

from __future__ import annotations


def compute_wsjf_score(
    business_value: int = 1,
    time_criticality: int = 1,
    risk_reduction: int = 1,
    job_size: int = 1,
) -> float:
    """Compute the WSJF score from its four components.

    Each component defaults to 1 so a missing value never produces a division
    by zero or a zero score. The formula is the SAFe definition:
    (Business Value + Time Criticality + Risk Reduction) / Job Size.
    """
    bv = max(business_value or 1, 1)
    tc = max(time_criticality or 1, 1)
    rr = max(risk_reduction or 1, 1)
    js = max(job_size or 1, 1)
    return round((bv + tc + rr) / js, 2)
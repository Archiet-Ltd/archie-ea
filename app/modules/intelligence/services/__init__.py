"""Intelligence module services.

T-001 adds ``derivation_runner.py`` (DE-1) and ``reason_codes.py`` (DE-14).
Left structured for later tasks to add their own modules alongside these
without touching either file:

- T-002/T-003 add ``invalidation.py`` (DE-3) and ``recompute_job.py`` (DE-4).
- T-006 adds ``connector_allowlist.py`` (DE-8) — the connector allowlist gate
  is explicitly out of scope for T-001.
- A later change adds ``crosswalk_service.py`` and the external-identifier
  crosswalk model/readers/writer.
"""

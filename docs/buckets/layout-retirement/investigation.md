# Investigation: module-layout retirement (P2, ADR 0004) — corrected state, 2026-09-17

ADR 0004 (dated 2026-07-30) is stale on two counts. Before any retirement work
starts, re-measure rather than trust the doc, per this repo's own documented
lesson (CLAUDE.md: "Read the numbers from X, not from here... re-measure
before trusting either").

## What actually has both layouts today

Checked directly (`app/<domain>` and `app/modules/<domain>` both present):

| Domain | Legacy (`app/<domain>`) | Modules (`app/modules/<domain>`) |
|---|---|---|
| account | yes | yes |
| admin | yes | yes |
| ai_chat | yes | yes |
| auth | yes | yes |
| dashboard | yes | yes |
| monitoring | yes | yes |
| integrations | **no** | yes |

**`integrations` is already fully migrated** — ADR 0004 lists it as one of the
original 7 overlapping domains; the legacy directory no longer exists. This
part of P2 is smaller than the to-be-plan assumed: **6 domains, not 7.**

## The `USE_*_GUARDRAILS` flag list has grown, and doesn't map 1:1 to layout duplication

`app/_bootstrap/blueprints.py`'s current default-on flag list (line 98-103)
has **12** entries: `ACCOUNT`, `ADMIN`, `VENDORS`, `IMPORT_BATCH`,
`SOLUTIONS_STRATEGIC`, `ARCHITECTURE`, `APPLICATIONS`, `CAPABILITIES`,
`AI_CHAT`, `DUPLICATE_DETECTION`, `GOVERNANCE`, `DASHBOARD`. Six of these
(`VENDORS`, `IMPORT_BATCH`, `SOLUTIONS_STRATEGIC`, `ARCHITECTURE`,
`APPLICATIONS`, `CAPABILITIES`, `DUPLICATE_DETECTION`, `GOVERNANCE` — eight,
not six) gate domains with **no legacy `app/<domain>` directory at all** —
these flags switch between two things *within* `app/modules/`, or between a
v1/v2 module implementation, not between the two layouts ADR 0004 is about.
Conflating these with the layout-duplication list would misscope the work.

**`monitoring` and `auth` are not in the current flag list at all** —
`monitoring`'s legacy blueprint registration has already been removed (see
`app/_bootstrap/blueprints.py:1509-1510`: "monitoring module blueprints are
ops tooling not needed in the architect-facing app. `_register_monitoring()`
has been removed"). So `monitoring`'s *routing* duplication is already
resolved. What remains for `monitoring` is a different, smaller problem (see
below) — not the "confirm which serves traffic, delete the loser" pattern
ADR 0004 describes for the other domains.

## `monitoring`: not a clean case

`app/monitoring/` contains four files with no blueprint/route registration —
they are plain service modules: `alerting_service.py`, `metrics_decorator.py`,
`metrics_service.py`, `security_monitoring.py`. These are **still actively
imported** by other legacy-only modules that have no bearing on this bucket:

- `app/ai/audit_trail.py`
- `app/ai/cost_monitor.py`
- `app/import/error_handler.py`
- `app/import/import_audit.py`
- `app/security/upload_monitoring.py`
- `app/workflow/error_recovery.py`
- `app/workflow/manual_intervention.py`

Retiring `app/monitoring/` is therefore not "delete the dead blueprint
fallback" (already done) — it requires either (a) confirming
`app/modules/monitoring/services/` has equivalent functionality these seven
consumers could switch to, and migrating each import, or (b) leaving
`app/monitoring/`'s service files in place indefinitely as shared utility
code with no route duplication, which may be the actually-correct outcome
(a service module living outside `app/modules/` is not automatically the
same defect as a duplicated *routed* domain).

## Recommendation

**Not implemented in this session** — this needs its own scoped task brief
with an accurate current-state table (this document), not ADR 0004's
7-domain, one-shot framing. Suggested sequencing for a future session:

1. **Task A — `account`, `admin`, `ai_chat`, `auth`, `dashboard`**: five
   genuine legacy-vs-modules routing duplicates, each following ADR 0004's
   described sequence (confirm module implementation serves traffic per the
   default-on flag, delete legacy blueprint + fallback branch in
   `blueprints.py`, remove the flag, verify with `tests/test_boot_health.py`
   per domain, one PR per domain — not a single big-bang change).
2. **Task B — `monitoring`**: a distinct, smaller investigation into whether
   its four service files have `app/modules/monitoring/services/` equivalents
   worth migrating consumers to, or whether they're legitimately
   layout-exempt shared utilities.
3. **Not this bucket**: the eight `USE_*_GUARDRAILS` flags that gate
   something other than legacy-vs-modules duplication (`VENDORS`,
   `IMPORT_BATCH`, `SOLUTIONS_STRATEGIC`, `ARCHITECTURE`, `APPLICATIONS`,
   `CAPABILITIES`, `DUPLICATE_DETECTION`, `GOVERNANCE`) — these need their own
   investigation to characterize what they actually switch between before
   anyone assumes they belong to ADR 0004's scope.

No application code changed in this investigation.

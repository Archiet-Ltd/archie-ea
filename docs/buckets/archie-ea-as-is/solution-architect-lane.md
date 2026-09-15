# As-Is: Solution Architecture lane

Author: `solution-architect`. Input: `current-state.md`.

## Structural picture

The app factory (`app/__init__.py`) is a slim orchestrator delegating to
`app/_bootstrap/*.py` — extensions, security, routes, context processors,
assets, services, blueprints, swagger, cli, in that order (order matters:
tenant middleware must install before blueprints). This part is clean and
matches its own documentation.

The real structural problem is the **two parallel code layouts** already
named in the as-is doc: 48 domains under `app/<domain>/`, 36 under
`app/modules/<domain>/`, 8 overlapping (`account`, `admin`, `ai_chat`,
`auth`, `dashboard`, `integrations`, `monitoring`) and switched at boot by
`USE_*_GUARDRAILS` flags defaulting **on**. This is not cosmetic: it means
"which code actually runs for `/admin`" is a runtime decision, not something
readable from the file tree, and a contributor editing the wrong (legacy)
copy of an overlapping domain does nothing.

**Blueprints register non-fatally** — a broken module logs and continues
rather than crashing boot. This is a deliberate resilience choice, but its
cost is real: any `url_for()` to a failed-to-register endpoint raises
`BuildError` and 500s *every* page rendering the sidebar, which is why
`_validate_critical_endpoints()` and the `boot-health` gate exist as a
safety net rather than a nice-to-have.

## One-system-of-record violations beyond capabilities (ADR 0008)

The as-is doc already covers the 6-store capabilities case (the worst one,
with a live gate ratchet at 1). The same pattern is structurally likely
anywhere a domain exists in both layouts — I have not audited all 8
overlapping domains for duplicate models/routes beyond what ADR 0008
documents; that's a gap this pass surfaces rather than closes.

## New: interface_register module (from the uncommitted 2026-09-15 run)

A new `app/modules/interface_register/` exists (routes, services, templates)
that the earlier autonomous run added. Structurally it follows the canonical
`app/modules/<domain>/` pattern correctly — no new duplication introduced.
Whether it duplicates existing SAP/interface tracking elsewhere in the repo
is unverified; the user has not yet decided whether to keep this work at
all, so it is not analyzed further here.

## To-be direction (this lane's opinion, for tech-lead to reconcile)

1. Retiring the 8 overlapping legacy domains is the single highest-leverage
   structural fix available — it removes an entire class of "which copy is
   live" bugs in one motion, and ADR 0004 already names it as the next step.
   Recommend sequencing this **before** any of the 6-capability-store
   consolidation, since some of the duplicate domains likely touch
   capability code paths and doing both at once compounds risk.
2. Do not add a 7th capability store or a 9th overlapping domain while this
   is open — any new work should extend `app/modules/`, never `app/<domain>/`
   directly (already stated as the convention; worth re-stating as a gate
   rather than a comment, since it currently relies on discipline).

**Handoff:** to `tech-lead`, alongside the other three lane docs.

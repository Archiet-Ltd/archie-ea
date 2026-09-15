# As-Is: Security Architecture lane

Author: `security-architect`. Input: `current-state.md`, ADR 0003, ADR 0005.

## Tenant isolation (ADR 0003) — mostly closed, one documented gap remains

Bulk ORM UPDATE/DELETE are tenant-filtered mechanically via `do_orm_execute`
with `with_loader_criteria` — this was previously a gap, now closed (the two
strict xfails encoding it in `tests/test_tenant_isolation.py` are plain
passing tests). **Remaining, real gap:** `Query.get()`/`Session.get()`
bypass the tenant filter on an identity-map *hit* (only filtered on a miss).
Per-request code is unaffected (one request = one tenant = one session);
the actual exposure is anything looping over multiple tenants inside one
session — CLI commands, the scheduler, importers, tests. The ADR is explicit
that this is capped by strict xfails and documented, not fixed, and that
fixing it needs the xfail markers removed in the same change (i.e., it's
scoped and known, not neglected).

## External network posture (ADR 0005) — accepted, egress/CVE items done

Zero browser egress: all 22 vendor libraries served locally with SHA-384
manifest (`VENDOR_MANIFEST.txt`), down from 78 external loads across 39
assets. Status is "Accepted; egress and CVE items done, remainder tracked" —
I have not re-verified what "remainder" currently contains beyond the
excerpt read; worth a follow-up read of the ADR's full consequences section
before treating this as fully closed.

## CI security gates — real, but their current pass/fail state is unverified here

`secret-scan` (gitleaks, full history), `security-sast` (bandit, ratcheted
against `.bandit-baseline.json`), `dependency-audit` (pip-audit, ratcheted).
Per prior session notes, main CI has been broadly red since before
2026-09-15 including CVE and SAST jobs — **this needs a live `gh run list`
check**, not an assumption, before any to-be plan claims a security-clean
starting point.

## AI-surface security (relevant given the AI/ML/NLP roster now exists)

Four AI-specific gates are enforced and, per the last verified baseline,
clean: `ai-evidence-rules`, `ai-tool-guard`, `ai-untrusted-content`,
`ai-approval-honoured`. These are exactly the boundaries
`prompt-security-tester` and `ai-ethics-governance-lead` (new roster
additions) are designed to adversarially re-test rather than trust from a
static gate reading alone — a gate passing means the *pattern* wasn't found
in source, not that a live attack was tried.

## To-be direction (this lane's opinion)

1. Re-verify actual CI status (`gh run list --limit 10`) before this bucket's
   to-be plan assumes anything about current security posture — a stale
   assumption here has already burned this project once (per prior CI-state
   notes: "green reported locally while CI was actually red").
2. The `Query.get()` tenant gap is low-priority to fix in isolation (narrow,
   documented, capped) but should be revisited if the interface_register
   module (or any future importer-style code) starts looping over tenants in
   one session.
3. Recommend `prompt-security-tester` run one real adversarial pass against
   the existing `app/modules/ai_chat/` surface as its first task, independent
   of any new feature — establishing a baseline is more valuable than
   waiting for the next AI feature to test it incidentally.

**Handoff:** to `tech-lead`, alongside the other three lane docs.

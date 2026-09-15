# Bucket brief: archie-ea as-is / to-be analysis

**Objective:** produce a grounded as-is architecture assessment of the
archie-oss platform, then a reconciled to-be direction, using the SDLC
subagent roster as reviewers of the existing system rather than designers of
a new feature.

**Context:** archie-oss is a mature Flask/Postgres enterprise-architecture
platform with a documented, self-admitted debt backlog (ADRs 0002–0008,
`docs/known-issues/`, `verification_baseline.json` ratchets). The point of
this bucket is to consolidate what's already true and documented into one
coherent picture, and propose an ordered remediation path — not to discover
new problems from scratch.

**Constraints:** no application code is touched by this bucket. Every role
works from the repo's existing artifacts (ADRs, gates, known-issues,
verification baseline) and cites them — this is a documentation/analysis
exercise, not a build. Each step in the pipeline stops for review before the
next one starts (standing rule, see root `CLAUDE.md`).

**Deliverable:** `docs/buckets/archie-ea-as-is/current-state.md` (this step),
followed by per-lane as-is passes, a reconciled to-be plan, and a QA gap
list.

**Acceptance criteria:** every claim in the as-is doc is traceable to a real
file, gate, or ADR in this repo — no invented metrics or unverified claims,
per this repo's own "never invent data" standard.

**Handoff target:** `solution-architect`, `data-architect`,
`security-architect`, `integration-architect` (parallel as-is passes, next
step, on your go-ahead).

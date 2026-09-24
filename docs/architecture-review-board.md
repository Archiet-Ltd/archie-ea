# How to Run an Architecture Review Board (ARB)

An **Architecture Review Board (ARB)** is the governance body that reviews proposed
solutions and changes against your enterprise architecture — its principles, standards,
target state, and risk appetite — and decides whether they may proceed. Done well, an ARB
keeps delivery fast *and* coherent. Done badly, it's a bottleneck everyone routes around.

This guide covers what an ARB is for, who sits on it, how to run the meeting, what a good
decision record looks like, and a readiness checklist you can use before any submission.

## What an ARB is for

- **Conformance** — does the proposal follow architecture principles, standards, and
  reference architectures?
- **Coherence** — does it fit the target architecture, reuse what exists, and avoid
  creating duplicate capabilities?
- **Risk** — are the security, data, cost, and operability risks understood and accepted
  by someone with the authority to accept them?
- **Traceability** — is there a durable record of *what* was decided and *why*?

An ARB is **not** a design workshop, a status meeting, or a rubber stamp. If proposals
arrive unfinished, the ARB becomes design-by-committee; if it approves everything, it adds
latency without adding control.

## Who is on the ARB

| Role | Responsibility |
|---|---|
| Chair (Chief/Lead Architect) | Runs the meeting, owns the decision, breaks ties |
| Enterprise Architect(s) | Conformance to principles, standards, target state |
| Security / Risk | Threat model, data classification, compliance |
| Domain owners | Impact on their applications, data, or capabilities |
| Submitting Solution Architect | Presents and defends the proposal |

Keep the standing board small (5–8). Pull in domain experts per item rather than inviting
everyone to everything.

## Cadence and flow

1. **Submit** — the solution architect files a submission package (below) ahead of the
   meeting. No package, no slot.
2. **Triage** — the chair classifies: *fast-track* (low-risk, conforms — approve async),
   *standard* (review at the meeting), or *escalate* (board-level / steering).
3. **Review** — time-boxed (10–20 min per item). The submitter presents; the board probes
   conformance, coherence, and risk.
4. **Decide** — one of: **Approved**, **Approved with conditions**, **Rejected**, or
   **Deferred** (needs more information).
5. **Record** — capture the decision, rationale, conditions, and review id.
6. **Track conditions** — "approved with conditions" is only real if the conditions are
   tracked to closure.

A weekly cadence with a fast-track lane handles most organizations. The fast-track lane is
what keeps the ARB from becoming a bottleneck.

## The submission package

A reviewable proposal includes:

- **Problem & drivers** — the business motivation (drivers, goals, constraints).
- **Requirements** — functional and non-functional (especially the quality attributes).
- **Options considered** — at least build/buy/extend, with a recommendation and rationale.
- **Target-state fit** — which capabilities and ArchiMate elements it touches; what it
  reuses vs. introduces.
- **Risks & dependencies** — security, data, cost, operability, and cross-team
  dependencies.
- **Architecture diagram(s)** — typically an ArchiMate application-cooperation or
  layered view.

## What a good ARB decision record contains

- Decision: Approved / Approved-with-conditions / Rejected / Deferred
- Date, review id, and who was present
- **Rationale** — the *why*, in enough detail that someone in 18 months understands it
- Conditions (if any), each with an owner and a due date
- Principles or standards invoked

The rationale is the most valuable and most-often-skipped field. An approval with no
recorded reasoning is indistinguishable from a rubber stamp.

## ARB readiness checklist

Before you submit, confirm:

- [ ] Drivers, goals, and constraints are explicit
- [ ] Functional **and** non-functional requirements are captured
- [ ] At least one real alternative was considered, with a recommendation
- [ ] The proposal is mapped to target-state capabilities / ArchiMate elements
- [ ] Security and data classification are addressed
- [ ] Cost and operability are addressed
- [ ] Dependencies on other teams are named
- [ ] There is a diagram a reviewer can actually read

## Common anti-patterns

- **Approve-everything ARB** — adds latency, not control. Track your approval rate; if
  it's ~100%, the board isn't reviewing.
- **Design-in-the-room** — proposals arriving unfinished turn the ARB into a workshop.
  Enforce the submission package.
- **No conditions tracking** — "approved with conditions" with no follow-up is just
  "approved."
- **Invisible decisions** — decisions in someone's inbox instead of a durable, searchable
  record.

## How Entelim helps (optional)

[Entelim](https://github.com/Anioko/archie-ea) builds the ARB into the solution-design
journey: it scores each solution's **maturity**, runs a **readiness gate** that blocks
submission until drivers, requirements, and architecture are in place, and on submit
creates an **ARB review item with an audit trail** — so conformance and the decision
record happen by construction rather than by discipline.

## FAQ

**What is an Architecture Review Board?**
A governance body that reviews proposed solutions against enterprise architecture
principles, standards, target state, and risk, and decides whether they may proceed.

**How often should an ARB meet?**
Weekly works for most organizations, paired with a fast-track lane for low-risk, conformant
changes so the board doesn't become a bottleneck.

**What's the difference between an ARB and a design review?**
A design review improves a design; an ARB *governs* it — checking conformance and risk and
recording an accountable decision.

---
See also: [TOGAF ADM with ArchiMate](togaf-adm-with-archimate.md) ·
[ArchiMate 3.2 cheat sheet](archimate-3-2-cheat-sheet.md)

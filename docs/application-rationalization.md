# Application Portfolio Rationalization (TIME & 6R)

**Application rationalization** is the practice of assessing your application portfolio and
deciding, app by app, what to *keep, consolidate, modernize, or retire* — to cut cost,
reduce risk, and remove duplication. It's one of the highest-return activities in
enterprise architecture, and it's where a good application portfolio pays for itself.

This guide covers what to assess, the two standard decision frameworks (**TIME** and the
**6 Rs**), the data you need, and how to run it without it stalling.

## Why rationalize

A typical large enterprise runs hundreds to thousands of applications. Over time you
accumulate:

- **Redundancy** — multiple apps doing the same job (often from M&A or shadow IT).
- **Cost drag** — license, hosting, and support spend on low-value systems.
- **Risk** — unsupported versions, end-of-life technology, single points of failure.
- **Complexity** — every extra app is more integrations, more attack surface, more to
  change.

Rationalization turns that landscape into a deliberate portfolio.

## What to assess (the two axes)

Most rationalization frameworks score each application on two dimensions:

- **Business value / fit** — how well it supports the business capabilities that matter
  (strategic importance, usage, user satisfaction, capability coverage).
- **Technical health / quality** — how healthy the technology is (supportability, technical
  debt, security posture, scalability, integration burden).

You then place each app on a 2×2 and let the quadrant suggest the disposition.

## Framework 1 — TIME (Gartner)

| Quadrant | Business value | Technical quality | Disposition |
|---|---|---|---|
| **Tolerate** | Low | High | Leave it alone; don't invest |
| **Invest** | High | High | Enhance and extend |
| **Migrate** | High | Low | Re-platform / modernize / replace |
| **Eliminate** | Low | Low | Retire and decommission |

TIME is a portfolio-disposition lens — it tells you the *intent* for each app.

## Framework 2 — The 6 Rs (migration/modernization)

When you've decided an app must change, the **6 Rs** describe *how*:

1. **Retain** — keep as-is (for now).
2. **Retire** — decommission; the capability is gone or duplicated elsewhere.
3. **Rehost** — "lift and shift" to new infrastructure, unchanged.
4. **Replatform** — minor optimization (e.g., managed database) without rearchitecting.
5. **Refactor / Re-architect** — significant code/architecture change for cloud-native or
   scale.
6. **Repurchase** — replace with a SaaS/COTS product.

TIME tells you the *quadrant*; the 6 Rs tell you the *move*. "Migrate" in TIME usually
resolves to Replatform, Refactor, or Repurchase.

## The data you actually need

Rationalization fails most often for one reason: **missing data**. Before scoring, gather
(at minimum):

- **Cost** — annual license, hosting, support, and FTE cost per app.
- **Ownership** — business owner and technical owner.
- **Lifecycle** — version, support status, end-of-life dates.
- **Capability mapping** — which business capabilities each app supports (this is what
  reveals redundancy).
- **Dependencies** — what integrates with it (this is what reveals decommission risk).
- **Usage** — active users / transactions (low usage is a strong retire signal).

If you can't get cost and ownership, start the *organizational* work to capture them — no
framework can rationalize on data that doesn't exist.

## Running it without stalling

1. **Scope a slice** — one domain or business unit, not the whole estate at once.
2. **Score on the two axes** with the data you have; flag gaps rather than guessing.
3. **Find the duplicates** via the capability mapping — overlapping capability coverage is
   the fastest cost win.
4. **Propose dispositions** (TIME) and moves (6R) per app.
5. **Sequence by value and risk** — quick, low-dependency retirements first.
6. **Govern the changes** through your [ARB](architecture-review-board.md).

## Common pitfalls

- **Scoring on opinion, not data** — anchor on cost, usage, and lifecycle facts.
- **Ignoring dependencies** — retiring an app that quietly feeds three others causes
  outages.
- **Big-bang scope** — rationalize a domain at a time and show wins.
- **No capability map** — without it, you can't see redundancy, which is the biggest prize.

## How Entelim helps (optional)

[Entelim](https://github.com/Anioko/archie-ea) maintains the application portfolio,
capability mappings, and dependencies in one model, so disposition scoring and
duplicate-detection run against live data rather than a stale spreadsheet — and the
resulting changes flow through the same ARB governance workflow.

## FAQ

**What is application rationalization?**
The practice of assessing an application portfolio and deciding which apps to keep,
consolidate, modernize, or retire to reduce cost, risk, and duplication.

**What is the TIME framework?**
A Gartner model that places each application into Tolerate, Invest, Migrate, or Eliminate
based on its business value and technical quality.

**What are the 6 Rs of application modernization?**
Retain, Retire, Rehost, Replatform, Refactor, and Repurchase — the six strategies for
*how* to change an application once you've decided it must change.

---
See also: [How to run an ARB](architecture-review-board.md) ·
[ArchiMate 3.2 cheat sheet](archimate-3-2-cheat-sheet.md)

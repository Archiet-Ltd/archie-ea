---
wave: 5
page_family: module
module_label: "Investment Analysis"
endpoint: architecture.investment_priorities
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-05, segment: S3}
capture_status: live
cta: plans
icon: trending-up
headline: Rank where to invest across your capabilities
summary: Capabilities scored on strategic importance, coverage, maturity gap and risk, with real initiative budgets used wherever one is linked.
who_for:
- A CIO or head of architecture deciding which capability deserves investment first.
- A portfolio manager who needs a ranked view before a funding round.
questions:
- q: Which capabilities should we invest in first?
  who: a CIO
- q: How well do our applications cover this capability?
  who: an enterprise architect
- q: What budget is left on the initiative behind it?
  who: a portfolio manager
- q: How risky is this capability?
  who: a risk owner
steps:
- name: Map applications to capabilities
  text: The matrix opens once at least one application is mapped.
- name: Read the priority bands
  text: Critical, high, medium or low, with a recommendation and a timeframe.
- name: Check the figures
  text: Live marks a linked initiative budget; Est. marks an estimate.
- name: Ask for a sequence
  text: An optional AI panel suggests an order; it changes nothing.
---

# Investment Analysis

*A ranked view of where to invest across your business capabilities — scored on strategic
importance, coverage, maturity gap and risk, with real initiative budgets used wherever one is linked.*

## What this module does

A single business case answers whether one initiative is worth doing. Investment Analysis answers a
different question: across all your business capabilities, which deserve investment first. Each
capability is scored on strategic importance, how well applications cover it, its maturity gap and
how risky it is; where maturity has not been assessed, that part of the score takes a neutral
midpoint rather than an invented gap. It is then placed in a critical, high, medium or low priority band, with a recommendation and a timeframe.

Where a capability is linked to a strategic initiative that has a budget allocated, the figure shown
is that initiative's remaining budget (its allocation is shown alongside) and is marked Live. Where
there is no link, or the linked initiative has no allocation, the figure is a scoring-based estimate
and is marked Est. The matrix opens once at least one application has been mapped to a capability. An
optional AI panel suggests a sequence from the matrix's own figures; it is advice only and changes
nothing. It does not roll up business cases.

## Where you'll meet it

- [Build the business case for the CIO from the model's own figures](/use-cases/business-case-for-the-cio)

## Related modules

- [Business Case](/modules/business-case)
- [Portfolio](/modules/portfolio)

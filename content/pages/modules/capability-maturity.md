---
wave: 3
page_family: module
module_label: "Capability Maturity"
endpoint: maturity_management.maturity_heatmap
grouped_sub_pages: [maturity_management.frameworks_overview]
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: in_review
answers_use_cases:
  - {id: UC-S3-06, segment: S3}
capture_status: live
cta: plans
icon: gauge
headline: See where each capability stands against its target
summary: A heat grid of current against target maturity for every capability, with unassessed capabilities shown as missing, not guessed.
who_for:
- A business architect who has to show where capabilities fall short of their targets.
- A CTO or portfolio manager choosing where to invest next.
questions:
- q: Which capabilities are furthest below their target?
  who: a business architect
- q: How many capabilities have actually been assessed?
  who: a portfolio manager
- q: Who owns this capability, and what is acting on it?
  who: a CTO
- q: How does maturity compare across our frameworks?
  who: an enterprise architect
steps:
- name: Record assessments
  text: Levels are entered by hand, one capability at a time or in a batch, or imported from a CSV.
- name: Read the heatmap
  text: Current level, target level and the gap, grouped by category.
- name: Spot what is missing
  text: A capability with no assessment date shows a dash, never Level 1.
- name: Open a capability
  text: See its maturity, owner and the initiatives acting on it.
---

# Capability Maturity

*A heat grid of current against target maturity for every capability, grouped by category, that
shows unassessed capabilities as missing — never a plausible-looking guess in an empty cell.*

## What this module does

The heatmap lists your capabilities by category on a five-point scale from Initial to Optimising,
showing current level, target level and the gap between them. A capability counts as assessed only
once an assessment date is recorded; until then every level shows as a dash rather than as Level 1,
and the page states how many capabilities are assessed out of the total. Averages cover assessed
capabilities only. Each capability opens a line-of-sight view of its maturity, owner and the
initiatives acting on it.

A separate frameworks overview compares maturity across the frameworks your capabilities are
classified under. Levels are entered by hand, one capability at a time or in a batch, or imported
from a CSV; they are not calculated for you.

Available on a paid plan. [See plans](/pricing).

## Related

- [Which value streams are at risk?](/use-cases/value-streams-at-risk)

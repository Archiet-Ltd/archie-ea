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

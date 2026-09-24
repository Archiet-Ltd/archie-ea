---
page_family: module
module_label: "Portfolio"
endpoint: portfolio.index
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-04, segment: S3}
  - {id: UC-S3-09, segment: S3}
capture_status: awaiting_capture
---

# Portfolio

*Capabilities, value streams, programmes and work packages, joined — so a maturity gap, a stalled
initiative, and a late work package all show up as one connected picture instead of three separate
spreadsheets.*

## What this module does

Ask which value streams are at risk, and the answer reads the capabilities underneath them to find
maturity gaps, with initiatives and success metrics attached. Ask whether a programme is on time and
on budget, and the same portfolio answers with dates, status and cost per work package — showing
missing cost plainly rather than defaulting to zero.

## Where you'll meet it

- [Which value streams are at risk?](/enterprise-architecture/value-streams-at-risk)
- [Is the programme on time and on budget?](/enterprise-architecture/programme-tracking)

## Related modules

- [Business Case](/modules/business-case)
- [Investment Analysis](/modules/investment-analysis)
- [Roadmaps](/modules/roadmaps)

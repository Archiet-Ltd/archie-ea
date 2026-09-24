---
page_family: module
module_label: "Investment Analysis"
endpoint: architecture.investment_priorities
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-05, segment: S3}
capture_status: awaiting_capture
---

# Investment Analysis

*Entelim's portfolio-wide view of cost and priority — every business case rolled up into one place,
so a CIO can rank initiatives instead of reviewing them one at a time.*

## What this module does

A single business case answers whether one initiative is worth doing. Investment Analysis answers
the next question: across every business case in the portfolio, which ones are the priority, given
the cost and impact figures each one actually carries.

## Where you'll meet it

- [Build the business case for the CIO from the model's own figures](/enterprise-architecture/business-case)

## Related modules

- [Business Case](/modules/business-case)
- [Portfolio](/modules/portfolio)

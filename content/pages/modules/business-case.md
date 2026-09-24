---
page_family: module
module_label: "Business Case"
endpoint: business_case.index
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-05, segment: S3}
capture_status: awaiting_capture
---

# Business Case

*Capex, opex, three-year TCO, ROI and payback — computed from figures already in your model, never
invented to fill a blank field.*

## What this module does

A business case linked to a capability, initiative or solution: problem, options, recommendation,
benefits and risks alongside the numbers a CIO actually asks for. A field with nothing behind it
stays empty rather than showing a plausible-looking guess.

## Where you'll meet it

- [Build the business case for the CIO from the model's own figures](/enterprise-architecture/business-case)

## Related modules

- [Portfolio](/modules/portfolio)
- [Investment Analysis](/modules/investment-analysis)

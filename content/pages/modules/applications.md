---
page_family: module
module_label: "Applications"
endpoint: unified_applications.application_list
source: app/modules/modules_directory/routes.py + app/utils/role_access.py (_link calls, read 2026-09-23)
state: on_main
answers_use_cases:
  - {id: UC-S1-05, segment: S1}
  - {id: UC-S2-03, segment: S2}
  - {id: UC-S2-07, segment: S2}
  - {id: UC-S3-01, segment: S3}
  - {id: UC-S4-05, segment: S4}
capture_status: awaiting_capture
---

# Applications

*The single list of every application Entelim knows about, where it came from, who owns it, and what
it costs.*

## What this module does

Every application in your model — typed in, imported, or read through a connector — lives in one
list, not scattered across a spreadsheet and three people's heads. Each row shows where the record
came from, and links straight into the questions that read it: what you're paying for twice, vendor
and procurement detail, and what breaks if this application fails.

## Where you'll meet it

- [Show an investor what we run, in an afternoon](/startups/show-what-we-run)
- [What are we paying for twice?](/scale-up/duplicate-spend)
- [Give the acquirer a current architecture picture we didn't draw by hand](/scale-up/twin-map)
- [Import our existing Archi or Open Exchange model](/enterprise-architecture/import)
- [Which contracts renew soon, and what depends on them?](/services-ops/contract-renewals)

## Related modules

- [Vendors](/modules/vendors)
- [Rationalization](/modules/rationalization)
- [Procurement](/modules/procurement)

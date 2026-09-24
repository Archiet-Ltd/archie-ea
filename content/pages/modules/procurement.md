---
page_family: module
module_label: "Procurement"
endpoint: procurement.renewals_dashboard
grouped_sub_pages: [procurement.contracts_list, procurement.spend_analytics, procurement.licenses_list, procurement.compliance_dashboard]
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S4-05, segment: S4}
capture_status: awaiting_capture
---

# Procurement

*Contracts, renewals, licence entitlements, spend by category, and compliance status — one module,
because they're one workflow, not five separate tools.*

## What this module does

Renewals, contracts, spend analytics and licences all read from the same vendor and application
records. Ask which contracts renew next quarter, which licences are entitled versus actually used,
or which vendor a piece of spend belongs to, and the answer traces back to your real application
list rather than living as a separate, disconnected spreadsheet.

## Where you'll meet it

- [Which contracts renew soon, and what depends on them?](/services-ops/contract-renewals)

## Related modules

- [Applications](/modules/applications)
- [Vendors](/modules/vendors)

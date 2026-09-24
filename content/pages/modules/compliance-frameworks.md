---
page_family: module
module_label: "Compliance Frameworks"
endpoint: application_mgmt.compliance_frameworks_dashboard
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-08, segment: S3}
capture_status: awaiting_capture
---

# Compliance Frameworks

*The filter that turns the Risk Register into a control-gap view — which framework requirements
your model's risks actually touch.*

## What this module does

Pairs with the Risk Register to show which control or compliance requirement a risk maps to, and
whether the gap is owned. It shows what your own model's data says against a framework's
requirements — never a certification or a guarantee that you pass an audit.

## Where you'll meet it

- [Which risks and control gaps touch this goal?](/enterprise-architecture/risk-and-controls)

## Related modules

- [Risk Register](/modules/risk-register)

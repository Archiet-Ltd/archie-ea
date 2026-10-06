---
page_family: module
module_label: "Compliance Frameworks"
endpoint: application_mgmt.compliance_frameworks_dashboard
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-08, segment: S3}
capture_status: live
cta: plans
---

# Compliance Frameworks

*Records where each application in your model stands against every framework's controls —
what's implemented, what's still a gap.*

## What this module does

Records, per application, where it stands against each framework's controls. It shows what your
own model's data says against a framework's requirements — never a certification or a guarantee
that you pass an audit.

## Where you'll meet it

- [Which risks touch this part of the architecture?](/enterprise-architecture/risk-and-controls)

## Related modules

- [Risk Register](/modules/risk-register)

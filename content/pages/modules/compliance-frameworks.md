---
wave: 5
page_family: module
module_label: "Compliance Frameworks"
endpoint: application_mgmt.compliance_frameworks_dashboard
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S3-08, segment: S3}
capture_status: live
cta: plans
icon: shield-check
headline: Record where each application stands against a framework's controls
summary: For each application, what is implemented and what is still a gap, as your own model records it. It is never a certification and never a promise you will pass an audit.
who_for:
- A compliance lead who has to show which controls apply to which applications.
- A security architect looking for control gaps before an audit.
questions:
- q: Which controls does this application meet?
  who: a compliance lead
- q: Where are our control gaps?
  who: a security architect
- q: Which risks touch this part of the architecture?
  who: a risk owner
- q: What does our own record say against this framework?
  who: an auditor's contact
steps:
- name: Pick a framework
  text: Choose the framework whose controls you track.
- name: Record each application's position
  text: Implemented, or still a gap.
- name: Read the dashboard
  text: See framework coverage across the estate.
- name: Follow a gap
  text: Open the risks that touch the same part of the architecture.
---

# Compliance Frameworks

*Records where each application in your model stands against every framework's controls —
what's implemented, what's still a gap.*

## What this module does

Records, per application, where it stands against each framework's controls. It shows what your
own model's data says against a framework's requirements — never a certification or a guarantee
that you pass an audit.

## Where you'll meet it

- [Which risks touch this part of the architecture?](/use-cases/risk-and-control-gaps)

## Related modules

- [Risk Register](/modules/risk-register)

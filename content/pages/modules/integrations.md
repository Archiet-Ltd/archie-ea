---
wave: 4
page_family: module
module_label: "Integrations"
endpoint: main.integrations
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S2-04, segment: S2}
capture_status: live
cta: plans
icon: plug
headline: See what is feeding your model
summary: Every connector in one place, with a health page showing what is actually feeding your model from GitHub and Jira.
who_for:
- A platform administrator who connects the tools.
- An architect who wants to know whether the model is current.
questions:
- q: Is the model still being fed from GitHub?
  who: an architect
- q: When did the Jira connector last synchronise?
  who: a platform administrator
- q: Which tools are connected?
  who: a CTO
- q: Why is this fact out of date?
  who: an enterprise architect
steps:
- name: Connect a tool
  text: Add GitHub or Jira as a connector.
- name: Let it read
  text: The model stays current from what your team already maintains.
- name: Check the health page
  text: See what is feeding the model and when it last ran.
- name: Approve changes
  text: Suggested changes wait for a person to approve.
---

# Integrations

*Every connector in one place, with a health page showing exactly what's actually feeding your
model.*

## What this module does

Jira, ServiceNow, Datadog and ABACUS, connected here — so the model stays current because it reads
what your team already maintains, not because someone re-enters it by hand.

## Related modules

- [Applications](/modules/applications)

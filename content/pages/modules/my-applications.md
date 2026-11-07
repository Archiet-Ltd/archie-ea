---
wave: 1
page_family: module
module_label: "My Applications"
endpoint: my_applications.dashboard
grouped_sub_pages: [my_applications.app_list]
source: app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-09-23
state: on_main
answers_use_cases:
  - {id: UC-S2-10, segment: S2}
capture_status: live
cta: plans
icon: user-check
headline: See what you own and what depends on it
summary: A dashboard and list of the applications you are the recorded owner of, with the health of each.
who_for:
- An application owner who answers for a handful of systems.
- A manager who wants to know what is on the hook because of what they own.
questions:
- q: Which applications do I own?
  who: an application owner
- q: Which of mine are unhealthy?
  who: an application manager
- q: What depends on what I own?
  who: a team lead
- q: Which of my records are incomplete?
  who: an application owner before a review
steps:
- name: Sign in
  text: The page is scoped to you.
- name: Read your list
  text: The applications you are the recorded owner of.
- name: Check the health
  text: Health status is shown for each.
- name: Open an application
  text: Go to its record and keep it true.
---

# My Applications

*Not the whole portfolio — just what you personally own, and what's on the hook because of it.*

## What this module does

A dashboard and list scoped to you: the applications you're the recorded owner of, with health
status for each. The full Applications module is everyone's estate; this page is yours.

## Where you'll meet it

- [Show me what I own and what depends on it](/use-cases/what-i-own)

## Related modules

- [Applications](/modules/applications)

---
wave: 5
page_family: role
role_group: security-risk-compliance
state: on_main
capture_status: live
cta: plans
description: "Security architect tools from Entelim: risks ranked by what they reach, a sealed audit log and data subject requests on one model. Start free, see pricing."
source: app/utils/role_access.py (ROLE_SECURITY_ARCHITECT, SIDEBAR_ZONES, _MY_WORK_LINKS), app/modules/risk,
  app/modules/intelligence/routes/ui.py, app/modules/admin audit log and governance gates routes,
  app/modules/compliance/ (gdpr_bp), app/modules/tech_radar, app/services/billing_plans.py,
  read 18 October 2026
---

# Security architect tools that show what a risk reaches, not just where it was logged

Security architect tools earn their place by answering three questions a design review or an audit puts to you: which risks matter most right now, what each one actually touches, and whether the record of what happened can be trusted. Entelim answers them from the same architecture model your enterprise architects work in: a risk register ranked by the number of elements each risk reaches, a risk answer in the AI chat that lists every risk against an application with its score, owner and mitigation, and an audit log that proves it has not been altered. A security architect can start on the free Community plan.

**[Start free](/register)** or **[see pricing](/pricing)**.

## What a security architect does

A security architect decides how a design stays safe: which risks are acceptable, which controls must exist before a system goes live, and what evidence shows they do. The job turns from designing to defending when an auditor, a board member or a delivery team asks a plain question: what does this risk put in danger, who owns the fix, and how do you know the record is complete? Security architect tools earn their place by answering from the model the systems are described in, not from a spreadsheet of risks that nobody has connected to the estate.

## The questions you get asked

- [Which risks sit on our revenue-critical path, and who owns the mitigation?](/use-cases/risk-blast-radius)
- [Which risks and control gaps touch this goal?](/use-cases/risk-and-control-gaps)
- [What breaks if this service fails?](/use-cases/what-breaks-and-who-gets-called)

## The screens you will use

- **Risk Register**, which lists every recorded risk with its status, likelihood and impact, and ranks the top risks by the number of elements each one reaches, then by recorded score. Each row says how many elements it reaches and names some of them, so a risk logged against one database shows the applications and processes behind it. Totals for all risks, open risks, critical risks and mitigated risks sit across the top. See [Risk Register](/modules/risk-register).
- **The AI chat**, where you ask what could hurt a system and what it touches. It takes one application, process or other element and lists each risk against it with likelihood times impact, a severity word, the recorded owner, the mitigation recorded for it and the number of connections it reaches. See [Digital twin of your organisation](/modules/digital-twin).
- **Audit Log**, a searchable record of your organisation's activity that you can filter by date, user, action and entity type and export as CSV. Each entry is sealed to the one before it, so an entry altered, removed or inserted afterwards is detected, and a button runs the check and records who ran it and when. One filter shows only the AI tool calls the platform refused.
- **Governance Gates**, the hard gates that stop a solution moving forward until its completeness meets a threshold. The default gate on Architecture Review Board submission asks for 60 per cent completeness. Security architects can read the gates; changing one is an administrator's action.
- **Data Subject Requests**, where you record an access, erasure, rectification, restriction, portability or objection request, and Entelim lists the systems, and the suppliers recorded against them, that hold the personal data it concerns. Each search is given an owner, and every step is kept in the audit trail.
- **Tech Radar**, where each technology in the model is classed as adopt, trial, assess or hold, so a design that relies on something marked hold is visible at review.
- **Traceability Matrix**, which follows the elements from strategy and business down to technology, top-down or bottom-up, from a pivot element you choose, with a CSV export and a view of the gaps.

Enterprise architects set the direction these designs follow; read how they work on [enterprise architect tools](/roles/enterprise-architects), and how designs reach the board on [solution architect tools](/roles/solution-architects).

## A week with Entelim

1. Open the Risk Register and read the top-ranked risks: which ones reach the most of the estate this week.
2. Name the application behind the biggest one in the AI chat and ask what could hurt it, to see every risk against it with its owner and its mitigation.
3. Before a design goes to the review board, check it against the governance gate and against the Tech Radar for anything marked hold.
4. When a data subject request arrives, record it and send the searches to the owners of the systems it lists.
5. At audit time, filter the audit log to the period, export it, and run the integrity check so the evidence says it is intact.

## What you hand over

A ranked list of risks with the elements each one reaches, the owner and the mitigation recorded against it. An audit log export with an integrity check result and the name of whoever ran it. A data subject request with its scope, its searches and their owners, and the audit entries for every step. And a design that was checked against a gate and a technology decision before the board saw it.

## Limits

Entelim records and ranks the risks and the architecture around them; it does not scan your systems for vulnerabilities, test your controls or replace a security monitoring tool. A risk's score is the likelihood and impact recorded for it, not a figure Entelim estimates. A data subject request lists the systems that are recorded as using a personal-data category in the model: if no personal-data category is linked to an application yet, the request says no system is recorded as holding the data, and the model needs that link before the list means anything. The audit integrity check covers entries the platform has sealed; it says nothing about systems outside Entelim.

## Frequently asked questions

### How does Entelim rank risks?

The Risk Register ranks the top risks by the number of architecture elements each one reaches, then by the recorded likelihood and impact score. Each row states how many elements it reaches and names some of them, so you see why a risk is near the top. Risks linked to no elements follow, ordered by recorded score.

### Can I prove the audit log has not been altered?

Yes. Each audit entry is sealed to the one before it, so an entry changed, removed or inserted later is detected. The Audit Log page has a Verify integrity button that checks the whole chain and records the result with the name of the person who ran it and the time.

### Can I see which systems hold a person's personal data?

Yes, as far as the model records it. A data subject request lists the systems that use the personal-data categories you link it to, and the suppliers recorded against those systems. Where no system is recorded, the request says so plainly rather than guessing.

### Can it enforce a design standard before review?

Yes, through governance gates. A gate holds a solution back from a step, such as submission to the Architecture Review Board, until it reaches a completeness threshold; the default for board submission is 60 per cent. Security architects can read the gates and administrators change them.

### Is it free?

Yes, to start. The Community plan is free for one organisation of up to three people; Startup, Team and Enterprise are the paid plans above it. See [Entelim pricing](/pricing), or self-host the open-source edition under AGPL-3.0.

## Start free

[Start free](/register) on the Community plan for one organisation of up to three people. A growing team moves to one of the paid plans. [See full pricing](/pricing).

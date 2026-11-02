---
wave: 5
page_family: function-per-segment
use_case_id: UC-S3-08
segment_id: S3
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/risk-and-control-gaps
icon: shield-off
---

# Which risks and control gaps touch this goal, and who owns the mitigation?

**For enterprise architecture teams answering to a regulator or an internal audit.**

Entelim keeps risks in a Risk Register, each with a likelihood, an impact, an owner and a mitigation plan. Ask the AI chat what could hurt a system and what it touches, and it shows each recorded risk with its score, its owner and how many connections it reaches. Ask which controls apply to a system and which have no evidence, and it lists the controls mapped to an application and flags the ones with no evidence.

## The question

An auditor points at a goal, a system or a regulation and asks the same three things. What could go wrong here? Which controls are supposed to stop it, and is there proof they work? Who is on the hook for closing the gap? In most estates the answers sit in a risk spreadsheet, a compliance tracker and somebody's memory, and they disagree.

## What Entelim answers

**Risk Register.** Four tiles sit at the top: Total, Open, Critical and Mitigated. Critical counts open critical risks only, so a closed risk does not inflate it. The table shows Title, Owner, Likelihood, Impact, Score, Level and Status. You can search by title or owner and filter by Open, Mitigated, Accepted or Closed. Each row offers View, Edit, Map to…, Mark mitigated, Reopen, Close and Delete. **Add Risk** takes a title, a description, likelihood and impact from 1 to 5, an owner and a mitigation plan. Below the table, an Issues & Dependencies list records the other two RAID categories, each with an owner and a target date. Map to… links a risk to an application, a solution or a programme.

**The chat, risk question.** Name the system or risk. You get one card per risk with the title, the risk level, "Likelihood x Impact = score" with the recorded values, the owner (or "No owner recorded"), the mitigation plan if one exists, and a count of the connections the risk reaches. If nothing is recorded, the answer says "No risks recorded against this element."

**The chat, compliance question.** Name an application. Each mapped control appears with its code, name, framework, status and one of three evidence lines: "No evidence recorded and not verified", "Evidence recorded", and a verified date where one exists. Open policy violations follow, with severity, then the time of the last policy scan, or "No policy scan is recorded for this element."

**Regulatory Compliance.** A read-only screen listing each active framework with Code, Name, Category, Jurisdiction, Enforcement, Penalty risk and a control count. Open a framework to see its controls with Control, Title, Category and Priority. It also shows how many application-to-control mappings sit at each implementation status.

## Who asks it

An enterprise architect or security architect preparing evidence for an audit, or a risk owner asked at a review board which of their risks has no plan.

## How to get your answer

1. Connect GitHub or import a file so your applications are in the model, then open the Risk Register and use **Add Risk** for each risk, giving an owner and a mitigation plan.
2. Use **Map to…** to tie each risk to the application, solution or programme it threatens.
3. Map applications to controls so the compliance question has something to read.
4. Open the **AI chat** and ask what could hurt the system, and what it touches.
5. For control gaps, ask which controls apply to the application and which have no evidence.
6. Read the owner line and the evidence line on each card. "No owner recorded" and "No evidence recorded and not verified" are your gaps.

## Doing this without Entelim

You join a risk log to a controls matrix by hand, then chase owners by email. It holds for the audit week. After that nothing ties a risk to the systems it touches, so the join goes stale.

## Limits

- The chat shows risks recorded on the element you name. It does not walk from a goal to every risk connected to it, and risks linked through Map to… are not read by the chat. Use the Risk Register for those.
- The risk owner is a name typed into the register. It is not checked against your users.
- The chat shows a count of connections reached, not a list.
- The compliance question works for applications. For anything else it says no compliance controls are recorded.
- Regulatory Compliance is a separate screen. It does not filter by goal.
- Frameworks and controls are shared reference data; you map your applications to them.

## Frequently asked questions

### Does it tell me which risks touch a particular goal?

Not directly. The chat answers from a recorded risk outwards, showing how many connections it reaches. The Risk Register shows what each risk is mapped to.

### What happens if a risk has no owner?

The card reads "No owner recorded". Entelim does not fill the gap with a guess.

### Can I see which controls have no evidence?

Yes, per application, on the compliance question. Each control without evidence reads "No evidence recorded and not verified".

### How is this different from a spreadsheet?

Scores are worked out from the likelihood and impact you record, and the same risk record feeds the register, the chat and the model.

## Related questions and modules

- [Risk Register](/modules/risk-register)
- [Compliance Frameworks](/modules/compliance-frameworks)
- [Which risks sit on our revenue-critical path, and who owns the mitigation?](/use-cases/risk-blast-radius)
- [What breaks if this service or platform fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)

---
page_family: function-per-segment
use_case_id: UC-S2-08
segment_id: S2
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/risk-blast-radius
icon: flame
---

# Which risks sit on our revenue-critical path, and who owns the mitigation?

**For scale-up CTOs and VPs of Engineering.**

On the Ask page, open "What could hurt ___ and what does it touch?" and pick a risk from your model. Entelim
shows the risk with its level, its likelihood and impact score, its recorded owner, its mitigation plan and a
count of the connections its blast radius reaches. The Risk Register lists every risk with the same fields, so you
can see which ones have no owner.

## The question

A risk gets logged in a spreadsheet against one component. The spreadsheet says how bad it is and who should deal
with it. It does not say what else the risk touches, so nobody can tell whether it sits on the path your revenue
depends on. When an investor or auditor asks which risks matter most, you piece the answer together by hand.

## What Entelim answers

On **Ask**, the question "What could hurt ___ and what does it touch?" opens a search box. Type the name of a risk
and choose it from the matches. The results show one card per risk recorded against that element:

- the **risk title** and its **level** (low, medium, high or critical)
- the line "Likelihood 4 x Impact 3 = 12", with your own figures
- the **owner**, or "No owner recorded" when nobody is named
- the **mitigation plan**, when one has been written
- a count such as "6 connections reached", the size of the risk's blast radius, found by following the same
  connections the "What breaks if ___ fails" question follows

If nothing is recorded against the element, the page says "No risks recorded against this element." If the lookup
fails, it says so rather than showing an empty list.

The **Risk Register** (under Governance) holds the risks themselves. It shows four tiles, Total, Open, Critical and
Mitigated, then a table with Title, Owner, Likelihood, Impact, Score, Level and Status. You can search by title, filter by
status (Open, Mitigated, Accepted or Closed), and use the row actions View, Edit, Map to, Mark mitigated, Reopen,
Close and Delete. Add Risk takes a title, description, likelihood and impact from 1 to 5, an owner and a mitigation
plan. Critical counts open risks only, so a closed risk does not inflate the figure.

## Who asks it

A CTO or VP of Engineering preparing a board pack, a security questionnaire or a due-diligence response, who needs to
say which risks are real, how far each one reaches and who is holding it.

## How to get your answer

1. Open the **Risk Register** and choose **Add Risk** for each risk you track. Give it a likelihood, an impact, an
   owner and a mitigation plan.
2. Check the register's table for rows where the Owner column is empty. Those are the risks nobody holds.
3. Make sure the systems the risk affects are connected to it in your model, so there is something for the blast
   radius to follow.
4. Open **Ask**, choose "What could hurt ___ and what does it touch?", and type the risk's name.
5. Read the card: level, score, owner, mitigation plan and the number of connections reached.

## Doing this without Entelim

You keep a risk log in a spreadsheet and a separate set of diagrams, then trace connections by hand for each risk. It
works for a handful of risks. It breaks when the diagrams drift from reality, and nothing flags a risk that has lost
its owner.

## Limits

- The blast radius is a count of connections reached. The card does not list them, and the page does not name
  the revenue streams they serve.
- Entelim has no field that marks a system or a risk as revenue-critical. You get how far a risk reaches, and you
  judge which of those reaches matter to revenue.
- Ask finds risks recorded against the element you pick. A risk that you only linked to an application, solution or
  programme with Map to on the register is shown on the register, but Ask does not read those links.
- The owner is the single owner recorded on the risk. There is no separate field for the person who owns the
  mitigation.
- The score is likelihood times impact from the figures you entered. Entelim does not estimate them.

## Frequently asked questions

### Does Entelim work out how likely a risk is?

No. You enter the likelihood and impact, each from 1 to 5, and Entelim multiplies them and shows the level.

### What if a risk has no owner?

The Ask card says "No owner recorded" and the register shows the Owner cell empty. Edit the risk and name one.

### Can I see which systems a risk reaches?

Ask shows how many connections the risk reaches. To see the systems themselves, open the Twin map or ask "What breaks
if ___ fails" about the element in question.

## Related questions and modules

- [Risk Register](/modules/risk-register)
- [Digital Twin](/modules/digital-twin)
- [What breaks if this service fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)
- [Which risks and control gaps touch this goal, and who owns the mitigation?](/use-cases/risk-and-control-gaps)

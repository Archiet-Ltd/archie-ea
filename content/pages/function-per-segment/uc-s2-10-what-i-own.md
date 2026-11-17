---
wave: 1
page_family: function-per-segment
use_case_id: UC-S2-10
segment_id: S2
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/what-i-own
icon: briefcase
---

# Show me what I own and what depends on it, so I know what I am on the hook for

**For engineering managers and staff engineers, not just the CTO signing the contract.**

Open My Applications. It lists only the applications you are recorded as owner of, with the type of ownership you hold,
the health status recorded on each one and any sunset dates coming up. To see what depends on one of them, ask the AI
chat what breaks if it fails. My Applications does not yet list dependants itself.

## The question

You are accountable for a service. What you do not have is a clean view of exactly what that includes. The company's
application list is long and belongs to everyone, so finding your slice means filtering a spreadsheet or asking around.
You find out what you were holding when something breaks or a colleague leaves.

## What Entelim answers

**My Applications** is a personal workspace with four tabs: Dashboard, My Portfolio, Health Overview and Roadmap
Impact.

The **Dashboard** opens with five tiles: Total Apps, Healthy, At Risk, Critical and Not assessed. Below them a panel
lists your applications by name with your ownership type, and a "Showing 10 of 24" style line appears when you own
more than the panel holds. A Not assessed tile with a count above zero offers a "Record a health status" link.

**My Portfolio** is the full list, 20 to a page, with a search box ("Search applications...") and filters for
ownership type: All, Primary, Backup, Technical and Business. Each card links to "View details".

An application's detail page shows the description, type, business domain, department, business criticality,
vendor and version, hosting type, platform, technology stack and data classification, and a "Your Ownership" panel
with your role, whether you are the primary owner and the date you were assigned. Key dates include go-live, sunset
date and last review. A link takes you on to the full application record.

**Health Overview** groups your applications under Critical, At Risk, Healthy and Not assessed, according to the
status recorded on each application. An application with no recorded status, or one outside those values, shows as
Not assessed. It is never counted as healthy by default.

**Roadmap Impact** lists your applications that have a sunset date in the future, soonest first, and says "No Roadmap
Impacts" when there are none.

Every figure on these pages is read from the same ownership records, so the tiles, the list and the grouped health
view do not disagree.

## Who asks it

An engineering manager or staff engineer who has been named owner of one or more systems, in the application manager
role. The same question comes from a new joiner on day one, and from a manager taking over a team's estate.

## How to get your answer

1. Sign in as an application manager and open **My Applications** in the sidebar.
2. Read the tiles. Total Apps is the number of applications you own, and the four health tiles add up to it.
3. Open **My Portfolio** and use the ownership-type filters to separate what you run as Primary from what you back up.
4. Open **Health Overview** and record a status for anything under Not assessed.
5. Open **Roadmap Impact** to see what is due to be retired.
6. For what depends on an application, open the **AI chat** and ask what breaks if that application fails.

## Doing this without Entelim

You search the service catalogue or a wiki page for your name, then check each entry by hand. The list is out of date
as soon as someone changes teams, and it says nothing about health or retirement dates.

## Limits

- The list of what depends on each owned application is not on these pages. The AI chat answers that question one
  application at a time.
- Only the application manager role, and platform administrators, can open My Applications. Other roles are refused.
- It lists applications you are recorded against as an owner. An application with no owner recorded appears for nobody.
- Health is the status someone recorded. Entelim does not calculate it, and unrecorded health is shown as Not assessed.
- The Roadmap Impact tab reads sunset dates only. An application without one does not appear there.

## Frequently asked questions

### How do I become the owner of an application?

An ownership record is added on the application, with an ownership type of Primary, Backup, Technical, Business or
Steward. Once it names you, the application appears under My Applications.

### Can I see applications I do not own?

Not here. The All Applications link on the dashboard opens the organisation-wide list.

### What if my health tile says Not assessed?

No health status is recorded on those applications. Use "Record a health status" to set one.

### Is this different from the main applications list?

Yes. The main list covers the whole organisation. My Applications shows only your own ownership records.

## Related questions and modules

- [My Applications](/modules/my-applications)
- [Applications](/modules/applications)
- [Which systems have no owner, and which owner is a single point of failure?](/use-cases/systems-with-no-owner)
- [What breaks if this service fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)

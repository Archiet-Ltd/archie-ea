---
wave: 1
page_family: function-per-segment
use_case_id: UC-S1-05
segment_id: S1
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/show-investors-what-we-run
icon: presentation
---

# Show an investor what we run and who owns it, in an afternoon

**For startup founders preparing for a fundraise, a board meeting, or diligence.**

Three screens give you the operating picture. The **Applications** list is the inventory of what you run, with a count of how many applications have a named owner. The **Org Chart** and the **Enterprise RACI** matrix show who is Responsible and Accountable for each capability. The **Twin map** draws what a chosen system connects to. All three read from the same records, so what you show an investor matches what your team sees.

## The question

A diligence request or a board question asks for the operating picture, not the pitch deck: which systems you run, who owns each, and how they fit together. Assembling it from memory and scattered documents takes time you do not have during a raise.

## What Entelim answers

**Applications** (Library section of the sidebar) lists every application in the portfolio. Four cards across the top count the **Active Portfolio**, **Strategic**, **Tactical** and **Sunset/Decom Pipeline**, and each card filters the table when clicked. Decommissioned applications are hidden until you ask for them. The table shows ID, Application name with the start of its description, Type, whether the application is linked to an ArchiMate element, Lifecycle Status, Capability and row actions. Search and filter by type, lifecycle status, domain and capability level, then use **Export** to download the filtered list as a CSV file.

A **Data Quality** panel states how many applications in the portfolio have a named owner ("a business owner on record, or an application manager assigned"), how many have a vendor recorded and how many are decommissioned. Above the table, **Application Cooperation Viewpoint** opens a drawing of up to 25 applications and their relationships.

**Organization** holds the **Org Chart**, drawn from the actors and the composition and aggregation links in your model, and the **Enterprise RACI** matrix. In the matrix, stakeholders run down the side and capabilities across the top. Click a cell to cycle it through Responsible, Accountable, Consulted and Informed. A cell nobody has set stays blank.

On **Ask**, the accountability question, "Who's accountable for ___, and can they take on more?", lists the recorded owners of one system and of what it affects downstream. The **Twin map** button on Ask opens the map: pick a team, service or system and it draws what it connects to in bands (Goals, Strategy, Business, Systems, Technology, Delivery), with a hop-depth slider from 1 to 5 and a table of the same connections underneath.

## Who asks it

A founder or CTO answering an investor's, auditor's or acquirer's request for "your systems and who owns them".

## How to get your answer

1. Add your applications on the **Applications** page with **Add Application**, or use **Import** to bring in a spreadsheet.
2. For each important application, open its edit page and go to the owners section. Under **Search user**, type at least two characters of a name, choose a **Role** (Primary, Backup, Technical or Business) and click **Add owner**.
3. Open **Organization** and check the Org Chart and the Enterprise RACI for the capabilities that matter to the story.
4. Read the **Data Quality** panel on Applications. Fix the gaps it names before anyone else finds them.
5. Open **Ask**, pick the accountability question for your two or three critical systems, and then open the **Twin map** for the same systems.
6. Export the application list as CSV and share the screens.

## Doing this without Entelim

A slide per system, a spreadsheet of owners and a diagram from the last round. Each says something a little different, and diligence teams notice when they disagree.

## Limits

- Entelim shows the applications and owners you have recorded. It does not find systems by scanning your cloud account.
- The application list does not show an owner column. Owners appear in the Data Quality count, on each application and in the accountability answer on Ask.
- The Twin map and the Ask answers draw only what is in the model. A system with no relationships appears with no connections.
- The Org Chart draws the hierarchy that exists in your model. With no composition or aggregation links, it falls back to a flat list grouped by actor type.
- The Twin map cannot be saved or exported. To keep a picture, build and save a diagram in the Composer; Diagrams lists what you have saved.

## Frequently asked questions

### Do I need to model everything first?

No. Start with the systems the business could not run without, and give each an owner.

### Can an investor see this directly?

Entelim has no public link for these screens. A person needs a sign-in to your organisation, so most teams share a screen or the CSV export.

### What if an application has no owner?

It counts against the owner figure in the Data Quality panel. Ask also draws on owners of the systems downstream of it, so cards can appear even when the application has no owner of its own; open the application's Ownership section to check its own assignments. Only when nothing in that chain has an owner does Ask say so.

## Related questions and modules

- [Applications](/modules/applications)
- [Org Chart & RACI](/modules/org-chart)
- [Digital Twin](/modules/digital-twin)
- [What must be true for our revenue stream?](/use-cases/revenue-stream-risk)
- [Which systems have no owner?](/use-cases/systems-with-no-owner)

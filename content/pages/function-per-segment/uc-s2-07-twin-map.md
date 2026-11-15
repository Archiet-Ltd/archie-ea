---
wave: 2
page_family: function-per-segment
use_case_id: UC-S2-07
segment_id: S2
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/architecture-map-for-due-diligence
icon: map
---

# Give the acquirer or the auditor a current architecture picture we did not draw by hand

**For scale-up CTOs facing a funding round, an audit, or acquisition diligence.**

Open the **Twin map** from the Ask page, pick a service, system or team, and Entelim draws what it connects to, banded by layer, from the model as it stands now. Nobody draws it. For a picture you can hand over, build and save a diagram in the Composer and export it, including as ArchiMate Exchange Format that other modelling tools can open.

## The question

Diligence or an audit asks for the system landscape. The diagram that answers it either does not exist, or was drawn six months ago by someone who has left.

## What Entelim answers

The **Twin map** has a search box ("Type a team, service or system name"). Choose an element and the page shows a **Map** of its connections in six horizontal bands: Goals, Strategy, Business, Systems, Technology and Delivery. An element whose layer is not recorded goes in a band called "Layer not recorded". Connections that were recorded and ones Entelim worked out are both drawn, and a **Show what we worked out** switch turns the worked-out ones off. A **Hop depth** slider from 1 to 5 widens or narrows the map.

Click an element and the **Selected element** panel shows its name, its Owner (or "Not recorded"), how many things depend on it, and a **Why?** button. **Full detail** gives the ArchiMate type and layer. Beneath the drawing, a table lists the same connections as text, with columns for Source, Relationship, Target, Kind, Depth and Owner, and a **Centre on this** button to move the map to any element in it. An empty map says "Pick a team, service, or system to see what it connects to."

On Ask, every result line has a **View on twin map** link that opens the map already centred on that element.

For a document you can attach to a data room, the **Composer** is where pictures are built and kept. **Save Viewpoint** stores a diagram under a name you choose. The **Diagrams** library lists every saved diagram in your organisation, with a name search and columns for Name, Solution, Viewpoint, Elements and Updated, and an **Open** link on each. The Composer's Export menu offers PNG, SVG, PDF, PowerPoint, ArchiMate XML (ArchiMate Exchange Format, which other ArchiMate tools can re-import) and a PDF report, plus Archi, Lucidchart and Mermaid files.

Each element also has an **Impact graph** page, which draws what depends on it and what it depends on to 1, 2 or 3 hops, with the decisions recorded against it. The **Why?** panel on Ask and the Twin map links to it.

## Who asks it

A CTO or head of engineering answering a data-room request, a security or ISO audit, or an acquirer's technical review.

## How to get your answer

1. Keep your applications, platforms and relationships in Entelim. Import or add them in **Applications** and the architecture model.
2. Open **Ask a question**, then click **Twin map** at the top right.
3. Search for the most important system and choose it. Raise the **Hop depth** until the map covers what the reader needs to see.
4. Click the elements the reader will ask about. Open **Why?** on any line you will have to defend.
5. To keep a picture, open the **Composer**, open a viewpoint or add the elements you want, click **Save Viewpoint** and give it a name. The Composer will not save an empty canvas.
6. Use the Export menu for the format the other side asks for. Find saved work again under **Diagrams**.

## Doing this without Entelim

Someone opens a diagramming tool and redraws the estate from memory and a spreadsheet. It looks good for a week, and nobody can say how complete it is.

## Limits

- The Twin map draws one element's connections at a time. It is not a single picture of the whole company.
- It cannot be saved or exported. Saving and exporting happen in the Composer.
- The Twin map does not link into the Composer. A saved diagram is opened from the Diagrams library.
- The map follows connections away from the chosen element. It shows no health signal, and says "Not recorded yet" for health.
- Worked-out connections appear only after they have been calculated. Until then the map shows recorded connections and a notice.

## Frequently asked questions

### Is the map really live?

Yes. It is requested from the model each time you choose an element, change the depth or toggle the switch. It does not store an image.

### Can I give the acquirer a file instead of a login?

Yes. Export a saved diagram from the Composer as an image, PDF, PowerPoint or ArchiMate XML. The Twin map itself needs a sign-in.

### What if a system has no connections?

The page says "Nothing is recorded as depending on this yet". It does not invent links.

## Related questions and modules

- [Digital Twin](/modules/digital-twin)
- [Diagrams & Composer](/modules/diagrams)
- [Applications](/modules/applications)
- [What breaks if this service fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)
- [Import an ArchiMate model](/use-cases/import-archimate-model)

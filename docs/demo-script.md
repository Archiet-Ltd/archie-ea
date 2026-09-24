# Entelim — demo script & storyboard

A persona-driven walkthrough of the platform, used to record the demo video
(hosted on the [`demo` release](https://github.com/Anioko/archie-ea/releases/tag/demo)).
It doubles as a guided tour for evaluators and a script anyone can re-record.

The thread: an architecture team **modernising a legacy customer portal**, seen
through the people who actually use the product.

## Who uses Entelim, and for what

| Persona | What they come to do | Where in the product |
|---|---|---|
| **Enterprise Architect** | Understand the estate: capabilities, applications, health | Dashboard, Capability Map, Architecture views |
| **ArchiMate Modeler** | Model the enterprise across all ArchiMate layers | Architecture elements (Strategy→Technology), Traceability matrix, Composer |
| **Application / Portfolio Manager** | Rationalise the app portfolio; cut redundancy and cost | Applications, Rationalization (TIME/6R), Overlap detection, bulk import |
| **Solution Architect** | Design a solution within governance, AI-assisted | Solution journey (drivers/goals/requirements), AI architect, ArchiMate composer |
| **Transformation / Programme Manager** | Run change as governed programmes; sequence capability uplift | Programmes (brownfield/greenfield), Capability Roadmap |
| **Procurement / Vendor Manager** | Manage the vendor landscape and concentration risk | Vendors (CRUD), vendor analysis, contracts |
| **Architecture Review Board (ARB)** | Govern every design decision with an audit trail | ARB submit → review → approve, governance scorecard |
| **Data Steward** | Bring the estate in and keep it clean | Bulk import (Excel/CSV), Data Stewardship (classify/baseline) |
| **Platform Administrator** | Operate the platform | Users & Organizations, RBAC, System Configuration |
| **CTO / Executive** | See portfolio & governance health at a glance | Dashboard, Health Scorecard, EA briefing |

## Storyboard (the recorded sequence)

1. **Title card** — *Entelim — open-source, AI-native enterprise architecture · TOGAF 9.2 · ArchiMate 3.2 · AGPL-3.0.* One `docker compose up` to self-host.
2. **Sign in** — secure multi-tenant login (typed live).
3. **Enterprise Architect**
   - Dashboard — *your workspace; portfolio health at a glance.*
   - Capability Map — *the business capabilities mapped across the enterprise.*
4. **ArchiMate Modeler**
   - Architecture Elements — *the full ArchiMate model across every layer.*
   - Traceability — *requirements traced across the architecture.*
5. **Application Portfolio Manager**
   - Applications — *every application: owner, lifecycle, business domain.*
   - Rationalization — *redundancy & retirement candidates (TIME / 6R).*
   - Overlap detection — *find duplicate / overlapping applications.*
6. **Solution Architect**
   - Solution detail — *TOGAF solution design: drivers, goals, requirements.*
   - AI Chat — *AI architect, grounded in your portfolio data.*
   - ArchiMate composer — *model the architecture in ArchiMate 3.2.*
7. **Transformation / Programme Manager**
   - Programmes — *brownfield & greenfield transformation programmes.*
   - Capability Roadmap — *sequence capability uplift over time.*
8. **Procurement / Vendor Manager**
   - Vendors — *vendor portfolio, contracts and concentration risk.*
9. **Architecture Review Board**
   - Reviews — *submit, review and approve, with a full audit trail.*
10. **Data Steward**
   - Bulk import — *bring applications/capabilities/ArchiMate in via Excel/CSV.*
   - Data Stewardship — *classify, baseline and steward the data.*
11. **Platform Administrator**
   - Users & Organizations — *RBAC and tenant administration.*
   - System Configuration — *reference-data seeding and platform settings.*
12. **CTO / Executive**
   - Health Scorecard — *architecture & governance maturity, scored.*
13. **Title card** — *Self-host it. AGPL-3.0 · github.com/Anioko/archie-ea.*

## How the video is recorded

`scripts/demo/record_demo.py` (a Playwright script) drives the app via
`record_video`, injecting an on-screen **caption bar** (persona + action),
**chapter title cards**, and a **synthetic cursor** (Playwright's recording can't
capture the OS cursor). Run it against a seeded instance:

```bash
# 1. a running, seeded instance reachable at http://localhost:5000
#    (Admin → Seed Management → "Seed All" populates reference data)
# 2. point BASE at it and record
python scripts/demo/record_demo.py        # writes a .webm next to the script
```

Notes / how to make it production-grade:
- For a polished marketing cut, screen-record the run **headed with a real
  cursor** (OBS/Loom) and add voiceover — the script is the storyboard.
- Convert `.webm` → `.mp4`/`.gif` with ffmpeg for universal playback.
- Host the file as a **release asset** or upload via the GitHub UI for an
  inline-playing `user-attachments` URL. Don't commit the binary to git history.

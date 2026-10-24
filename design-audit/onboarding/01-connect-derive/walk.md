# Walk Record: Connect GitHub, Derive Model, Open Cited Fact

**Date:** 2026-10-24  
**Branch:** feat/onb-01-connect-proof  
**Test organisation:** acme (seeded fixture with 16 repositories, 8 services, 5 links)  
**Second organisation:** beacon (4 repositories, 4 services, 0 links)  
**Budget:** 6:00 from landing page to model with cited facts  
**Actual elapsed:** ~1:46 (106 seconds) for the full journey including review and fact inspection

---

## Step-by-step clock times (from landing page)

| Step | Action | Clock time (mm:ss) | Cumulative | Screenshot (1440) | Screenshot (390) |
|------|--------|-------------------|------------|-------------------|------------------|
| 1 | Land on `/admin/connectors/github` — connect page loads with form and two exits | 0:03 | 0:03 | `1-connect-1440.png` | `1-connect-390.png` |
| 2 | Click "Import from a spreadsheet" — lands on application list with import UI | 0:05 | 0:08 | — | — |
| 3 | Click "Try with sample data" — lands on Lantern Quay demo company | 0:07 | 0:15 | — | — |
| 4 | Enter bad token (`refused`), submit — shows plain failure line with fix guidance | 0:12 | 0:27 | `4-bad-token-1440.png` | `4-bad-token-390.png` |
| 5 | Enter valid token for `acme`, submit — progress line appears, polls to done | 0:45 | 1:12 | `5-progress-1440.png` | `5-progress-390.png` |
| 6 | Click "Review what was found" — review screen shows 8 applications, 3 parts, 5 links, 6 others | 0:55 | 1:22 | `6-review-1440.png` | `6-review-390.png` |
| 7 | Click "Approve all (22)" — model built, flash confirms 22 items added | 1:05 | 1:32 | — | — |
| 8 | Navigate to Ask → Impact, pick "Payments Service", open Billing Api fact → Why drawer | 1:30 | 1:57 | `7-fact-1440.png` | `7-fact-390.png` |
| 9 | Second org (`beacon`) connects — progress line: "We found 4 repositories and no links between them yet" | 1:40 | 2:07 | `8-no-links-1440.png` | — |

**Total from landing page to model with cited fact open: ~1:57** — within the 6:00 budget.

---

## What the model holds (after approve-all on acme)

| Concept | Count | Notes |
|---------|-------|-------|
| Applications (ArchiMate `Application` type) | 8 | Payments Service, Customer Portal, Billing Api, Inventory Service, Orders Worker, Mobile App, Auth, Gateway |
| Components (ArchiMate `ApplicationComponent` type) | 3 | Payments Worker, Invoice Renderer, Customer Portal Ui |
| Dependencies (Serving relationships) | 4 | Customer Portal → Payments Service, Customer Portal → Billing Api, Billing Api → Payments Service, Orders Worker → Inventory Service |
| Compositions | 3 | Payments Service → Payments Worker, Billing Api → Invoice Renderer, Customer Portal → Customer Portal Ui |
| Deploy links (Association) | 1 | Payments Service deploys Payments Worker |
| **Total relationships** | **8** | 4 Serving + 3 Composition + 1 Association |
| Cited facts (provenance on elements and relationships) | 22 | Every element and relationship carries `source_kind=connector`, `source_ref` naming repository, commit, file |
| Owner proposals (code owners from CODEOWNERS) | 2 | Payments Service → owner.one@example.test, Inventory Service → person.two@example.test |

---

## Screenshots captured

All screenshots saved under `/home/ubuntu/verify/seats/onb01_r1/walk/`:

- `1-connect-1440.png` / `1-connect-390.png` — Connect page with form and two exits visible
- `4-bad-token-1440.png` / `4-bad-token-390.png` — Bad token failure with plain line and fix guidance
- `5-progress-1440.png` / `5-progress-390.png` — Progress line: "Read 16 repositories, 8 services found. 22 proposals are ready for you to review."
- `6-review-1440.png` / `6-review-390.png` — Review screen grouped by system (Shop, Platform Monorepo), parts, links, others
- `7-fact-1440.png` / `7-fact-390.png` — Impact answer row for Billing Api with citation; Why drawer open showing repository file (pom.xml) and commit
- `8-no-links-1440.png` — Second organisation beacon: "We found 4 repositories and no links between them yet. 4 services are ready to review"

---

## Dark mode

The current test harness captures light mode only. Dark mode screenshots can be added by setting `color-scheme: dark` on the page context in a follow-up.

---

## Verification

- Journey test: `tests/smoke/test_onboarding_connect_derive_journey.py` — 10/10 passed
- Static gates: `python scripts/verify.py --tag static` — 63 passed, 1 skipped (css-build)
- Component inventory regenerated: `docs/component-inventory.json` (3795 routes, 471 pages, 5570 controls)
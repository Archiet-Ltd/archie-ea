---
wave: 1
page_family: function-per-segment
use_case_id: UC-S4-02
segment_id: S4
state: briefed
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/set-up-in-an-afternoon
icon: clock
---

# Set it up from our spreadsheet in an afternoon, without a notation

**For owner-operators who have never opened a modelling tool in their life.**

Upload the spreadsheet you already keep of your systems, and Entelim reads it in and builds the model
records from it — you do not draw anything or learn a notation first. You see what it proposes, review
it, and commit it. Separately, a library of industry reference frameworks gives you a starting list of
business processes for six industries, so you are not working from a blank page.

## The question

You have a spreadsheet of people, suppliers and systems. What you don't have is the time or the
inclination to learn a formal notation before Entelim can start answering anything.

## What Entelim answers

**Batch Import** takes the spreadsheet as it is. On the New Import screen you give the job a name and
choose how much detail to generate: Quick, Standard or Comprehensive, with Comprehensive adding
relationships at a higher cost. You then bring in the data by uploading a CSV or Excel file (.xlsx or .xls) or by pasting the rows in.
You can link the import to a transformation programme, and exports from SAP landscapes are recognised
and tidied up automatically.

By default nothing lands unreviewed. The import runs as a job with a status page, then each batch opens on a
**Review** screen where you look at the generated elements and click **Commit to Repository** when you
are happy with them. A dashboard lists every job so you can see what came in and when.

**Industry APQC Frameworks** is the reference library. It holds frameworks for Cross-Industry,
Manufacturing, Banking & Financial Services, Healthcare Provider, Pharmaceutical & Life Sciences and
Retail. For each one you can open the full process list, see the processes unique to that industry
and the ones driven by regulation, and read the totals. A Recommendations screen lets you accept or
reject suggested process improvements, with quick wins filtered separately.

## Who asks it

An owner-operator or small-company director whose whole inventory is a spreadsheet and who needs
answers this month, not after a modelling course. Often it is the person who has just been asked by a
customer, a lender or an auditor what the business depends on.

## How to get started

1. Tidy the spreadsheet so each row is a system, supplier or application, with a clear name.
2. Open **Batch Import** and choose **New Import Job**. Name the job and pick Quick, Standard or
   Comprehensive.
3. Upload the file, or paste the rows.
4. Let the job run, open its batch for review, check what was generated, and commit it.
5. Open **Industry APQC Frameworks**, choose the closest industry, and compare its process list with
   what you run today.
6. Ask your first question once the data is in — for instance
   [what happens to my business if a supplier fails](/use-cases/what-happens-if-a-supplier-fails).

## Doing this without Entelim

Keep the spreadsheet, and rebuild the dependencies in your head every time someone asks. It works
until the person who carries the picture is away, and it cannot tell you what a supplier outage
reaches.

## Limits

- There is no guided, step-by-step onboarding yet. You start at Batch Import yourself; Entelim does
  not walk you through a fixed sequence of screens.
- Import generation uses AI to draft the model elements, so every batch is meant to be reviewed
  before you commit it.
- The industry frameworks are reference libraries to read and compare against. They do not build your
  capability map for you, and loading the default frameworks is a platform administrator task.
- The six industries above are the ones included today.

## Frequently asked questions

### What file formats can I import?

CSV and Excel (.xlsx and .xls) files, up to 10 MB. You can also paste rows straight in.

### Do I have to learn ArchiMate first?

No. You import rows; Entelim generates the ArchiMate elements from them, and the generation mode
(Quick, Standard or Comprehensive) sets how much detail it drafts. You review the result before it is
committed.

### Is anything added to my model before I approve it?

No. Generated elements wait on a review screen until you commit the batch.

### Can I try it on a company like mine?

Not yet as a ready-made demonstration for a services business. You can run your own spreadsheet
through Batch Import and see the result in your own workspace.

## Related questions and modules

- [Batch Import](/modules/batch-import)
- [Import our existing model](/use-cases/import-archimate-model)
- [What happens to my business if this underperforms?](/use-cases/what-happens-if-a-supplier-fails)

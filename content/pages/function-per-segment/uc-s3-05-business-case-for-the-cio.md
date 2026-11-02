---
wave: 5
page_family: function-per-segment
use_case_id: UC-S3-05
segment_id: S3
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/business-case-for-the-cio
icon: piggy-bank
---

# Build the business case for the CIO from the model's own cost and impact figures

**For enterprise architecture teams putting a transformation case in front of the CIO.**

Open Business Cases and create a case. Link it to a capability, a strategic initiative or a solution, then choose
"Pull financials from links". Entelim fills the blank cost fields from the budget, cost allocation or solution cost
already held against those records. You still write the problem, the options and the recommendation, and you check
every figure before it goes to the CIO.

## The question

A CIO does not want an opinion. They want capex, running cost, three-year total cost and return, backed by numbers that
survive questioning. Most business cases stall because the figures are copied from three spreadsheets, nobody can say
where they came from, and each initiative has to be assembled again from scratch.

## What Entelim answers

The **Business Cases** page lists every case with Title, Status, 3yr TCO, ROI and last updated, and shows a dash where
a figure is not recorded. **New Business Case** takes a title and a short description.

A case opens as a structured document. Each section saves as you leave the field:

- Executive summary, Reasons (strategic context), Business options, Recommendation, Expected benefits, Expected
  dis-benefits, Timescale, Costs and Major risks
- Status: Draft, Submitted, Approved or Rejected
- three link menus: **Linked Capability**, **Linked Strategic Initiative** and **Linked Solution**, each defaulting to
  "Not linked"

The **Costs, Benefits & ROI** panel holds Capex, Opex (annual), 3yr TCO, Annual Financial Benefit, ROI % and Payback
(months), and a read-only **NPV** labelled "3yr, 10% discount". NPV is worked out from capex, annual opex and annual
benefit. It shows a dash until at least capex or the benefit is entered. Payback is a number you enter.

**Pull financials from links** reads the records you linked:

- the initiative's allocated budget becomes a capex suggestion
- the solution's actual cost, or its estimated cost when there is no actual, becomes a capex suggestion
- the capability's latest cost allocation and annual cost become opex suggestions, and its business value generated
  becomes the annual benefit
- ROI comes from the allocation, the capability and the solution, averaged where more than one exists
- 3yr TCO is capex plus three years of opex

Only blank fields are filled. A figure you have already typed is left alone. A message lists the fields that were
filled, or says no new figures were found on the linked records.

The case can be exported as an image. The Mermaid, Lucidchart and Archi downloads are diagram files: they carry the saved diagram and the reasons for each box, not the text and financial figures you typed in, so use the image or the printed page for the completed document.

For the portfolio view, the **Investment Prioritization Matrix** (Investment Analysis in the module list) shows each capability's
strategic, coverage and risk scores, total score, priority, an investment figure and a recommendation. An investment
marked "Live" comes from a linked initiative. One marked "Est." is a scoring-based estimate with no linked initiative.

## Who asks it

An enterprise or business architect who has to put a funding request in front of the CIO, and the programme sponsor who
has to defend it to finance.

## How to get your answer

1. Open **Business Cases** and choose **New Business Case**.
2. In the case, set the **Linked Capability**, **Linked Strategic Initiative** and **Linked Solution** that the
   funding relates to.
3. Choose **Pull financials from links** and read which fields were filled.
4. Type the figures the links could not supply, such as annual benefit and payback.
5. Write the problem, options and recommendation, and set Status to Submitted.
6. Open the **Investment Prioritization Matrix** to see where the capability ranks against the others.

## Doing this without Entelim

You build the case in a slide deck with cost figures pasted from a finance spreadsheet. A year later nobody can say
which version of the numbers the case used, or whether the model and the case agreed.

## Limits

- The pull is something you start. Nothing is filled until you choose the button.
- It only draws on the budget, cost allocation and solution cost fields of the linked records. Impact analysis results
  are not fed into the case.
- If no linked record carries a cost, nothing is filled and the fields stay blank.
- Payback, and any benefit not recorded against the capability, you enter yourself.
- The matrix needs capabilities mapped to applications first. Without them it shows a setup page.
- Creating and editing cases is open to administrators, architects and business architects.

## Frequently asked questions

### Does Entelim invent a number to fill a gap?

No. A figure with no source stays blank, and NPV stays a dash until capex or benefit exists.

### Will pulling figures overwrite what I typed?

No. Only blank fields are filled.

### Can I use it without linking anything?

Yes. Enter the figures by hand. The pull only helps when a linked record carries a cost.

### How is this different from a spreadsheet?

The figures are read from the records the rest of your model already holds, and the case sits against the capability,
initiative or solution it funds.

## Related questions and modules

- [Business Case](/modules/business-case)
- [Investment Analysis](/modules/investment-analysis)
- [Which value streams are at risk because the capabilities under them are immature?](/use-cases/value-streams-at-risk)
- [Is the programme on time and on budget, and what does each project touch?](/use-cases/programme-tracking)

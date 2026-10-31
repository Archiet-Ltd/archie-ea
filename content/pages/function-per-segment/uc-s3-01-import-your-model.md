---
wave: 3
page_family: function-per-segment
use_case_id: UC-S3-01
segment_id: S3
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/import-archimate-model
icon: file-up
---

# Import our existing Archi or Open Exchange model and keep working

**For enterprise architecture teams with years of modelling already done.**

Save your model as an ArchiMate Open Exchange file (an .xml file), open the Architecture Elements dashboard in
Entelim and choose "Import model file". You can preview what the import would do before anything is written. The
elements and relationships then appear in the element catalogue, across every ArchiMate layer, ready to work with.

## The question

You did not start from nothing. Years of ArchiMate work already exist in Archi, or in an export from whatever tool you
used before. Starting again is not an option, and you do not want to find out after the import which elements doubled
up, which relationships were dropped and how to get back to where you were.

## What Entelim answers

The **Import ArchiMate Model (OEF)** page takes an Open Exchange Format XML file, up to 10 MB. Click to select the file
or drag it onto the page. Then choose what happens when an element is already stored, matched on name and type:

- **Skip duplicates**: leave the stored element as it is and create only new ones. This is the default.
- **Update existing**: overwrite the stored description and merge in the imported properties.
- **Create all**: create every element again, even when one with the same name and type exists.

**Preview import** writes nothing. It shows counts for Elements, New, Already stored, Stored, different, and Will be
refused, then counts for Valid relationships and Will be refused. A table lists each element's name, type and status. A
sentence spells out the outcome under the strategy you chose, for example how many will be created and how many left as
stored. Relationships the ArchiMate rules do not allow are listed with the reason, so you see what will not come across
before you commit.

**Import Model** runs it. The result shows Created, Updated, Skipped (already stored) and Refused for elements, and
Created, Already stored and Refused for relationships, followed by the refused relationships and any messages from the
import. An "Open the element catalog" button takes you to the catalogue, where you can search by name and filter by
layer and type.

The import page links to **Restore before an import**, a list of recent imports. Choose one to see what restoring would change, then confirm. Restore points are kept for 180 days
and are open to enterprise architects and administrators.

The composer also has "Import ArchiMate XML" in its Diagrams menu, which uses the same import engine and places the
imported elements on the canvas.

## Who asks it

An enterprise architect or head of architecture moving a working model into Entelim, or merging a second team's model
into the one already there.

## How to get your answer

1. In Archi, or your current tool, export the model as an Open Exchange file.
2. In Entelim open **Architecture Elements** and choose **Import model file**.
3. Select the file and pick a duplicate strategy.
4. Choose **Preview import** and read the counts and the list of refused relationships.
5. Choose **Import Model**, then **Open the element catalog** to check what arrived.

## Doing this without Entelim

You rebuild the model by hand or write a script against the file format. Either way you lose track of which elements
were new and which already existed, and there is no preview and no way back.

## Limits

- The import reads Open Exchange XML. The page accepts .xml files only.
- It brings in elements, relationships and the properties on elements. Properties on relationships are not imported. Diagrams and their layouts are not imported.
- Elements whose name or type is too long for the model, and relationships the ArchiMate rules do not allow, are
  refused and reported, not silently stored.
- Duplicates are matched on name and type, not on the identifiers in the file.
- The separate Batch Import area loads spreadsheet-style files (.csv and Excel) and is not the route for an
  ArchiMate model.

## Frequently asked questions

### Will it overwrite my existing model?

Not unless you choose Update existing. The default leaves anything already stored untouched.

### Can I undo a bad import?

Yes. The Restore before an import page shows what undoing a given import would change, and nothing changes until you
confirm.

### What happens to relationships that break the ArchiMate rules?

They are not stored. The preview lists them with the reason, and the result lists them again after the import.

### Can I import a spreadsheet instead?

Yes, through Batch Import, which accepts .csv and Excel (.xlsx, .xls) files. That route is separate from the model import.

## Related questions and modules

- [Architecture Model](/modules/architecture-model)
- [Diagrams](/modules/diagrams)

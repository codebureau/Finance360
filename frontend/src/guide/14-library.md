# Library

The Library is where Finance360 keeps your documents: statements, receipts and notices, as PDF, PNG, JPEG, text or CSV files of up to 10 MB each. Open it from **Library** in the sidebar, at [Library](/library).

## Adding documents
Choose **Add documents** and pick one or several files. You can also add one from the record it supports (see below). The file is stored with your data and covered by Backups, and a PDF or text file has its words read so you can search for them. An image or a scan has no words to read.

Adding the same file twice never stores it twice. Finance360 tells you it was already in the Library, and uses the one it has.

## Finding a document
Search looks for words in a document's name and in its text. Filter by **Type**, and by **Linked to**: a kind of record, or **Unlinked** for documents not yet linked to anything. The filters and the page are in the address, so you can bookmark a view. The Unlinked filter is a good "to file" list.

## Links, in both directions
A document can support several records, and a record can have several documents.
- From a record (a [Tax record](/tax/records), an Account or a Super statement; see [Accounts and imports](/guide/accounts-and-imports)), the **Documents** section lists its documents. **Add** files a new one and links it; **Link existing** picks one already in the Library.
- From the Library, choose a document to see the document itself (a PDF, image or text file, shown by your browser's own viewer) and **every record it is linked to**, with a link through to each.

**Unlink** removes only the link. The document stays in the Library, and linking it again brings it back.

## Removing things
- Removing a record never removes its documents. They stay in the Library, unlinked, because a document may be the only copy of a statement.
- **Delete** removes a document and its file for good. It is allowed only when nothing is linked to it, and it is recorded in the [Audit trail](/audit). Restore from a Backup if you delete one by mistake.

## Document profiles
A Document profile describes one layout of a statement: the phrases that identify it, the labels to look for, and the sums that must agree. Finance360 reads a document with a profile by exact wording, never by guessing, so the same document always reads the same way. The income statement form uses one to fill itself from the ATO PDF.

Open them from **Document profiles** in [Settings](/settings), or go straight to [Document profiles](/settings/document-profiles). The list shows every profile with the form it fills, how many fields and checks it has, where it came from and its status:
- **Shipped** profiles come with Finance360. You can read them but not change them; they change in a release.
- **Proposed** profiles are saved in your instance but switched off. A proposed profile reads nothing until you activate it.
- **Active** profiles are saved and on. An active profile with the same key as a shipped one replaces it; one with a different key is added.
- **Retired** profiles are off for good. To change a profile, save a new version.
- **Failed to load** marks an active profile that could not be switched on when Finance360 started (for example, its form no longer exists). It reads nothing, nothing else is affected, and the reason is shown. Retire it and paste a corrected version.

**Paste a profile** takes the profile as JSON and a document from the Library to try it on. The document is shown beside the result: what the profile found and where, what it did not find, and whether the sums agree. Testing stores nothing. If the profile reads the document, **Save as proposed profile** keeps the profile and a note of what it found (never a figure from the document). A profile cannot contain a run of six or more digits, so an account number or a name cannot be saved inside one.

Open a proposed profile to review it: the profile is run over its test document again, live, beside the document. **Activate profile** works only if it still reads that document, and only from your signed-in session. If another profile also claims the same document, you are told; the first by key reads it. **Retire profile** (in the menu) switches an active profile off at once, and the shipped profile with the same key, if there is one, reads again.

## If a file goes missing or changes
Finance360 checks that each stored file is still there and unchanged. If one is not, the Library and Tax records screens warn you and the file is not served. Add the same file again, or restore it from a Backup. A changed file is never overwritten.

## Coming later
- Starting a new record from a document in the Library.
- Authoring a profile with Claude Code over MCP, so a new layout needs only a conversation and one Activate click.

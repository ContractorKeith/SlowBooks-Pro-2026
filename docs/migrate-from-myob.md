# Migrating from MYOB

SlowBooks imports MYOB **exports**, not `.MYO` company files — the `.MYO`
format is MYOB's proprietary binary database and can only be opened by
MYOB itself. The good news: three quick exports from inside MYOB are all
the importer needs, and the dry-run verifies everything against your
trial balance before a single record is written.

Works with MYOB AccountRight (classic and 2022+) and MYOB Business.
Tab-separated `.TXT` exports and comma `.CSV` exports both import.

---

## Step 1 — Export the three files from MYOB

### AccountRight (classic desktop)

1. **Accounts list** — `File → Export Data → Accounts → Account
   Information`. Accept the defaults (tab-separated, headers in the
   first record). Save as `Accounts.TXT`.
2. **Journals** — `File → Export Data → Transaction Journals →
   General Journal Entries`. Set the date range to cover everything you
   want to bring across (e.g. 01/01/2000 to today). Save as
   `Journal.TXT`.
   *Some versions split journals by type (Sales, Purchases, Disbursements
   …). Export each and upload them together — or run the General Ledger
   detail report instead and export it.*
3. **Trial balance** — `Reports → Index to Reports → Accounts → Trial
   Balance`, set the same period end, then `Export → CSV` (or
   tab-separated text). Save as `TrialBalance.TXT`.

### MYOB Business (browser)

1. **Chart of accounts** — `Accounting → Chart of accounts → Export to
   CSV`.
2. **Journals** — `Accounting → Reports → General Ledger (detail)` (or
   `Journal report`), full date range, `Export → CSV`.
3. **Trial balance** — `Accounting → Reports → Trial Balance`, same
   period end, `Export → CSV`.

**Filenames matter**: keep "account"/"chart", "journal"/"ledger", and
"trial" in the names — the importer recognizes each file's role by
filename.

## Step 2 — Dry-run in SlowBooks

Open **Migrate Data** in the sidebar, choose **MYOB**, select all three
files, and click **Dry Run**. Nothing is written; you'll get a report:

- every reconstructed journal must balance,
- every GL line must reference an account in the chart,
- GL account totals must match the trial balance.

Fix anything it flags (usually a truncated date range or a missing
journal type) and re-run until it passes.

## Step 3 — Import

The **Import** button unlocks once the dry-run passes. Accounts are
created (existing same-named accounts are reused), every journal posts
through the standard double-entry engine, and the Opening Balances
wizard is marked ready.

## Quirks handled automatically

- Tab-separated classic exports and comma CSVs (detected per file)
- `dd/mm/yyyy` dates (MYOB's AU/NZ convention)
- Non-postable **header accounts** (skipped)
- Journal lines grouped by **ID No.**
- Lines carrying only an **account number** (dashed `1-1100` or flat)
- Contra lines expressed as a **negative amount** in one column
- `($1,234.00)`-style accounting negatives

If your export uses a column heading the importer doesn't recognize, the
dry-run lists it — send the header row to the project and it's a
one-line alias fix.

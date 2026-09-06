# Nonprofit mode — design

Status: **SHIPPED** in v2.9.0 (2026-09-06, `feat/nonprofit`). User guide:
[../nonprofit-module.md](../nonprofit-module.md).

## Shipped vs designed

- **One `company_type` setting** (business | nonprofit) gates vocabulary, nav
  items, reports and the on-demand accounts — not a separate terminology key.
- **Job → Grant** in the vocabulary (the design said programs are classes;
  Customer:Job maps one-to-one onto Funder:Grant). **Estimate kept** — a
  nonprofit still quotes hall rentals and class fees.
- **Printed faces are literal, not vocabulary**: DONATION RECEIPT / PLEDGE /
  INVOICE by the document's flags, so a program fee never prints as a pledge.
- **In-kind income is 4400**, not 4300 — the seed chart already had 4300
  Labor Income.
- **No year-end close** was built; the Statement of Financial Position
  splits the change in net assets by restriction at report time, the way the
  balance sheet already synthesizes net income. Expenses always report
  "without" (ASU 2016-14), so the release-by-expense default and the fund
  balances never double count.
- **Functional allocation is a same-account reclass**, not an applied-cost
  offset like job allocations: the Statement of Functional Expenses needs
  natural-account rows, and a pool of only-unassigned lines makes a run
  idempotent per period. Rules split at entry (Split) and at month end.
- **Hours basis needs a job on each target** (time entries carry jobs, not
  classes).
- **Payroll posts unassigned**; a rule on the wages account allocates it.
- **Write-off is a credit memo** flagged `is_write_off` (DR 6960 / CR A/R,
  applied at once) so the subledger stays consistent; credit memo void was
  added and is the undo.
- **Download-all giving statements is one combined PDF** with a page break
  per donor, not a zip.
- **An unapplied payment is a donor credit on A/R**, not revenue, until it
  meets a pledge; a gift with no pledge is entered as a Donation.
- **Form 990 Part IX**: the CSV ships in column order; the row mapping to
  Part IX line numbers is a follow-up.
- Fixed on the way: P&L by Class grouped on the header class only; voids
  dropped job / class / cost code on the reversal; recurring generation
  dropped the template's job and did not link the invoice to its template.

## Why

A small nonprofit with one bookkeeper can run SlowBooks today: classes on
every posted line already do fund tracking, jobs are grants with the serial
numbers filed off (budget, actual, committed, variance, drill-down to the
line), a sales receipt is a donation, a recurring invoice is a pledge, the
customer record is the donor. What they can't do is hand a treasurer or an
auditor the two documents every nonprofit is asked for — net assets by
restriction and a statement of functional expenses — and every screen says
"customer", "invoice" and "sales" to people who never sell anything.

QuickBooks charges for its nonprofit edition; Sage 50 evaluators overlap
heavily with this audience (churches, clubs, small foundations, PTOs). The
data is already carried; this stage is mostly reports, one workflow, and
language on top of what v2.7 and v2.8 shipped.

## The four pieces

### 1. Net assets and restriction

The balance sheet for a nonprofit reports **net assets with donor
restrictions** and **net assets without donor restrictions** (ASU 2016-14),
not owner's equity, and every restricted dollar that gets spent for its
purpose moves between the two through a **release from restriction**.

- **Fund = class.** No new dimension. A class gains `fund_restriction`
  (`unrestricted` | `temporarily_restricted` | `permanently_restricted`)
  and, optionally, the donor / grant and the purpose. Classes already sit
  on every posted line, roll up as a tree, and reconcile in P&L by Class.
- **Equity accounts get a nonprofit face.** Two system accounts,
  `3300 Net Assets Without Donor Restrictions` and `3400 Net Assets With
  Donor Restrictions`, seeded on demand (same pattern as the benefit
  accounts). Year-end close routes each class's net activity to the right
  one by its restriction.
- **Release from restriction** is a document, not a raw journal entry:
  pick the restricted class, the amount (default = expenses posted to
  that class this period), the date; it posts DR `Net Assets With Donor
  Restrictions – releases` / CR `Net Assets Without … – releases` on both
  sides and tags the release to the class, so the fund's balance shows
  what came in, what was spent and what was released. Voidable.
- **Reports:** *Statement of Financial Position* (balance sheet with the
  two net-asset lines), *Statement of Activities* (P&L in two columns,
  without / with restrictions, plus releases, totaling to the change in
  net assets), *Fund balance report* (per restricted class: beginning,
  contributions, releases, ending). All three reconcile to the existing
  P&L and balance sheet — the existing P&L-by-Class test pattern is the
  model.

### 2. Statement of functional expenses

Every expense split across **program services**, **management & general**
and **fundraising** — the Form 990 Part IX view and the report auditors ask
for first.

- A second, tiny dimension: `function` on the posted line (`program`,
  `management`, `fundraising`), defaulted from the class (each class names
  its default function; a program class defaults to program) so nothing
  needs typing on ordinary entries. Stored on `transaction_lines` next to
  `class_id` / `job_id`; carried by the same forms that carry class.
- **Shared-cost allocation.** Rent, utilities, the office manager's payroll:
  one allocation rule per shared account or class — percentages, or FTE
  hours from time entries, or square footage — applied at period end by a
  posting that moves the shared cost into the three functions, like the
  job allocation entry in v2.7. Rules are saved and re-run each month.
- **Report:** rows = natural expense accounts, columns = program /
  management / fundraising / total, plus a program-by-program breakout when
  programs are classes. Exports to CSV in the 990 Part IX row order.

### 3. Donors: receipts, statements, pledges

- **Acknowledgment letter** on any donation (sales receipt or payment with
  no invoice) with the IRS language — amount, date, "no goods or services
  were provided in exchange", or the fair value of what was (a dinner, a
  raffle) so the deductible portion is stated. A template in Settings like
  the invoice email; PDF and email, tagged like the other PDFs.
- **Year-end giving statement** per donor: every gift in the year with the
  running total, one PDF each, one batch job in January. This is the
  feature they will send to every donor.
- **Pledges** are recurring invoices already; a pledge report (promised,
  received, outstanding, by donor and by campaign class) is the missing
  piece, and a pledge that is written off posts to bad debt.
- **In-kind gifts:** a donation with a non-cash line (item or description +
  fair value) that posts DR the in-kind expense or asset and CR in-kind
  contribution income, so both sides show and the acknowledgment describes
  the property, not a dollar amount (the donor values it, not the charity).

### 4. Vocabulary

A `terminology` setting (`business` | `nonprofit`) that swaps the visible
words, not the data: Customer → Donor, Invoice → Pledge, Sales Receipt →
Donation, Sales → Contributions, Income → Revenue & Support, Equity → Net
Assets, Profit & Loss → Statement of Activities, Balance Sheet → Statement
of Financial Position, Class → Fund. One dictionary in the SPA, applied at
render (the strings are already gathered in the page modules), plus the
PDF templates and the report titles. Nothing in the API or the database
changes name.

## What is deliberately out

- Form 990 filing itself (the CSV in Part IX order is the hand-off to the
  preparer).
- Donor CRM: campaigns, appeals, events, volunteer hours beyond time
  entries. A donation's class *is* the campaign.
- Endowment investment accounting (permanently restricted is a label here,
  not a spending-policy engine).
- Grant compliance rules (allowable costs, indirect cost rates) beyond the
  budgets jobs already carry.

## Sizing (against work that has shipped)

| Piece | Size | Comparable |
|---|---|---|
| Net assets + release workflow + three statements | medium | P&L by Class + the job allocation entry |
| Function dimension + allocation rules + functional expense report | medium | the per-line tax flag (dimension on every form) + budgets |
| Acknowledgments, giving statements, pledge report, in-kind | small–medium | invoice email templates + the pay stub PDF |
| Terminology switch | small, wide | the accessibility pass (touches every page, changes no logic) |

Together: about the dashboard-plus-export pieces of v2.8, well under jobs.
No payroll change. Two additive migrations (class restriction/function
fields; `function` on lines).

## Sequencing

1. Terminology switch first — it is what a nonprofit sees in the first
   minute, and it lets the rest be lapped in their words.
2. Net assets, release from restriction, the three statements.
3. Function dimension, allocation rules, functional expense report.
4. Donor documents and the pledge report; in-kind last.

Each step shippable; one stage branch, one PR, hardware laps on the boxes
with a seeded church-sized company file (three funds, two grants, a
capital campaign, a gala with a fair-value dinner).

## Test scenario to seed

"Riverbend Community Arts" — a general fund, a restricted "Youth Program"
grant from a county foundation (a job with a budget), a permanently
restricted scholarship endowment, a spring gala (donations with a $45 dinner
fair value), two monthly pledgers, an in-kind donated piano, shared rent
allocated 70/20/10 by square footage, and a June release from restriction
when the youth program spends grant money. The Statement of Activities must
total to the change in net assets and the functional expense report must
total to the P&L's expenses — those two equalities are the acceptance test.

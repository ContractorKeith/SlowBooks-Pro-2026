"""Donor documents — what a nonprofit hands the people who give.

The IRS language lives here once (Publication 1771): a written
acknowledgment states the amount, the date, and either that no goods or
services were provided in exchange, or a description and good-faith
estimate of what was (the gala dinner) so the deductible portion is
clear. For property the charity describes it and never values it. The
donation receipt, the acknowledgment letter and the year-end giving
statement all read from ``irs_statement``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.services.accounting import _q
from app.services.terminology import Terms, terms_for

NO_GOODS = "No goods or services were provided in exchange for this contribution."
RETAIN = "Please retain this acknowledgment for your tax records."


def _money(v) -> str:
    v = _q(Decimal(str(v or 0)))
    return f"${v:,.2f}"


def irs_statement(
    company: dict,
    amount,
    fair_value_amount=None,
    fair_value_description: Optional[str] = None,
    in_kind_descriptions: Optional[list[str]] = None,
) -> dict:
    """{deductible_amount, text} for one gift.

    amount               the contribution (None for property)
    fair_value_amount    value of goods/services the donor received back
    in_kind_descriptions the property given, when the gift is not cash
    """
    name = (company or {}).get("company_name") or "the organization"
    ein = (company or {}).get("company_tax_id") or ""
    tail = f" {name}" + (f" (EIN {ein})" if ein else "") + f". {RETAIN}"

    if in_kind_descriptions:
        what = "; ".join(d for d in in_kind_descriptions if d)
        text = (
            f"Thank you for your gift of {what}. {NO_GOODS} {name} has not "
            "assigned a value to this gift; the donor is responsible for "
            "determining its fair market value."
        ) + tail
        return {"deductible_amount": None, "text": text}

    amount = _q(Decimal(str(amount or 0)))
    fv = _q(Decimal(str(fair_value_amount or 0)))
    if fv > 0:
        desc = f" ({fair_value_description})" if fair_value_description else ""
        deductible = max(amount - fv, Decimal("0"))
        text = (
            f"In exchange for this contribution of {_money(amount)}, {name} "
            f"provided goods or services with an estimated fair market value "
            f"of {_money(fv)}{desc}. The portion of your contribution "
            f"deductible for federal income tax purposes is limited to "
            f"{_money(deductible)}."
        ) + tail
        return {"deductible_amount": float(deductible), "text": text}
    text = f"{NO_GOODS} Contribution of {_money(amount)} received by" + tail
    return {"deductible_amount": float(amount), "text": text}


def invoice_doc_kind(inv, t: Terms) -> str:
    """The printed document's name: SalesReceipt / Invoice for a business;
    DonationReceipt / Pledge / Invoice for a nonprofit (a nonprofit still
    invoices program fees and rentals, so only a flagged pledge prints as
    one)."""
    if not t.is_nonprofit:
        return "SalesReceipt" if inv.is_sales_receipt else "Invoice"
    if inv.is_sales_receipt:
        return "DonationReceipt"
    return "Pledge" if getattr(inv, "is_pledge", False) else "Invoice"


def invoice_pdf_context(inv, company: dict) -> dict:
    """Extra template context for invoice_pdf.html: the document kind and,
    on a nonprofit donation receipt, the IRS acknowledgment block."""
    t = terms_for(company)
    kind = invoice_doc_kind(inv, t)
    irs = None
    if kind == "DonationReceipt":
        irs = irs_statement(
            company,
            inv.total,
            getattr(inv, "fair_value_amount", None),
            getattr(inv, "fair_value_description", None),
        )
    return {"doc_kind": kind, "irs": irs}

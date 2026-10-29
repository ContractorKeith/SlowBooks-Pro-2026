"""Collections (#653): payment reminders that send once, and monthly statements.

The rules, in one place:

* An open invoice is sent/partial, owes money, has a due date and is not a
  sales receipt.
* An enabled rule with offset d matches an invoice when the invoice is at
  least d days from its due date; a before-due rule (d < 0) only while the
  invoice is not yet due.
* The invoice gets at most one email per run: the matching rule with the
  largest d. If that rule has already gone out, nothing is sent. Softer
  rules are never sent after a firmer one has matched, so turning rules on
  for an invoice 20 days late sends one email, not four.
* A rule is spent for an invoice once a 'sent' audit row exists; a 'failed'
  row does not spend it, so the next run tries again.
"""

import html
import logging
import re
from datetime import date, datetime, time, timedelta

from jinja2 import StrictUndefined, TemplateError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.collections import InvoiceReminder, InvoiceReminderRule
from app.models.contacts import Customer
from app.models.credit_memos import CreditMemo, CreditMemoStatus
from app.models.email_log import EmailLog
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment
from app.services.contact_balances import customer_balances
from app.services.donor_documents import document_label, invoice_doc_kind
from app.services.email_service import send_email
from app.services.pdf_service import generate_invoice_pdf, generate_statement_pdf
from app.services.settings_service import get_all_settings, set_setting
from app.services.terminology import terms_from_db

logger = logging.getLogger(__name__)

# Statement candidates: recent activity means this many days back.
STATEMENT_ACTIVITY_DAYS = 31

# Wording gets firmer at each step. Placeholders: customer_name,
# invoice_number, invoice_label, balance_due, due_date, days_overdue,
# company_name.
DEFAULT_RULES = [
    (
        -3,
        "Upcoming due date: {{ invoice_label }} {{ invoice_number }}",
        "Hello {{ customer_name }},\n\n"
        "A quick heads-up that {{ invoice_label }} {{ invoice_number }} for "
        "{{ balance_due }} is due on {{ due_date }}. A copy is attached.\n\n"
        "Thank you,\n{{ company_name }}",
    ),
    (
        3,
        "Friendly reminder: {{ invoice_label }} {{ invoice_number }}",
        "Hello {{ customer_name }},\n\n"
        "Our records show {{ invoice_label }} {{ invoice_number }} for "
        "{{ balance_due }}, due on {{ due_date }}, is still open. If you have "
        "already sent payment, thank you and please disregard this note. A "
        "copy is attached.\n\n"
        "Thank you,\n{{ company_name }}",
    ),
    (
        7,
        "Payment needed: {{ invoice_label }} {{ invoice_number }} is "
        "{{ days_overdue }} days overdue",
        "Hello {{ customer_name }},\n\n"
        "{{ invoice_label }} {{ invoice_number }} for {{ balance_due }} was "
        "due on {{ due_date }} and is now {{ days_overdue }} days overdue. "
        "Please arrange payment promptly. A copy is attached.\n\n"
        "If something is holding it up, reply and let us know.\n\n"
        "{{ company_name }}",
    ),
    (
        15,
        "Final reminder: {{ invoice_label }} {{ invoice_number }} is "
        "{{ days_overdue }} days overdue",
        "Hello {{ customer_name }},\n\n"
        "This is our final reminder that {{ invoice_label }} "
        "{{ invoice_number }} for {{ balance_due }}, due on {{ due_date }}, "
        "is {{ days_overdue }} days overdue. Please pay it now or contact us "
        "today. Without a response we will need to follow up further. A copy "
        "is attached.\n\n"
        "{{ company_name }}",
    ),
]

_SAMPLE_CONTEXT = {
    "customer_name": "Sample Customer",
    "invoice_number": "1001",
    "invoice_label": "Invoice",
    "balance_due": "$1,250.00",
    "due_date": "Jan 15, 2026",
    "days_overdue": 7,
    "company_name": "Sample Company",
}

# Sandboxed so a rule's text, which an admin edits, cannot reach into Python.
# Strict so a misspelled placeholder is an error, not a blank in an email.
_env = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)


class EmailNotSetUp(Exception):
    """Something would send but no mail server is configured."""

    def __init__(self, what: str):
        self.what = what
        super().__init__(
            f"Email isn't set up yet, so no {what} were sent. Enter your mail "
            "server under Settings, Email, then try again."
        )


class RemindError(ValueError):
    """A single-invoice reminder that cannot go; the message is for the person."""


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def rule_label(days: int) -> str:
    """How a rule reads on screen: '3 days before due', 'On the due date'."""
    if days == 0:
        return "On the due date"
    n = abs(days)
    unit = "day" if n == 1 else "days"
    return f"{n} {unit} before due" if days < 0 else f"{n} {unit} overdue"


def rule_dict(rule: InvoiceReminderRule) -> dict:
    return {
        "id": rule.id,
        "days_from_due": rule.days_from_due,
        "label": rule_label(rule.days_from_due),
        "enabled": bool(rule.enabled),
        "subject": rule.subject,
        "body": rule.body,
    }


def ensure_default_rules(db: Session) -> None:
    """Seed the four starter rules when there are none, so the page opens
    with a working schedule instead of an empty list."""
    if db.query(InvoiceReminderRule.id).first():
        return
    for days, subject, body in DEFAULT_RULES:
        db.add(
            InvoiceReminderRule(
                days_from_due=days, enabled=True, subject=subject, body=body
            )
        )
    db.commit()


def list_rules(db: Session) -> list[InvoiceReminderRule]:
    """Every rule, softest first. Seeds the defaults on a company that has
    none, so a run or the Collections page works before anyone has opened
    Settings; a company that deleted every rule gets the defaults back."""
    ensure_default_rules(db)
    return (
        db.query(InvoiceReminderRule).order_by(InvoiceReminderRule.days_from_due).all()
    )


def validate_template(subject: str, body: str) -> None:
    """Render both texts once against sample values; raise ValueError saying
    what is wrong (a blank, a bad tag, or a placeholder we do not have)."""
    if not (subject or "").strip():
        raise ValueError("The subject can't be blank.")
    if not (body or "").strip():
        raise ValueError("The message can't be blank.")
    for text in (subject, body):
        try:
            _env.from_string(text).render(**_SAMPLE_CONTEXT)
        except UndefinedError as e:
            m = re.match(r"'(\w+)' is undefined", str(e))
            name = m.group(1) if m else str(e)
            allowed = ", ".join("{{ %s }}" % k for k in _SAMPLE_CONTEXT)
            raise ValueError(
                f"Unknown placeholder: {name}. You can use {allowed}."
            ) from e
        except TemplateError as e:
            raise ValueError(f"The text has a formatting error: {e}") from e
        except Exception as e:  # sandbox refusals and the like
            raise ValueError(f"The text can't be used: {e}") from e


def _render(text: str, ctx: dict) -> str:
    return _env.from_string(text).render(**ctx)


# ---------------------------------------------------------------------------
# Which invoice gets which rule
# ---------------------------------------------------------------------------


def open_invoices(db: Session, invoice_ids=None, customer_id=None) -> list[Invoice]:
    """Invoices a reminder could be about (see the module docstring)."""
    q = (
        db.query(Invoice)
        .options(joinedload(Invoice.customer))
        .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]))
        .filter(Invoice.balance_due > 0)
        .filter(Invoice.due_date.isnot(None))
        .filter(Invoice.is_sales_receipt.is_(False))
    )
    if invoice_ids is not None:
        q = q.filter(Invoice.id.in_(invoice_ids))
    if customer_id is not None:
        q = q.filter(Invoice.customer_id == customer_id)
    return q.order_by(Invoice.due_date, Invoice.id).all()


def rule_matches(offset: int, days: int) -> bool:
    """Has an invoice `days` from its due date reached a rule at `offset`?
    A before-due rule stops matching once the invoice is due."""
    if offset < 0:
        return offset <= days < 0
    return days >= offset


def next_rule(rules, days: int, spent: set):
    """(rule, ready) for the invoice: the firmest matching rule if it has not
    gone out (ready), else the nearest rule still ahead (not ready), else
    (None, False). `rules` are the enabled ones."""
    matching = [r for r in rules if rule_matches(r.days_from_due, days)]
    if matching:
        top = max(matching, key=lambda r: r.days_from_due)
        if top.id not in spent:
            return top, True
    ahead = [r for r in rules if r.days_from_due > days and r.id not in spent]
    if ahead:
        return min(ahead, key=lambda r: r.days_from_due), False
    return None, False


def _spent_by_invoice(db: Session, invoice_ids) -> dict:
    spent: dict = {}
    if not invoice_ids:
        return spent
    rows = (
        db.query(InvoiceReminder.invoice_id, InvoiceReminder.rule_id)
        .filter(InvoiceReminder.invoice_id.in_(list(invoice_ids)))
        .filter(InvoiceReminder.status == "sent")
        .all()
    )
    for inv_id, rule_id in rows:
        spent.setdefault(inv_id, set()).add(rule_id)
    return spent


def _block_reason(customer) -> str | None:
    """Why a reminder must not go to this customer, or None."""
    if customer is None or not customer.is_active:
        return "customer is inactive"
    if not customer.invoice_reminders_enabled:
        return "reminders are turned off for this customer"
    if not (customer.email or "").strip():
        return "no email address on file"
    return None


def plan_reminders(db: Session, as_of: date, invoice_ids=None):
    """(sends, skipped): what a run would email and what it would pass over
    and why. Only invoices with a rule ready to go are listed in either."""
    rules = [r for r in list_rules(db) if r.enabled]
    invoices = open_invoices(db, invoice_ids)
    spent = _spent_by_invoice(db, [i.id for i in invoices])
    sends, skipped = [], []
    for inv in invoices:
        days = (as_of - inv.due_date).days
        rule, ready = next_rule(rules, days, spent.get(inv.id, set()))
        if not ready:
            continue
        reason = _block_reason(inv.customer)
        if reason:
            skipped.append({"invoice": inv, "reason": reason})
        else:
            sends.append({"invoice": inv, "rule": rule, "days": days})
    return sends, skipped


def preview_reminders(db: Session, as_of: date, invoice_ids=None) -> dict:
    """Exactly what run_reminders would send, without sending."""
    sends, skipped = plan_reminders(db, as_of, invoice_ids)
    return {
        "as_of_date": as_of.isoformat(),
        "items": [
            {
                "invoice_id": s["invoice"].id,
                "invoice_number": s["invoice"].invoice_number,
                "customer_id": s["invoice"].customer_id,
                "customer_name": s["invoice"].customer.name,
                "recipient": s["invoice"].customer.email.strip(),
                "rule_id": s["rule"].id,
                "label": rule_label(s["rule"].days_from_due),
                "days_from_due": s["days"],
                "balance_due": float(s["invoice"].balance_due),
            }
            for s in sends
        ],
        "skipped": [
            {
                "invoice_id": k["invoice"].id,
                "invoice_number": k["invoice"].invoice_number,
                "customer_name": k["invoice"].customer.name,
                "reason": k["reason"],
            }
            for k in skipped
        ],
    }


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def _smtp_ready(settings: dict) -> bool:
    return bool((settings.get("smtp_host") or "").strip())


def _money(amount, invoice, home: str) -> str:
    code = (invoice.currency or "").strip().upper()
    text = f"{float(amount or 0):,.2f}"
    return f"{code} {text}" if code and code != home else f"${text}"


def _context(db, inv: Invoice, days: int, settings: dict, words, home: str) -> dict:
    return {
        "customer_name": inv.customer.name,
        "invoice_number": inv.invoice_number,
        "invoice_label": document_label(inv, words),
        "balance_due": _money(inv.balance_due, inv, home),
        "due_date": inv.due_date.strftime("%b %d, %Y"),
        "days_overdue": max(0, days),
        "company_name": settings.get("company_name", "") or "",
    }


def _html_body(text: str) -> str:
    """Plain text to an email body: escaped, line breaks kept."""
    return "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"


def _send_one(db, plan, settings, words, home, trigger) -> str | None:
    """Email one reminder and record it. Returns None when it went out, else
    the sentence to show. Commits either way, so a crash later in a run
    keeps what has already gone."""
    inv, rule, days = plan["invoice"], plan["rule"], plan["days"]
    customer = inv.customer
    recipient = customer.email.strip()
    label = f"{document_label(inv, words)} {inv.invoice_number}"
    error = None
    ok = False
    log_id = None
    try:
        ctx = _context(db, inv, days, settings, words, home)
        subject = " ".join(_render(rule.subject, ctx).split())
        body = _render(rule.body, ctx)
        pdf = generate_invoice_pdf(inv, settings)
        ok = send_email(
            db=db,
            to_email=recipient,
            subject=subject,
            html_body=_html_body(body),
            attachment_bytes=pdf,
            attachment_name=f"{invoice_doc_kind(inv, words)}_{inv.invoice_number}.pdf",
            entity_type="reminder",
            entity_id=inv.id,
        )
        if ok:
            log = (
                db.query(EmailLog.id)
                .filter(EmailLog.entity_type == "reminder")
                .filter(EmailLog.entity_id == inv.id)
                .order_by(EmailLog.id.desc())
                .first()
            )
            log_id = log[0] if log else None
        else:
            error = "The email could not be sent (the email log has the reason)."
    except Exception as e:
        logger.exception("Reminder for invoice %s failed", inv.id)
        db.rollback()
        error = f"The reminder could not be prepared: {e}"
    db.add(
        InvoiceReminder(
            invoice_id=inv.id,
            rule_id=rule.id,
            customer_id=customer.id,
            recipient=recipient,
            status="sent" if ok else "failed",
            trigger=trigger,
            days_from_due=days,
            balance_due=inv.balance_due,
            email_log_id=log_id,
            error=error,
        )
    )
    db.commit()
    if ok:
        return None
    return f"{customer.name}, {label}: {error}"


def run_reminders(db: Session, as_of: date, trigger: str, invoice_ids=None) -> dict:
    """Send every due reminder (see plan_reminders) and report the counts.
    Raises EmailNotSetUp when something would send and mail is not set up."""
    sends, skipped = plan_reminders(db, as_of, invoice_ids)
    settings = get_all_settings(db)
    if sends and not _smtp_ready(settings):
        raise EmailNotSetUp("reminders")
    words = terms_from_db(db)
    from app.services.currency import home_currency

    home = home_currency(db)
    sent, errors = 0, []
    for plan in sends:
        err = _send_one(db, plan, settings, words, home, trigger)
        if err:
            errors.append(err)
        else:
            sent += 1
    return {
        "sent": sent,
        "failed": len(errors),
        "skipped": len(skipped),
        "errors": errors,
        "ran_on": as_of.isoformat(),
    }


def run_if_due(db: Session, today: date) -> dict:
    """The once-a-day automatic run. The date is stamped before sending so a
    crash does not retry on every page load, but not when mail is not set up,
    so the run happens the day it is."""
    settings = get_all_settings(db)
    if settings.get("invoice_reminders_auto") != "true":
        return {"ran": False, "reason": "off"}
    if settings.get("invoice_reminders_last_run") == today.isoformat():
        return {"ran": False, "reason": "already_today"}
    sends, _ = plan_reminders(db, today)
    if sends and not _smtp_ready(settings):
        return {"ran": False, "reason": "no_smtp"}
    set_setting(db, "invoice_reminders_last_run", today.isoformat())
    db.commit()
    return {"ran": True, **run_reminders(db, today, "auto")}


def remind_invoice(db: Session, invoice_id: int, as_of: date) -> dict:
    """Send one invoice its next reminder by hand: the firmest matching rule,
    else the nearest one ahead. Raises LookupError / RemindError /
    EmailNotSetUp."""
    inv = (
        db.query(Invoice)
        .options(joinedload(Invoice.customer))
        .filter(Invoice.id == invoice_id)
        .first()
    )
    if not inv:
        raise LookupError("Invoice not found")
    if not open_invoices(db, [invoice_id]):
        raise RemindError("This invoice isn't open, so there is nothing to remind.")
    reason = _block_reason(inv.customer)
    if reason:
        raise RemindError(f"No reminder can be sent: {reason}.")
    rules = [r for r in list_rules(db) if r.enabled]
    days = (as_of - inv.due_date).days
    spent = _spent_by_invoice(db, [invoice_id]).get(invoice_id, set())
    rule, _ready = next_rule(rules, days, spent)
    if rule is None:
        raise RemindError("Every reminder rule has already been sent for this invoice.")
    settings = get_all_settings(db)
    if not _smtp_ready(settings):
        raise EmailNotSetUp("reminders")
    from app.services.currency import home_currency

    err = _send_one(
        db,
        {"invoice": inv, "rule": rule, "days": days},
        settings,
        terms_from_db(db),
        home_currency(db),
        "manual",
    )
    if err:
        raise RemindError(err)
    return {"sent": True, "label": rule_label(rule.days_from_due)}


def invoice_rows(
    db: Session, as_of: date, customer_id=None, overdue_only: bool = False
) -> dict:
    """The Collections list: every open invoice with where it stands."""
    rules = [r for r in list_rules(db) if r.enabled]
    all_rules = {r.id: r for r in list_rules(db)}
    invoices = open_invoices(db, customer_id=customer_id)
    ids = [i.id for i in invoices]
    spent = _spent_by_invoice(db, ids)
    last: dict = {}
    if ids:
        rows = (
            db.query(InvoiceReminder)
            .filter(InvoiceReminder.invoice_id.in_(ids))
            .order_by(InvoiceReminder.id)
            .all()
        )
        for row in rows:  # ascending, so the last one written wins
            last[row.invoice_id] = row
    items = []
    for inv in invoices:
        days = (as_of - inv.due_date).days
        if overdue_only and days <= 0:
            continue
        rule, ready = next_rule(rules, days, spent.get(inv.id, set()))
        row = last.get(inv.id)
        last_rule = all_rules.get(row.rule_id) if row else None
        items.append(
            {
                "invoice_id": inv.id,
                "invoice_number": inv.invoice_number,
                "customer_id": inv.customer_id,
                "customer_name": inv.customer.name,
                "customer_email": inv.customer.email,
                "date": inv.date.isoformat(),
                "due_date": inv.due_date.isoformat(),
                "days_overdue": days,
                "balance_due": float(inv.balance_due),
                "reminders_enabled": bool(inv.customer.invoice_reminders_enabled),
                "last_reminder": (
                    {
                        "rule_id": row.rule_id,
                        "label": rule_label(
                            last_rule.days_from_due if last_rule else row.days_from_due
                        ),
                        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
                        "status": row.status,
                    }
                    if row
                    else None
                ),
                "next_reminder": (
                    {
                        "rule_id": rule.id,
                        "label": rule_label(rule.days_from_due),
                        "due_on": (
                            inv.due_date + timedelta(days=rule.days_from_due)
                        ).isoformat(),
                        "ready": ready,
                    }
                    if rule
                    else None
                ),
            }
        )
    items.sort(key=lambda i: -i["days_overdue"])
    overdue = [i for i in items if i["days_overdue"] > 0]
    return {
        "as_of_date": as_of.isoformat(),
        "items": items,
        "totals": {
            "count": len(items),
            "balance_due": round(sum(i["balance_due"] for i in items), 2),
            "overdue_count": len(overdue),
            "overdue_balance": round(sum(i["balance_due"] for i in overdue), 2),
        },
    }


# ---------------------------------------------------------------------------
# Monthly statements
# ---------------------------------------------------------------------------


def _active_ids(db: Session, model, since: date, until: date, *extra) -> set:
    q = db.query(model.customer_id).filter(model.date >= since, model.date <= until)
    for cond in extra:
        q = q.filter(cond)
    return {r[0] for r in q.distinct()}


def _already_sent(db: Session, customer_id: int, as_of: date):
    """Date of this month's statement email to the customer, or None. Read
    from the email log, which every statement send already writes."""
    first = datetime.combine(as_of.replace(day=1), time.min)
    nxt = (as_of.replace(day=28) + timedelta(days=4)).replace(day=1)
    row = (
        db.query(EmailLog)
        .filter(EmailLog.entity_type == "statement")
        .filter(EmailLog.entity_id == customer_id)
        .filter(EmailLog.status == "sent")
        .filter(
            EmailLog.sent_at >= first,
            EmailLog.sent_at < datetime.combine(nxt, time.min),
        )
        .order_by(EmailLog.sent_at.desc())
        .first()
    )
    return row.sent_at.date() if row and row.sent_at else None


def statement_candidates(db: Session, as_of: date) -> list[dict]:
    """Customers on monthly statements who owe something or had activity in
    the last month, with whether this month's statement has gone."""
    since = as_of - timedelta(days=STATEMENT_ACTIVITY_DAYS)
    recent = (
        _active_ids(db, Invoice, since, as_of, Invoice.status != InvoiceStatus.VOID)
        | _active_ids(
            db,
            Payment,
            since,
            as_of,
            or_(Payment.is_voided.is_(False), Payment.is_voided.is_(None)),
        )
        | _active_ids(
            db, CreditMemo, since, as_of, CreditMemo.status != CreditMemoStatus.VOID
        )
    )
    customers = (
        db.query(Customer)
        .filter(Customer.is_active.is_(True), Customer.monthly_statement.is_(True))
        .order_by(Customer.name)
        .all()
    )
    # Customer.balance is a stored column nothing keeps up; what a customer
    # owes is summed from the open documents (contact_balances).
    owed = customer_balances(db, [c.id for c in customers])
    out = []
    for c in customers:
        balance = float(owed.get(c.id, 0))
        if balance == 0 and c.id not in recent:
            continue
        email = (c.email or "").strip()
        sent_on = _already_sent(db, c.id, as_of)
        reason = (
            "no email" if not email else "already sent this month" if sent_on else None
        )
        out.append(
            {
                "customer": c,
                "customer_id": c.id,
                "customer_name": c.name,
                "email": email or None,
                "balance": balance,
                "already_sent_on": sent_on.isoformat() if sent_on else None,
                "will_send": reason is None,
                "reason": reason,
            }
        )
    return out


def statements_due(db: Session, as_of: date) -> dict:
    keys = (
        "customer_id",
        "customer_name",
        "email",
        "balance",
        "already_sent_on",
        "will_send",
        "reason",
    )
    return {
        "as_of_date": as_of.isoformat(),
        "items": [{k: c[k] for k in keys} for c in statement_candidates(db, as_of)],
    }


def run_statements(db: Session, as_of: date, force: bool = False) -> dict:
    """Email each customer's statement PDF. `force` also resends to those who
    already had one this month; a customer with no email is never sent."""
    from app.routes.reports.receivables import statement_activity

    todo, skipped, errors = [], 0, []
    for c in statement_candidates(db, as_of):
        if c["reason"] == "no email":
            skipped += 1
            errors.append(f"{c['customer_name']}: no email address on file")
        elif c["will_send"] or force:
            todo.append(c)
        else:
            skipped += 1
    settings = get_all_settings(db)
    if todo and not _smtp_ready(settings):
        raise EmailNotSetUp("statements")
    company_name = settings.get("company_name", "") or ""
    sent = failed = 0
    for c in todo:
        customer = c["customer"]
        ok = False
        try:
            pdf = generate_statement_pdf(
                customer, statement_activity(db, customer, as_of), settings, as_of
            )
            ok = send_email(
                db=db,
                to_email=c["email"],
                subject=f"Account Statement — {company_name or 'Our Company'}",
                html_body=(
                    f"<p>Dear {html.escape(customer.name)},</p>"
                    "<p>Please find your account statement attached.</p>"
                    f"<p>{html.escape(company_name)}</p>"
                ),
                attachment_bytes=pdf,
                attachment_name=f"Statement_{customer.name}.pdf",
                entity_type="statement",
                entity_id=customer.id,
            )
        except Exception:
            logger.exception("Failed to send statement to customer %s", customer.id)
            db.rollback()
        if ok:
            sent += 1
        else:
            failed += 1
            errors.append(
                f"{customer.name}: the statement could not be sent (the email "
                "log has the reason)"
            )
    return {"sent": sent, "failed": failed, "skipped": skipped, "errors": errors}

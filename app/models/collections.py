# ============================================================================
# Collections (#653) — payment reminder rules and the audit of what was sent.
# A rule says "this many days from the due date, send this wording"; every
# send (or failed try) is one InvoiceReminder row, which is also what makes a
# rule "spent" for an invoice so it is never sent twice.
# ============================================================================

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
    true,
)

from app.database import Base


class InvoiceReminderRule(Base):
    __tablename__ = "invoice_reminder_rules"

    id = Column(Integer, primary_key=True, index=True)
    # Negative = before the due date, 0 = on it, positive = after. One rule
    # per day offset.
    days_from_due = Column(Integer, nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default=true())
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InvoiceReminder(Base):
    __tablename__ = "invoice_reminders"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    rule_id = Column(Integer, ForeignKey("invoice_reminder_rules.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    recipient = Column(String(200), nullable=False)
    status = Column(String(20), nullable=False)  # sent | failed
    trigger = Column(String(20), nullable=False)  # run | manual | auto
    # The invoice's offset from its due date when this went out (the rule's
    # own offset can be earlier than that).
    days_from_due = Column(Integer, nullable=False)
    balance_due = Column(Numeric(15, 2), nullable=False)
    email_log_id = Column(Integer, ForeignKey("email_log.id"), nullable=True)
    error = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())

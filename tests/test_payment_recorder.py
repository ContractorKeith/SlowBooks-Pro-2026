"""record_provider_payment — the shared accounting seam for all online
payment providers. GL invariants, idempotency, capping, edge cases."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment
from app.models.transactions import Transaction, TransactionLine
from app.services.accounting import get_ar_account_id, get_undeposited_funds_id
from app.services.payments.recorder import record_provider_payment


@pytest.fixture
def invoice(db_session, seed_accounts):
    customer = Customer(name="Pay Test Customer", is_active=True)
    db_session.add(customer)
    db_session.flush()
    inv = Invoice(
        invoice_number="PAY-1001",
        customer_id=customer.id,
        date=date(2026, 7, 1),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("200.00"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("200.00"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("200.00"),
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def test_full_payment_records_and_balances(db_session, invoice):
    status = record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_test_full", Decimal("200.00")
    )
    assert status == "payment_recorded"

    db_session.refresh(invoice)
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.balance_due == Decimal("0.00")
    assert invoice.amount_paid == Decimal("200.00")

    payment = db_session.query(Payment).filter_by(reference="cs_test_full").one()
    assert payment.method == "stripe"
    assert payment.amount == Decimal("200.00")
    assert payment.transaction_id is not None

    # GL invariants: DR Undeposited Funds == CR A/R == amount
    txn = db_session.get(Transaction, payment.transaction_id)
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    ar_id = get_ar_account_id(db_session)
    uf_id = get_undeposited_funds_id(db_session)
    debit_total = sum(line.debit or 0 for line in lines)
    credit_total = sum(line.credit or 0 for line in lines)
    assert debit_total == credit_total == Decimal("200.00")
    assert any(
        line.account_id == uf_id and line.debit == Decimal("200.00") for line in lines
    )
    assert any(
        line.account_id == ar_id and line.credit == Decimal("200.00") for line in lines
    )


def test_double_delivery_is_idempotent(db_session, invoice):
    first = record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_test_dup", Decimal("200.00")
    )
    second = record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_test_dup", Decimal("200.00")
    )
    assert first == "payment_recorded"
    assert second == "already_processed"
    assert db_session.query(Payment).filter_by(reference="cs_test_dup").count() == 1


def test_overpay_capped_at_balance(db_session, invoice):
    status = record_provider_payment(
        db_session, "paypal", "PayPal", invoice.id, "pp_order_over", Decimal("999.99")
    )
    assert status == "payment_recorded"
    db_session.refresh(invoice)
    assert invoice.amount_paid == Decimal("200.00")
    assert invoice.balance_due == Decimal("0.00")
    payment = db_session.query(Payment).filter_by(reference="pp_order_over").one()
    assert payment.amount == Decimal("200.00")
    assert payment.method == "paypal"


def test_partial_payment_sets_partial_status(db_session, invoice):
    status = record_provider_payment(
        db_session, "square", "Square", invoice.id, "sq_order_part", Decimal("50.00")
    )
    assert status == "payment_recorded"
    db_session.refresh(invoice)
    assert invoice.status == InvoiceStatus.PARTIAL
    assert invoice.balance_due == Decimal("150.00")


def test_settled_invoice_is_noop(db_session, invoice):
    invoice.status = InvoiceStatus.PAID
    db_session.commit()
    status = record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_test_paid", Decimal("10.00")
    )
    assert status == "invoice_already_settled"
    assert db_session.query(Payment).filter_by(reference="cs_test_paid").count() == 0


def test_void_invoice_is_noop(db_session, invoice):
    invoice.status = InvoiceStatus.VOID
    db_session.commit()
    status = record_provider_payment(
        db_session, "stripe", "Stripe", invoice.id, "cs_test_void", Decimal("10.00")
    )
    assert status == "invoice_already_settled"


def test_missing_invoice(db_session, seed_accounts):
    status = record_provider_payment(
        db_session, "stripe", "Stripe", 999999, "cs_test_missing", Decimal("10.00")
    )
    assert status == "invoice_not_found"


def test_oversize_external_id_rejected(db_session, invoice):
    with pytest.raises(ValueError):
        record_provider_payment(
            db_session, "stripe", "Stripe", invoice.id, "x" * 101, Decimal("10.00")
        )

"""Collections (#653): the monthly statement flag, who is due one, and the
batch send. WeasyPrint and SMTP are replaced where the service imports them."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.contacts import Customer
from app.models.email_log import EmailLog
from app.models.invoices import Invoice, InvoiceStatus
from app.services.settings_service import set_setting

TODAY = date.today()
NO_SMTP = (
    "Email isn't set up yet, so no statements were sent. Enter your mail "
    "server under Settings, Email, then try again."
)


@pytest.fixture
def mail(monkeypatch, db_session):
    calls = []

    def fake_send(**kw):
        calls.append(kw)
        return True

    monkeypatch.setattr("app.services.collections.send_email", fake_send)
    monkeypatch.setattr(
        "app.services.collections.generate_statement_pdf", lambda *a, **k: b"%PDF"
    )
    set_setting(db_session, "smtp_host", "smtp.test")
    db_session.commit()
    return calls


def _cust(db, name, email="x@example.com", balance=100, monthly=True, **kw):
    c = Customer(
        name=name,
        email=email,
        is_active=True,
        balance=Decimal(balance),
        monthly_statement=monthly,
        **kw,
    )
    db.add(c)
    db.commit()
    return c


def _due(client):
    return {
        i["customer_name"]: i
        for i in client.get("/api/collections/statements/due").json()["items"]
    }


def _logged(db, cust, when):
    db.add(
        EmailLog(
            entity_type="statement",
            entity_id=cust.id,
            recipient="x@example.com",
            status="sent",
            sent_at=when,
        )
    )
    db.commit()


def test_candidates_need_the_flag_and_a_balance_or_activity(client, db_session):
    _cust(db_session, "Flagged owes", balance=50)
    _cust(db_session, "Not flagged", balance=50, monthly=False)
    _cust(db_session, "Flagged zero quiet", balance=0)
    busy = _cust(db_session, "Flagged zero busy", balance=0)
    old = _cust(db_session, "Flagged zero old", balance=0)
    gone = _cust(db_session, "Inactive", balance=50)
    gone.is_active = False
    for who, days in ((busy, 10), (old, 90)):
        db_session.add(
            Invoice(
                invoice_number=f"S-{who.id}",
                customer_id=who.id,
                status=InvoiceStatus.PAID,
                date=TODAY - timedelta(days=days),
                total=10,
                balance_due=0,
            )
        )
    db_session.commit()
    got = _due(client)
    assert sorted(got) == ["Flagged owes", "Flagged zero busy"]
    row = got["Flagged owes"]
    assert row["balance"] == 50 and row["will_send"] is True
    assert row["reason"] is None and row["already_sent_on"] is None
    assert row["email"] == "x@example.com"


def test_void_invoice_is_not_activity(client, db_session):
    c = _cust(db_session, "Void only", balance=0)
    db_session.add(
        Invoice(
            invoice_number="V-1",
            customer_id=c.id,
            status=InvoiceStatus.VOID,
            date=TODAY - timedelta(days=2),
            total=10,
            balance_due=0,
        )
    )
    db_session.commit()
    assert _due(client) == {}


def test_already_sent_this_month_and_no_email(client, db_session):
    sent = _cust(db_session, "Sent")
    _logged(
        db_session,
        sent,
        datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=12),
    )
    last_month = _cust(db_session, "Sent last month")
    _logged(
        db_session,
        last_month,
        datetime.combine(TODAY.replace(day=1) - timedelta(days=3), datetime.min.time()),
    )
    _cust(db_session, "Bare", email=None)
    got = _due(client)
    assert got["Sent"]["already_sent_on"] == TODAY.isoformat()
    assert got["Sent"]["will_send"] is False
    assert got["Sent"]["reason"] == "already sent this month"
    assert got["Sent last month"]["will_send"] is True
    assert got["Bare"]["will_send"] is False and got["Bare"]["reason"] == "no email"


def test_run_sends_and_skips(client, db_session, mail):
    _cust(db_session, "Ready", email="ready@example.com")
    sent = _cust(db_session, "Done")
    _logged(
        db_session,
        sent,
        datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=12),
    )
    _cust(db_session, "Bare", email=None)
    r = client.post("/api/collections/statements/run", json={}).json()
    assert (r["sent"], r["failed"], r["skipped"]) == (1, 0, 2)
    assert r["errors"] == ["Bare: no email address on file"]
    assert [c["to_email"] for c in mail] == ["ready@example.com"]
    assert mail[0]["entity_type"] == "statement"
    assert mail[0]["attachment_bytes"] == b"%PDF"


def test_force_resends_to_already_sent(client, db_session, mail):
    done = _cust(db_session, "Done")
    _logged(
        db_session,
        done,
        datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=12),
    )
    r = client.post("/api/collections/statements/run", json={"force": True}).json()
    assert r["sent"] == 1 and r["skipped"] == 0
    assert len(mail) == 1


def test_failed_send_is_counted(client, db_session, mail, monkeypatch):
    _cust(db_session, "Unlucky")
    monkeypatch.setattr("app.services.collections.send_email", lambda **kw: False)
    r = client.post("/api/collections/statements/run", json={}).json()
    assert (r["sent"], r["failed"]) == (0, 1)
    assert "Unlucky" in r["errors"][0]


def test_run_needs_smtp_only_when_something_sends(client, db_session):
    assert client.post("/api/collections/statements/run", json={}).json()["sent"] == 0
    _cust(db_session, "Waiting")
    r = client.post("/api/collections/statements/run", json={})
    assert r.status_code == 400 and r.json()["detail"] == NO_SMTP
    # only a customer with no email: nothing would send, so no 400
    db_session.query(Customer).update({"email": None})
    db_session.commit()
    assert client.post("/api/collections/statements/run", json={}).status_code == 200

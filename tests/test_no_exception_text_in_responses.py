"""CodeQL py/stack-trace-exposure (alerts 39, 45, 46, 47, 57): a route that
catches a bare Exception must not hand its text to the browser. The text
goes to the server log; the response says only that it failed."""

from tests.test_qb_report_import import FIXTURE

SECRET = "RuntimeError: /srv/secret/path.db is locked by pid 4242"


def _boom(*a, **kw):
    raise RuntimeError(SECRET)


def test_qbo_import_and_export_do_not_echo_exception_text(client, monkeypatch, caplog):
    from app.routes import qbo as route
    from app.services import qbo_export, qbo_import, qbo_service

    monkeypatch.setattr(qbo_service, "is_connected", lambda db: True)
    monkeypatch.setattr(qbo_import, "import_all", _boom)
    monkeypatch.setattr(qbo_export, "export_all", _boom)
    monkeypatch.setitem(route._IMPORT_ENTITY_MAP, "customers", _boom)
    with caplog.at_level("ERROR"):
        for path in ("/api/qbo/import", "/api/qbo/import/customers", "/api/qbo/export"):
            r = client.post(path)
            assert r.status_code == 500, (path, r.text)
            assert "secret" not in r.text and "4242" not in r.text, (path, r.text)
            assert "server log" in r.text, (path, r.text)
    assert "secret/path.db" in caplog.text  # logged, not served


def test_iif_import_does_not_echo_exception_text(client, monkeypatch, caplog):
    from app.routes import iif as route

    monkeypatch.setattr(route, "import_all", _boom)
    with caplog.at_level("ERROR"):
        r = client.post(
            "/api/iif/import", files={"file": ("x.iif", b"!HDR\n", "text/plain")}
        )
    assert (
        r.status_code == 500 and "secret" not in r.text and "server log" in r.text
    ), r.text
    assert "secret/path.db" in caplog.text


def test_qb_report_import_row_errors_keep_data_messages_but_not_crashes(
    client, seed_accounts, monkeypatch, caplog
):
    from app.services import qb_report_import as svc

    monkeypatch.setattr(svc, "Invoice", _boom)  # every receipt row now crashes
    with caplog.at_level("ERROR"):
        r = client.post(
            "/api/csv/import/qb-report",
            files={"file": ("receipts.csv", FIXTURE.read_bytes(), "text/csv")},
        )
    assert r.status_code == 200, r.text
    errors = r.json()["errors"]
    assert errors and all(
        "unexpected error" in e and "secret" not in e for e in errors
    ), errors
    # logged with its traceback (the message text is mangled by SQLAlchemy's
    # lambda wrapper in this seam, so assert on the shape, not the words)
    assert "Traceback" in caplog.text and "RuntimeError" in caplog.text


def test_donor_preview_reason_is_a_fixed_phrase_not_exception_text(
    client, seed_accounts, seed_customer
):
    """The one flagged site whose message was ours all along: it now comes
    off a typed reason, so the phrase still reaches the user."""
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 40, "line_order": 0}],
        },
    )
    assert r.status_code == 201, r.text
    p = client.get(f"/api/donors/gifts/invoice/{r.json()['id']}/acknowledgment/preview")
    assert p.status_code == 200 and p.json() == {
        "eligible": False,
        "amount": None,
        "reason": "Acknowledge the payment, not the pledge",
    }, p.text
    pdf = client.get(f"/api/donors/gifts/invoice/{r.json()['id']}/acknowledgment/pdf")
    assert (
        pdf.status_code == 400
        and pdf.json()["detail"] == "Acknowledge the payment, not the pledge"
    )


# ---------------------------------------------------------------------------
# macbase1, 2.9.4 gate (HIGH): the IIF importer catches per ROW and answered
# 200 with the whole INSERT statement and every bound parameter — a live
# payment_token included — for an ordinary QuickBooks export missing its
# document number. The route-level handler never fires.
# ---------------------------------------------------------------------------

INVOICE_IIF_NO_DOCNUM = (
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tDUEDATE\tTERMS\tMEMO\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO\n"
    "!ENDTRNS\n"
    "TRNS\tINVOICE\t01/02/2026\tAccounts Receivable\tGate Customer\t1234.56\t\t01/02/2026\tNet 30\tno docnum\n"
    "SPL\tINVOICE\t01/02/2026\tSales\tGate Customer\t-1234.56\t\tline\n"
    "ENDTRNS\n"
)

SQL_MARKERS = (
    "INSERT INTO",
    "[SQL:",
    "[parameters:",
    "payment_token",
    "VALUES (",
    "Traceback",
)


def test_iif_row_errors_carry_the_constraint_not_the_statement(
    client, db_session, seed_accounts
):
    from app.models.contacts import Customer

    db_session.add(Customer(name="Gate Customer", is_active=True))
    db_session.commit()
    r = client.post(
        "/api/iif/import",
        files={"file": ("invoices.iif", INVOICE_IIF_NO_DOCNUM.encode(), "text/plain")},
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert not any(m in body for m in SQL_MARKERS), body[:600]
    errors = r.json().get("errors") or []
    # the row is still reported, and the constraint is named — that is the
    # part a user can act on
    assert errors, r.text
    joined = " ".join(
        e.get("message", "") if isinstance(e, dict) else str(e) for e in errors
    )
    assert (
        "constraint" in joined.lower()
        or "invoice_number" in joined
        or "document" in joined.lower()
    ), joined


def test_safe_message_shapes(caplog):
    from sqlalchemy.exc import IntegrityError

    from app.services.safe_errors import GENERIC, safe_message

    class _Orig(Exception):
        pass

    ie = IntegrityError(
        "INSERT INTO invoices (...) VALUES (?, ?)",
        ("tok-secret", 1),
        _Orig("NOT NULL constraint failed: invoices.invoice_number"),
    )
    try:
        raise ie
    except IntegrityError as exc:
        msg = safe_message(exc, "test")
    assert (
        msg
        == "Database constraint: NOT NULL constraint failed: invoices.invoice_number"
    )
    assert "tok-secret" not in msg and "INSERT" not in msg
    assert (
        safe_message(ValueError("Row 3: amount is not a number"), "test")
        == "Row 3: amount is not a number"
    )
    with caplog.at_level("ERROR"):
        try:
            raise RuntimeError(SECRET)
        except RuntimeError as exc:
            msg = safe_message(exc, "test")
    assert msg == GENERIC and "secret" not in msg
    assert "secret/path.db" in caplog.text and "Traceback" in caplog.text


def test_qbo_auth_url_and_export_entity_and_test_email_do_not_echo(client, monkeypatch):
    from app.routes import qbo as route
    from app.services import qbo_service

    monkeypatch.setattr(qbo_service, "get_auth_url", _boom)
    r = (
        client.get("/api/qbo/auth-url")
        if any(
            getattr(x, "path", "") == "/api/qbo/auth-url" for x in route.router.routes
        )
        else None
    )
    if r is not None:
        assert r.status_code == 400 and "secret" not in r.text, r.text
    monkeypatch.setattr(qbo_service, "is_connected", lambda db: True)
    monkeypatch.setitem(route._EXPORT_ENTITY_MAP, "customers", _boom)
    r = client.post("/api/qbo/export/customers")
    assert (
        r.status_code == 500 and "secret" not in r.text and "server log" in r.text
    ), r.text
    from app.routes import settings as settings_route

    monkeypatch.setattr(settings_route, "send_email", _boom, raising=False)
    r = client.post("/api/settings/test-email")
    assert r.status_code in (400, 500, 502) and "secret" not in r.text, r.text


def test_python_errors_are_bugs_not_user_messages(caplog):
    """macbase1, 2.9.4 round 2: KeyError / IndexError / ZeroDivisionError are
    what Python raises when the code is wrong; they must be logged as bugs
    and answered generically, never passed through as "'ACCNT'"."""
    from decimal import Decimal, InvalidOperation

    from app.services.safe_errors import GENERIC, safe_message

    cases = []
    for thrower in (lambda: {}["ACCNT"], lambda: [][3], lambda: 1 / 0):
        try:
            thrower()
        except Exception as exc:
            with caplog.at_level("ERROR"):
                cases.append(safe_message(exc, "test"))
    assert cases == [GENERIC, GENERIC, GENERIC]
    assert caplog.text.count("Traceback") >= 3
    try:
        Decimal("abc")
    except InvalidOperation as exc:
        assert safe_message(exc, "test") == "a number could not be read"

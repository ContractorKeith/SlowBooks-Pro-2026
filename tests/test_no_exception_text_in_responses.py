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

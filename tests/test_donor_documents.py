"""Donor documents: the donation receipt and pledge faces on the invoice
PDF, the IRS acknowledgment block, the donor fields on a customer, and
the recurring-template link on generated pledges."""

from decimal import Decimal

from app.services.donor_documents import irs_statement


def _nonprofit(client):
    assert (
        client.put("/api/settings", json={"company_type": "nonprofit"}).status_code
        == 200
    )
    client.post("/api/nonprofit/setup-accounts")


def _receipt(client, customer_id, **over):
    body = {
        "customer_id": customer_id,
        "date": "2026-05-09",
        "tax_rate": "0",
        "method": "Credit Card",
        "lines": [
            {"description": "Spring gala ticket", "quantity": 1, "rate": "150.00"}
        ],
    }
    body.update(over)
    return client.post("/api/sales-receipts", json=body)


# ---------------------------------------------------------------------------
# The IRS language, in one place
# ---------------------------------------------------------------------------


def test_irs_statement_variants():
    co = {"company_name": "Riverbend Community Arts", "company_tax_id": "12-3456789"}
    pure = irs_statement(co, 100)
    assert pure["deductible_amount"] == 100.0
    assert "No goods or services were provided" in pure["text"]
    assert "EIN 12-3456789" in pure["text"]

    quid = irs_statement(co, 150, 45, "gala dinner")
    assert quid["deductible_amount"] == 105.0
    assert "$45.00 (gala dinner)" in quid["text"]
    assert "limited to $105.00" in quid["text"]
    assert "No goods or services" not in quid["text"]

    prop = irs_statement(co, None, in_kind_descriptions=["Yamaha U1 upright piano"])
    assert prop["deductible_amount"] is None
    assert "Yamaha U1 upright piano" in prop["text"]
    assert "has not assigned a value" in prop["text"]
    assert "$" not in prop["text"]


# ---------------------------------------------------------------------------
# Donation receipt face
# ---------------------------------------------------------------------------


def test_receipt_prints_as_donation_receipt_with_deductible_portion(
    client, seed_accounts, seed_customer
):
    _nonprofit(client)
    r = _receipt(
        client,
        seed_customer.id,
        fair_value_amount="45",
        fair_value_description="gala dinner",
    )
    assert r.status_code in (200, 201), r.text
    inv = r.json()["invoice"]
    assert Decimal(inv["fair_value_amount"]) == Decimal("45")
    assert inv["fair_value_description"] == "gala dinner"

    html = client.get(f"/api/invoices/{inv['id']}/print-preview").text
    assert "DONATION RECEIPT" in html
    assert "SALES RECEIPT" not in html and "Sold To" not in html
    assert ">Donor<" in html
    assert "$45.00" in html and "gala dinner" in html
    assert "$105.00" in html  # the deductible portion
    assert "No goods or services" not in html
    assert "Received" in html and "Contribution" in html

    pdf = client.get(f"/api/invoices/{inv['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    assert (
        f"DonationReceipt_{inv['invoice_number']}.pdf"
        in pdf.headers["content-disposition"]
    )


def test_pure_gift_receipt_states_no_goods_or_services(
    client, seed_accounts, seed_customer
):
    _nonprofit(client)
    inv = _receipt(client, seed_customer.id).json()["invoice"]
    html = client.get(f"/api/invoices/{inv['id']}/print-preview").text
    assert "No goods or services were provided" in html
    assert "Deductible portion" not in html


def test_fair_value_is_validated_against_the_total(
    client, seed_accounts, seed_customer
):
    _nonprofit(client)
    assert (
        _receipt(client, seed_customer.id, fair_value_amount="151").status_code == 400
    )
    assert _receipt(client, seed_customer.id, fair_value_amount="-1").status_code == 400
    inv = _receipt(client, seed_customer.id, fair_value_amount="20").json()["invoice"]
    r = client.put(f"/api/invoices/{inv['id']}", json={"fair_value_amount": "999"})
    assert r.status_code == 400


def test_business_mode_receipt_is_untouched(client, seed_accounts, seed_customer):
    inv = _receipt(client, seed_customer.id, fair_value_amount="45").json()["invoice"]
    html = client.get(f"/api/invoices/{inv['id']}/print-preview").text
    assert "SALES RECEIPT" in html and "DONATION" not in html
    assert "Sold To" in html and "Acknowledgment" not in html
    pdf = client.get(f"/api/invoices/{inv['id']}/pdf")
    assert (
        f"SalesReceipt_{inv['invoice_number']}.pdf"
        in pdf.headers["content-disposition"]
    )


# ---------------------------------------------------------------------------
# Pledge face
# ---------------------------------------------------------------------------


def test_pledge_and_invoice_faces(client, seed_accounts, seed_customer):
    _nonprofit(client)
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-01-01",
        "terms": "Net 30",
        "tax_rate": "0",
        "lines": [{"description": "Monthly pledge", "quantity": 1, "rate": "100"}],
    }
    pledge = client.post("/api/invoices", json={**body, "is_pledge": True}).json()
    assert pledge["is_pledge"] is True
    html = client.get(f"/api/invoices/{pledge['id']}/print-preview").text
    assert (
        ">PLEDGE<" in html
        and "Pledge Amount" in html
        and "deductible when paid" in html
    )
    assert "Terms:" not in html
    pdf = client.get(f"/api/invoices/{pledge['id']}/pdf")
    assert (
        f"Pledge_{pledge['invoice_number']}.pdf" in pdf.headers["content-disposition"]
    )

    # a program fee is still an invoice for a nonprofit
    fee = client.post("/api/invoices", json={**body, "is_pledge": False}).json()
    html = client.get(f"/api/invoices/{fee['id']}/print-preview").text
    assert ">INVOICE<" in html and "PLEDGE" not in html
    assert (
        f"Invoice_{fee['invoice_number']}.pdf"
        in client.get(f"/api/invoices/{fee['id']}/pdf").headers["content-disposition"]
    )

    dup = client.post(f"/api/invoices/{pledge['id']}/duplicate").json()
    assert dup["is_pledge"] is True


# ---------------------------------------------------------------------------
# Donor record
# ---------------------------------------------------------------------------


def test_customer_donor_fields_round_trip(client):
    r = client.post(
        "/api/customers",
        json={
            "name": "Maria Okafor",
            "donor_type": "individual",
            "salutation": "Dear Maria",
            "send_year_end_statement": False,
        },
    )
    assert r.status_code == 201, r.text
    c = r.json()
    assert (c["donor_type"], c["salutation"], c["send_year_end_statement"]) == (
        "individual",
        "Dear Maria",
        False,
    )
    plain = client.post("/api/customers", json={"name": "Plain Co"}).json()
    assert plain["donor_type"] is None and plain["send_year_end_statement"] is True
    r = client.put(
        f"/api/customers/{c['id']}",
        json={"send_year_end_statement": True, "donor_type": None},
    )
    assert r.status_code == 200 and r.json()["send_year_end_statement"] is True
    assert r.json()["donor_type"] is None


# ---------------------------------------------------------------------------
# Recurring pledges remember their template (and their grant)
# ---------------------------------------------------------------------------


def test_generated_invoices_link_to_template_and_carry_job(
    client, seed_accounts, seed_customer
):
    _nonprofit(client)
    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Capital Campaign"}
    ).json()
    rec = client.post(
        "/api/recurring",
        json={
            "customer_id": seed_customer.id,
            "frequency": "monthly",
            "start_date": "2026-01-01",
            "job_id": job["id"],
            "lines": [{"description": "Monthly pledge", "quantity": 1, "rate": "100"}],
        },
    )
    assert rec.status_code in (200, 201), rec.text
    r = client.post("/api/recurring/generate?as_of=2026-02-01")
    assert r.status_code in (200, 201), r.text
    invoices = client.get("/api/invoices?is_sales_receipt=false").json()
    generated = [i for i in invoices if i["recurring_invoice_id"] == rec.json()["id"]]
    assert len(generated) >= 1, invoices
    for inv in generated:
        assert inv["is_pledge"] is True
        assert inv["job_id"] == job["id"]

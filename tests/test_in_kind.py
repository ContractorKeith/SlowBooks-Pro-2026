"""In-kind gifts: two-sided posting tagged to the fund, void symmetry,
account validation, closing date, and an acknowledgment that describes
the property without a dollar figure."""

from decimal import Decimal

from app.models.accounts import Account
from app.models.transactions import Transaction, TransactionLine


def _nonprofit(client):
    client.put("/api/settings", json={"company_type": "nonprofit"})
    client.post("/api/nonprofit/setup-accounts")


def _piano(client, customer_id, **over):
    body = {
        "customer_id": customer_id,
        "date": "2026-04-20",
        "memo": "For the youth program",
        "lines": [
            {
                "description": "Yamaha U1 upright piano",
                "quantity": 1,
                "fair_value": "6500",
                "debit_account_id": over.pop("debit_account_id"),
            }
        ],
    }
    body.update(over)
    return client.post("/api/in-kind-gifts", json=body)


def test_in_kind_gift_posts_two_sided_and_voids(
    client, db_session, seed_accounts, seed_customer
):
    _nonprofit(client)
    fund = client.post(
        "/api/classes", json={"name": "Music Programs", "default_function": "program"}
    ).json()
    asset = client.post(
        "/api/accounts",
        json={
            "name": "Musical Instruments",
            "account_number": "1550",
            "account_type": "asset",
        },
    ).json()
    r = _piano(
        client, seed_customer.id, debit_account_id=asset["id"], class_id=fund["id"]
    )
    assert r.status_code == 201, r.text
    g = r.json()
    assert g["number"].startswith("IK-") and Decimal(g["total"]) == Decimal("6500")
    assert g["lines"][0]["credit_account_name"] == "In-Kind Contributions"
    assert g["class_name"] == "Music Programs" and g["customer_name"] == "Test Customer"

    income = db_session.query(Account).filter_by(name="In-Kind Contributions").one()
    txn = db_session.get(Transaction, g["transaction_id"])
    assert txn.source_type == "in_kind_gift"
    by = {ln.account_id: ln for ln in txn.lines}
    assert by[asset["id"]].debit == Decimal("6500") and by[income.id].credit == Decimal(
        "6500"
    )
    assert all(ln.class_id == fund["id"] for ln in txn.lines)
    assert by[asset["id"]].function == "program"

    # the gift shows on the Statement of Activities as revenue in the fund
    soa = client.get(
        "/api/reports/statement-of-activities?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    assert soa["totals"]["revenue"] == 6500.0
    byc = client.get(
        "/api/reports/profit-loss-by-class?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    assert {c["class_name"]: c["income"] for c in byc["classes"]}[
        "Music Programs"
    ] == 6500.0

    # acknowledgment: describes the piano, never a dollar figure
    pdf = client.get(f"/api/donors/gifts/in-kind/{g['id']}/acknowledgment/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    assert f"Acknowledgment_{g['number']}.pdf" in pdf.headers["content-disposition"]
    from app.services.donor_documents import gift_irs, load_gift

    gift = load_gift(db_session, "in-kind", g["id"])
    text = gift_irs({"company_name": "Riverbend"}, gift)["text"]
    assert "Yamaha U1 upright piano" in text and "$" not in text

    # void: reversing entry, same tags, ledger balanced
    r = client.post(f"/api/in-kind-gifts/{g['id']}/void")
    assert r.status_code == 200 and r.json()["status"] == "void"
    rev = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "in_kind_gift_void",
            Transaction.source_id == g["id"],
        )
        .one()
    )
    assert {ln.account_id: ln.credit for ln in rev.lines}[asset["id"]] == Decimal(
        "6500"
    )
    assert all(ln.class_id == fund["id"] for ln in rev.lines)
    dr = sum(Decimal(str(x[0])) for x in db_session.query(TransactionLine.debit).all())
    cr = sum(Decimal(str(x[0])) for x in db_session.query(TransactionLine.credit).all())
    assert dr == cr
    assert client.post(f"/api/in-kind-gifts/{g['id']}/void").status_code == 400
    assert (
        client.get(
            f"/api/donors/gifts/in-kind/{g['id']}/acknowledgment/pdf"
        ).status_code
        == 400
    )
    assert [x["status"] for x in client.get("/api/in-kind-gifts").json()] == ["void"]


def test_in_kind_validation_and_closing_date(client, seed_accounts, seed_customer):
    _nonprofit(client)
    income = seed_accounts["4000"].id
    checking = seed_accounts["1010"].id
    # the debit side must be an asset or expense
    r = _piano(client, seed_customer.id, debit_account_id=income)
    assert r.status_code == 422
    assert (
        client.post(
            "/api/in-kind-gifts",
            json={"customer_id": seed_customer.id, "date": "2026-04-20", "lines": []},
        ).status_code
        == 422
    )
    assert _piano(client, 99999, debit_account_id=checking).status_code == 404
    # a gift with no stated value is a document without a posting
    r = client.post(
        "/api/in-kind-gifts",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "lines": [
                {
                    "description": "Volunteer legal review",
                    "quantity": 1,
                    "fair_value": "0",
                    "debit_account_id": checking,
                }
            ],
        },
    )
    assert r.status_code == 201 and r.json()["transaction_id"] is None
    client.put("/api/settings", json={"closing_date": "2026-06-30"})
    assert (
        _piano(client, seed_customer.id, debit_account_id=checking).status_code == 403
    )

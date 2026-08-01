"""Fixed assets: depreciation math + journals, disposal gain/loss,
CSV import, reconciliation report."""

from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine


@pytest.fixture
def fa_accounts(db_session, seed_accounts):
    asset = Account(name="Computer Equipment (Asset)", account_type=AccountType.ASSET)
    accum = Account(name="Accum. Depr. — Computers", account_type=AccountType.ASSET)
    expense = Account(name="Depreciation Expense", account_type=AccountType.EXPENSE)
    db_session.add_all([asset, accum, expense])
    db_session.commit()
    return asset, accum, expense


@pytest.fixture
def computer_type(client, fa_accounts):
    asset, accum, expense = fa_accounts
    resp = client.post(
        "/api/fixed-assets/types",
        json={
            "name": "Computers",
            "depreciation_method": "straight_line",
            "effective_life_years": 5,
            "asset_account_id": asset.id,
            "accumulated_depreciation_account_id": accum.id,
            "depreciation_expense_account_id": expense.id,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def laptop(client, computer_type):
    resp = client.post(
        "/api/fixed-assets",
        json={
            "name": "Dev laptop",
            "asset_type_id": computer_type["id"],
            "purchase_date": "2026-01-15",
            "purchase_price": 3000,
            "salvage_value": 600,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_asset_gets_number_and_derived_book_value(laptop):
    assert laptop["asset_number"] == "FA-0001"
    assert laptop["book_value"] == 3000.0


def test_straight_line_depreciation_run(client, db_session, laptop, fa_accounts):
    _, accum, expense = fa_accounts
    # 2026-01-15 → 2026-07-15 = 6 full months; (3000-600)/60 = 40/mo → 240
    resp = client.post(
        "/api/fixed-assets/run-depreciation", json={"run_date": "2026-07-15"}
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["posted"] == 1
    assert result["total"] == 240.0

    asset = client.get(f"/api/fixed-assets/{laptop['id']}").json()
    assert asset["accumulated_depreciation"] == 240.0
    assert asset["book_value"] == 2760.0

    txn = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "depreciation")
        .one()
    )
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    assert any(
        ln.account_id == expense.id and ln.debit == Decimal("240.00") for ln in lines
    )
    assert any(
        ln.account_id == accum.id and ln.credit == Decimal("240.00") for ln in lines
    )

    # Re-run same date: nothing further to post
    again = client.post(
        "/api/fixed-assets/run-depreciation", json={"run_date": "2026-07-15"}
    ).json()
    assert again["posted"] == 0


def test_depreciation_never_breaches_salvage(client, laptop):
    resp = client.post(
        "/api/fixed-assets/run-depreciation", json={"run_date": "2099-12-31"}
    ).json()
    assert resp["total"] == 2400.0  # cost - salvage, capped
    asset = client.get(f"/api/fixed-assets/{laptop['id']}").json()
    assert asset["book_value"] == 600.0  # never below salvage


def test_disposal_posts_gain(client, db_session, laptop, fa_accounts):
    asset_acct, accum, _ = fa_accounts
    client.post("/api/fixed-assets/run-depreciation", json={"run_date": "2026-07-15"})

    bank = db_session.query(Account).filter(Account.name == "Checking").first()
    if not bank:
        bank = Account(name="Checking FA", account_type=AccountType.ASSET)
        db_session.add(bank)
        db_session.commit()

    # book value 2760 after 240 depreciation; sell for 3000 → 240 gain
    resp = client.post(
        f"/api/fixed-assets/{laptop['id']}/dispose",
        json={
            "disposal_date": "2026-07-20",
            "proceeds": 3000,
            "deposit_account_id": bank.id,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["gain_loss"] == 240.0

    txn = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "asset_disposal")
        .one()
    )
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    assert sum(ln.debit or 0 for ln in lines) == sum(ln.credit or 0 for ln in lines)
    # cost fully derecognized
    assert any(
        ln.account_id == asset_acct.id and ln.credit == Decimal("3000.00")
        for ln in lines
    )

    asset = client.get(f"/api/fixed-assets/{laptop['id']}").json()
    assert asset["status"] == "disposed"
    # disposed assets can't be edited or double-disposed
    assert (
        client.put(f"/api/fixed-assets/{laptop['id']}", json={"name": "x"}).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/fixed-assets/{laptop['id']}/dispose",
            json={
                "disposal_date": "2026-07-21",
                "proceeds": 1,
                "deposit_account_id": bank.id,
            },
        ).status_code
        == 400
    )


def test_csv_import(client, computer_type):
    csv_text = (
        "name,asset_type,purchase_date,purchase_price,salvage_value,description\n"
        "Monitor,Computers,2026-02-01,500,0,27 inch\n"
        "Desk,No Such Type,2026-02-01,900,0,\n"
    )
    import io

    resp = client.post(
        "/api/fixed-assets/import-csv",
        files={"file": ("assets.csv", io.BytesIO(csv_text.encode()), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["imported"] == 1
    assert len(data["errors"]) == 1
    assert "No Such Type" in data["errors"][0]["message"]


def test_reconciliation_report(client, laptop):
    client.post("/api/fixed-assets/run-depreciation", json={"run_date": "2026-07-15"})
    data = client.get("/api/fixed-assets/reports/reconciliation").json()
    row = next(t for t in data["types"] if t["asset_type"] == "Computers")
    assert row["asset_count"] == 1
    assert row["cost"] == 3000.0
    assert row["accumulated_depreciation"] == 240.0
    assert row["book_value"] == 2760.0

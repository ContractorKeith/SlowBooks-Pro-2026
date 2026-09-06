"""Nonprofit mode — the ledger side.

Funds are classes with a restriction and a default function; posted lines
carry a function (program / management / fundraising) defaulted from the
class; every by-class report groups on the line's class first. The
reconciliation promise is the same one P&L by Class makes: the fund and
function views always add up to the plain Profit & Loss."""

from datetime import date
from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine
from app.services.accounting import create_journal_entry


def _accts(db_session):
    income = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.INCOME)
        .first()
    )
    expense = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.EXPENSE)
        .first()
    )
    return income, expense


# ---------------------------------------------------------------------------
# Classes as funds
# ---------------------------------------------------------------------------


def test_class_restriction_and_function_fields_round_trip(client):
    r = client.post(
        "/api/classes",
        json={
            "name": "Youth Program",
            "restriction": "temporarily_restricted",
            "default_function": "program",
            "donor_name": "Riverbend County Community Foundation",
            "purpose": "After-school music instruction",
        },
    )
    assert r.status_code == 201, r.text
    cls = r.json()
    assert cls["restriction"] == "temporarily_restricted"
    assert cls["default_function"] == "program"
    assert cls["donor_name"].startswith("Riverbend")

    plain = client.post("/api/classes", json={"name": "General"}).json()
    assert plain["restriction"] == "unrestricted"
    assert plain["default_function"] is None

    r = client.put(f"/api/classes/{cls['id']}", json={"default_function": None})
    assert r.status_code == 200 and r.json()["default_function"] is None
    r = client.put(f"/api/classes/{cls['id']}", json={"restriction": "banana"})
    assert r.status_code == 422
    r = client.post(
        "/api/classes", json={"name": "Bad", "default_function": "overhead"}
    )
    assert r.status_code == 422

    listed = {c["name"]: c for c in client.get("/api/classes").json()}
    assert listed["Youth Program"]["restriction"] == "temporarily_restricted"
    uncat = listed["Uncategorized"]
    r = client.put(
        f"/api/classes/{uncat['id']}", json={"restriction": "permanently_restricted"}
    )
    assert r.status_code == 400  # the untagged bucket stays unrestricted
    r = client.put(
        f"/api/classes/{uncat['id']}", json={"default_function": "management"}
    )
    assert r.status_code == 200 and r.json()["default_function"] == "management"


# ---------------------------------------------------------------------------
# The function dimension on posted lines
# ---------------------------------------------------------------------------


def test_function_defaults_from_class_and_explicit_none_wins(
    client, db_session, seed_accounts
):
    program = client.post(
        "/api/classes", json={"name": "Programs", "default_function": "program"}
    ).json()
    income, expense = _accts(db_session)

    txn = create_journal_entry(
        db_session,
        date(2026, 5, 1),
        "function defaulting",
        [
            # class only -> function from the class
            {
                "account_id": expense.id,
                "debit": Decimal("100"),
                "credit": Decimal("0"),
                "class_id": program["id"],
            },
            # explicit function wins over the class default
            {
                "account_id": expense.id,
                "debit": Decimal("50"),
                "credit": Decimal("0"),
                "class_id": program["id"],
                "function": "fundraising",
            },
            # explicit None stays NULL even though the class has a default
            {
                "account_id": expense.id,
                "debit": Decimal("25"),
                "credit": Decimal("0"),
                "class_id": program["id"],
                "function": None,
            },
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("175")},
        ],
    )
    db_session.commit()
    lines = sorted(
        db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all(),
        key=lambda ln: ln.id,
    )
    assert [ln.function for ln in lines] == ["program", "fundraising", None, None]

    # header class inherits too
    txn2 = create_journal_entry(
        db_session,
        date(2026, 5, 2),
        "header class",
        [
            {"account_id": expense.id, "debit": Decimal("10"), "credit": Decimal("0")},
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("10")},
        ],
        class_id=program["id"],
    )
    db_session.commit()
    assert {
        ln.function
        for ln in db_session.query(TransactionLine).filter_by(transaction_id=txn2.id)
    } == {"program"}


def test_journal_and_bill_lines_accept_function_and_voids_carry_it(
    client, db_session, seed_accounts
):
    fund = client.post("/api/classes", json={"name": "Gala"}).json()
    income, expense = _accts(db_session)
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-06-01",
            "description": "gala costs",
            "lines": [
                {
                    "account_id": expense.id,
                    "debit": "300",
                    "class_id": fund["id"],
                    "function": "fundraising",
                },
                {"account_id": income.id, "credit": "300"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    je = r.json()
    posted = {ln["account_id"]: ln for ln in je["lines"]}
    assert posted[expense.id]["function"] == "fundraising"
    assert posted[expense.id]["class_id"] == fund["id"]

    r = client.post(f"/api/journal/{je['id']}/void")
    assert r.status_code == 200, r.text
    void_txn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "manual_void", Transaction.source_id == je["id"]
        )
        .one()
    )
    reversed_expense = [ln for ln in void_txn.lines if ln.account_id == expense.id][0]
    assert reversed_expense.credit == Decimal("300")
    assert reversed_expense.class_id == fund["id"]
    assert reversed_expense.function == "fundraising"

    # After the void the fund nets to zero on P&L by Class
    data = client.get(
        "/api/reports/profit-loss-by-class?start_date=2026-06-01&end_date=2026-06-30"
    ).json()
    by_name = {c["class_name"]: c for c in data["classes"]}
    assert by_name["Gala"]["expenses"] == 0.0


# ---------------------------------------------------------------------------
# P&L by Class groups on the line's class
# ---------------------------------------------------------------------------


def test_profit_loss_by_class_uses_line_class(client, db_session, seed_accounts):
    a = client.post("/api/classes", json={"name": "Fund A"}).json()
    b = client.post("/api/classes", json={"name": "Fund B"}).json()
    income, expense = _accts(db_session)

    # header-less entry, each expense line tagged to a different fund
    create_journal_entry(
        db_session,
        date(2026, 8, 10),
        "split rent",
        [
            {
                "account_id": expense.id,
                "debit": Decimal("70"),
                "credit": Decimal("0"),
                "class_id": a["id"],
            },
            {
                "account_id": expense.id,
                "debit": Decimal("30"),
                "credit": Decimal("0"),
                "class_id": b["id"],
            },
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("100")},
        ],
    )
    # header class with one line overriding it
    create_journal_entry(
        db_session,
        date(2026, 8, 11),
        "mostly A",
        [
            {"account_id": expense.id, "debit": Decimal("20"), "credit": Decimal("0")},
            {
                "account_id": expense.id,
                "debit": Decimal("5"),
                "credit": Decimal("0"),
                "class_id": b["id"],
            },
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("25")},
        ],
        class_id=a["id"],
    )
    db_session.commit()

    data = client.get(
        "/api/reports/profit-loss-by-class?start_date=2026-08-01&end_date=2026-08-31"
    ).json()
    by_name = {c["class_name"]: c for c in data["classes"]}
    assert by_name["Fund A"]["expenses"] == 90.0  # 70 + 20
    assert by_name["Fund B"]["expenses"] == 35.0  # 30 + 5
    assert by_name["Uncategorized"]["income"] == 100.0  # header-less credit
    assert by_name["Fund A"]["income"] == 25.0

    plain = client.get(
        "/api/reports/profit-loss?start_date=2026-08-01&end_date=2026-08-31"
    ).json()
    assert abs(data["total_income"] - plain["total_income"]) < 0.01
    assert abs(data["total_expenses"] - plain["total_expenses"]) < 0.01
    assert abs(data["total_net_income"] - plain["net_income"]) < 0.01

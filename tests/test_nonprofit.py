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


# ---------------------------------------------------------------------------
# Release from restriction
# ---------------------------------------------------------------------------


def _ledger_balanced(db_session) -> bool:
    dr = db_session.query(TransactionLine).with_entities(TransactionLine.debit).all()
    cr = db_session.query(TransactionLine).with_entities(TransactionLine.credit).all()
    return sum(Decimal(str(d[0])) for d in dr) == sum(Decimal(str(c[0])) for c in cr)


def _restricted_fund(client, name="Youth Program"):
    return client.post(
        "/api/classes",
        json={
            "name": name,
            "restriction": "temporarily_restricted",
            "default_function": "program",
        },
    ).json()


def test_release_suggest_equals_class_expenses_less_prior_releases(
    client, db_session, seed_accounts
):
    fund = _restricted_fund(client)
    income, expense = _accts(db_session)
    create_journal_entry(
        db_session,
        date(2026, 3, 10),
        "grant spending",
        [
            {
                "account_id": expense.id,
                "debit": Decimal("3300"),
                "credit": Decimal("0"),
            },
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("3300")},
        ],
        class_id=fund["id"],
    )
    db_session.commit()

    s = client.get(
        f"/api/nonprofit/releases/suggest?class_id={fund['id']}"
        "&start_date=2026-01-01&end_date=2026-06-30"
    ).json()
    assert Decimal(s["expenses"]) == Decimal("3300")
    assert Decimal(s["released"]) == Decimal("0")
    assert Decimal(s["suggested"]) == Decimal("3300")

    # release part of it, then the suggestion drops by that much
    r = client.post(
        "/api/nonprofit/releases",
        json={
            "date": "2026-04-30",
            "class_id": fund["id"],
            "amount": "1000",
            "period_start": "2026-01-01",
            "period_end": "2026-04-30",
        },
    )
    assert r.status_code == 201, r.text
    s = client.get(
        f"/api/nonprofit/releases/suggest?class_id={fund['id']}"
        "&start_date=2026-01-01&end_date=2026-06-30"
    ).json()
    assert Decimal(s["released"]) == Decimal("1000")
    assert Decimal(s["suggested"]) == Decimal("2300")

    # amount omitted = the suggestion
    r = client.post(
        "/api/nonprofit/releases",
        json={
            "date": "2026-06-30",
            "class_id": fund["id"],
            "period_start": "2026-01-01",
            "period_end": "2026-06-30",
        },
    )
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["amount"]) == Decimal("2300")
    assert r.json()["number"].startswith("RL-")
    assert r.json()["class_name"] == "Youth Program"

    # nothing left -> 422
    r = client.post(
        "/api/nonprofit/releases",
        json={"date": "2026-06-30", "class_id": fund["id"], "period_end": "2026-06-30"},
    )
    assert r.status_code == 422
    # an unrestricted fund cannot release
    plain = client.post("/api/classes", json={"name": "General"}).json()
    r = client.post(
        "/api/nonprofit/releases",
        json={"date": "2026-06-30", "class_id": plain["id"], "amount": "5"},
    )
    assert r.status_code == 422


def test_release_posts_and_voids_symmetrically(client, db_session, seed_accounts):
    from app.models.accounts import Account as _A

    fund = _restricted_fund(client, "Scholarship")
    r = client.post(
        "/api/nonprofit/releases",
        json={"date": "2026-06-30", "class_id": fund["id"], "amount": "750.25"},
    )
    assert r.status_code == 201, r.text
    rel = r.json()
    with_acct = (
        db_session.query(_A).filter_by(name="Net Assets With Donor Restrictions").one()
    )
    without_acct = (
        db_session.query(_A)
        .filter_by(name="Net Assets Without Donor Restrictions")
        .one()
    )
    txn = db_session.get(Transaction, rel["transaction_id"])
    assert txn.source_type == "restriction_release"
    by_acct = {ln.account_id: ln for ln in txn.lines}
    assert by_acct[with_acct.id].debit == Decimal("750.25")
    assert by_acct[without_acct.id].credit == Decimal("750.25")
    assert all(ln.class_id == fund["id"] for ln in txn.lines)
    assert all(ln.function is None for ln in txn.lines)
    assert _ledger_balanced(db_session)

    listed = client.get("/api/nonprofit/releases").json()
    assert [x["number"] for x in listed] == [rel["number"]]

    r = client.post(f"/api/nonprofit/releases/{rel['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"
    void_txn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "restriction_release_void",
            Transaction.source_id == rel["id"],
        )
        .one()
    )
    rev = {ln.account_id: ln for ln in void_txn.lines}
    assert rev[with_acct.id].credit == Decimal("750.25")
    assert rev[with_acct.id].class_id == fund["id"]
    assert _ledger_balanced(db_session)
    # the fund's released total nets to zero
    s = client.get(
        f"/api/nonprofit/releases/suggest?class_id={fund['id']}&end_date=2026-12-31"
    ).json()
    assert Decimal(s["released"]) == Decimal("0")
    # second void refused
    assert client.post(f"/api/nonprofit/releases/{rel['id']}/void").status_code == 400


def test_release_respects_closing_date(client, db_session, seed_accounts):
    fund = _restricted_fund(client, "Endowment")
    r = client.post(
        "/api/nonprofit/releases",
        json={"date": "2026-02-15", "class_id": fund["id"], "amount": "10"},
    )
    assert r.status_code == 201, r.text
    assert (
        client.put("/api/settings", json={"closing_date": "2026-03-31"}).status_code
        == 200
    )
    blocked = client.post(
        "/api/nonprofit/releases",
        json={"date": "2026-03-01", "class_id": fund["id"], "amount": "10"},
    )
    assert blocked.status_code == 403
    assert (
        client.post(f"/api/nonprofit/releases/{r.json()['id']}/void").status_code == 403
    )

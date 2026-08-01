"""Financial-report PDFs: render smoke tests for both paper sizes and
the statements pack."""

from datetime import date
from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.services.accounting import create_journal_entry
from app.services.settings_service import set_setting


def _post_activity(db_session):
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
    create_journal_entry(
        db_session,
        date(2026, 6, 15),
        "pdf smoke revenue",
        [
            {"account_id": expense.id, "debit": Decimal("40"), "credit": Decimal("0")},
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("40")},
        ],
    )
    db_session.commit()


def test_profit_loss_pdf_renders(client, db_session, seed_accounts):
    _post_activity(db_session)
    resp = client.get(
        "/api/reports/profit-loss/pdf?start_date=2026-01-01&end_date=2026-12-31"
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:5] == b"%PDF-"


def test_balance_sheet_pdf_renders(client, db_session, seed_accounts):
    _post_activity(db_session)
    resp = client.get("/api/reports/balance-sheet/pdf?as_of_date=2026-12-31")
    assert resp.status_code == 200
    assert resp.content[:5] == b"%PDF-"


def test_statements_pack_renders_both_paper_sizes(client, db_session, seed_accounts):
    _post_activity(db_session)
    for size in ("letter", "a4"):
        set_setting(db_session, "pdf_paper_size", size)
        db_session.commit()
        resp = client.get(
            "/api/reports/financial-statements/pdf"
            "?start_date=2026-01-01&end_date=2026-12-31"
        )
        assert resp.status_code == 200, (size, resp.text)
        assert resp.content[:5] == b"%PDF-"
        # A4 and letter pages differ in size, so the rendered bytes must too
    set_setting(db_session, "pdf_paper_size", "letter")
    db_session.commit()


def test_pack_differs_by_paper_size(client, db_session, seed_accounts):
    _post_activity(db_session)
    outputs = {}
    for size in ("letter", "a4"):
        set_setting(db_session, "pdf_paper_size", size)
        db_session.commit()
        outputs[size] = client.get(
            "/api/reports/financial-statements/pdf"
            "?start_date=2026-01-01&end_date=2026-12-31"
        ).content
    assert outputs["letter"] != outputs["a4"]

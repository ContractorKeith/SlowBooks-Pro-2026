"""A failed Mercury sync must leave all feeds and existing matches unchanged."""

import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy import select

from app.database import Base
from app.models.banking import BankAccount, BankTransaction
from app.models.bank_rules import BankRule
from app.routes import mercury as routes
from app.services import mercury_balances as balances
from app.services.accounting import create_journal_entry
from tests.test_mercury import (  # noqa: F401
    ACCOUNT,
    SECOND_ACCOUNT,
    ACCOUNT_DATA,
    TXN,
    bank as bank,
    transport as transport,
    connect_and_map,
    transaction,
)


def snapshot(db):
    return {
        table.name: sorted((tuple(row) for row in db.execute(select(table))), key=repr)
        for table in Base.metadata.sorted_tables
    }


@pytest.mark.parametrize("failure", ["balance_conflict", "second_import", "metadata"])
def test_failure_rolls_back_all_feeds(
    client, db_session, bank, transport, seed_accounts, monkeypatch, failure
):
    responses, _ = transport
    second = BankAccount(name="Second Mercury", account_id=seed_accounts["1010"].id)
    db_session.add(second)
    db_session.add(
        BankRule(
            name="Client income",
            pattern="Client LLC",
            rule_type="contains",
            account_id=seed_accounts["4000"].id,
            priority=10,
            is_active=True,
        )
    )
    db_session.add(
        BankTransaction(
            bank_account_id=bank.id,
            date=date(2026, 8, 10),
            amount=Decimal("17"),
            payee="Client LLC",
            match_status="unmatched",
            import_id="existing-row",
        )
    )
    for code, amount in (
        ("1000", Decimal("2500.25")),
        ("1010", Decimal("2500.25")),
        ("1000", Decimal("17")),
    ):
        create_journal_entry(
            db_session,
            date(2026, 8, 10),
            "Existing receipt",
            [
                {
                    "account_id": seed_accounts[code].id,
                    "debit": amount,
                    "credit": 0,
                },
                {
                    "account_id": seed_accounts["4000"].id,
                    "debit": 0,
                    "credit": amount,
                },
            ],
            source_type="journal",
        )
    db_session.commit()
    responses["/api/v1/accounts"] = {
        "accounts": [ACCOUNT_DATA, {**ACCOUNT_DATA, "id": SECOND_ACCOUNT}],
        "page": {},
    }
    connect_and_map(client, bank)
    assert (
        client.post(
            "/api/mercury/map",
            json={"mapping": {ACCOUNT: bank.id, SECOND_ACCOUNT: second.id}},
        ).status_code
        == 200
    )
    import httpx

    responses["/api/v1/transactions"] = lambda request: httpx.Response(
        200,
        json={
            "transactions": [
                transaction(
                    accountId=request.url.params["accountId"],
                    id=(
                        TXN
                        if request.url.params["accountId"] == ACCOUNT
                        else "10000000-0000-4000-8000-000000000002"
                    ),
                )
            ],
            "page": {},
        },
    )
    before = snapshot(db_session)
    original_import = routes.import_transactions
    original_setting = routes.set_setting

    def fail(*args, **kwargs):
        raise balances.BalanceConflict("Retry this balance update")

    def import_then_fail(db, bank_id, rows, **kwargs):
        result = original_import(db, bank_id, rows, **kwargs)
        if bank_id == second.id:
            raise RuntimeError("second import failed")
        return result

    def setting_then_fail(db, key, value):
        original_setting(db, key, value)
        if key == "mercury_last_sync":
            db.flush()
            raise RuntimeError("metadata failed")

    with monkeypatch.context() as patch:
        if failure == "balance_conflict":
            patch.setattr(balances, "save_snapshots", fail)
        elif failure == "second_import":
            patch.setattr(routes, "import_transactions", import_then_fail)
        else:
            patch.setattr(routes, "set_setting", setting_then_fail)
        if failure == "balance_conflict":
            response = client.post("/api/mercury/sync", json={"since": "2026-08-01"})
            assert response.status_code == 503
        else:
            with pytest.raises(RuntimeError):
                client.post("/api/mercury/sync", json={"since": "2026-08-01"})
    assert snapshot(db_session) == before
    # A retry starts from the same state and then deduplicates both feeds.
    for imported, skipped in [(2, 0), (0, 2)]:
        result = client.post("/api/mercury/sync", json={"since": "2026-08-01"}).json()
        assert (result["imported"], result["skipped"]) == (imported, skipped)
        assert result["matched"] == (3 if imported else 0)

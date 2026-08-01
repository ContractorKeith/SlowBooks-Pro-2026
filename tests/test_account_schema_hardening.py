"""Account schema hardening: NULL balances and blank account numbers.

- accounts.balance is nullable in practice (legacy/imported rows); the
  response schema must coalesce NULL to 0 instead of 500ing on read.
- accounts.account_number is unique; a blank string must normalize to
  NULL or the second no-number account collides with the first.
"""

from sqlalchemy import text

from app.models.accounts import Account, AccountType


def test_null_balance_account_reads_as_zero(client, db_session):
    account = Account(name="Legacy NULL balance", account_type=AccountType.EXPENSE)
    db_session.add(account)
    db_session.commit()
    db_session.execute(
        text("UPDATE accounts SET balance = NULL WHERE id = :id"),
        {"id": account.id},
    )
    db_session.commit()

    resp = client.get(f"/api/accounts/{account.id}")
    assert resp.status_code == 200
    assert resp.json()["balance"] == "0"


def test_blank_account_number_normalizes_to_null(client):
    first = client.post(
        "/api/accounts",
        json={"name": "No number one", "account_type": "expense", "account_number": ""},
    )
    assert first.status_code == 201
    assert first.json()["account_number"] is None

    # A second blank-numbered account must not hit the unique constraint
    second = client.post(
        "/api/accounts",
        json={
            "name": "No number two",
            "account_type": "expense",
            "account_number": "   ",
        },
    )
    assert second.status_code == 201
    assert second.json()["account_number"] is None


def test_blank_account_number_on_update_normalizes_to_null(client):
    created = client.post(
        "/api/accounts",
        json={
            "name": "Numbered",
            "account_type": "expense",
            "account_number": "9998",
        },
    )
    assert created.status_code == 201
    account_id = created.json()["id"]

    updated = client.put(f"/api/accounts/{account_id}", json={"account_number": ""})
    assert updated.status_code == 200
    assert updated.json()["account_number"] is None

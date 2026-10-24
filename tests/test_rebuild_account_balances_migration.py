"""The 49a0b2c7e1d3 data migration (#490) sets every stored Account.balance
to the sum of the account's lines in its natural direction, so a book whose
reclassifies left balances on the old account comes right on upgrade. Runs on
SQLite always, and on PostgreSQL when SLOWBOOKS_TEST_POSTGRES_URL names a
server (see test_account_detail_types_migration.py)."""

import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
BEFORE = "4ac01efbd72a"
REVISION = "49a0b2c7e1d3"


def _cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = url
    return cfg


def _account(conn, number, name, atype):
    conn.execute(
        sa.text(
            "INSERT INTO accounts (name, account_number, account_type, is_active, "
            "is_system, balance, detail_type) VALUES (:name, :number, :atype, :t, "
            ":f, 0, :detail)"
        ),
        {
            "name": name,
            "number": number,
            "atype": atype,
            "t": True,
            "f": False,
            "detail": {"ASSET": "bank", "EXPENSE": "expense", "INCOME": "income"}[
                atype
            ],
        },
    )
    return conn.execute(sa.text("SELECT max(id) FROM accounts")).scalar()


def _scenario(url: str) -> None:
    command.upgrade(_cfg(url), BEFORE)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            cash = _account(conn, "1000", "Checking", "ASSET")
            interest = _account(conn, "6400", "Interest Expense", "EXPENSE")
            internet = _account(conn, "6410", "Internet", "EXPENSE")
            sales = _account(conn, "4000", "Sales", "INCOME")
            empty = _account(conn, "6420", "Nothing posted", "EXPENSE")
            conn.execute(
                sa.text(
                    "INSERT INTO transactions (date, description) VALUES "
                    "('2026-01-05', 'bill')"
                )
            )
            txn = conn.execute(sa.text("SELECT max(id) FROM transactions")).scalar()
            for account, debit, credit in (
                (internet, "32.15", "0"),  # reclassified here from Interest
                (cash, "0", "32.15"),
                (cash, "250.00", "0"),
                (sales, "0", "250.00"),
            ):
                conn.execute(
                    sa.text(
                        "INSERT INTO transaction_lines (transaction_id, account_id, "
                        "debit, credit, is_billable, cleared) VALUES (:t, :a, :d, :c, :f, :f)"
                    ),
                    {"t": txn, "a": account, "d": debit, "c": credit, "f": False},
                )
            # The pre-fix book: the stored balance stayed on Interest Expense,
            # Internet shows nothing, and cash drifted.
            for account, stale in ((interest, "32.15"), (internet, "0"), (cash, "0")):
                conn.execute(
                    sa.text("UPDATE accounts SET balance = :b WHERE id = :id"),
                    {"b": stale, "id": account},
                )
            conn.execute(
                sa.text("UPDATE accounts SET balance = NULL WHERE id = :id"),
                {"id": empty},
            )
    finally:
        engine.dispose()

    command.upgrade(_cfg(url), REVISION)
    command.upgrade(_cfg(url), REVISION)  # a second run changes nothing

    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            got = {
                number: Decimal(str(balance))
                for number, balance in conn.execute(
                    sa.text("SELECT account_number, balance FROM accounts")
                ).all()
            }
    finally:
        engine.dispose()
    assert got == {
        "1000": Decimal("217.85"),
        "6400": Decimal("0"),
        "6410": Decimal("32.15"),
        "4000": Decimal("250.00"),
        "6420": Decimal("0"),
    }
    command.downgrade(_cfg(url), BEFORE)


def test_sqlite_rebuilds_every_balance_from_the_lines(tmp_path):
    _scenario("sqlite:///" + (tmp_path / "books.db").as_posix())


@pytest.mark.skipif(
    not os.environ.get("SLOWBOOKS_TEST_POSTGRES_URL"),
    reason="SLOWBOOKS_TEST_POSTGRES_URL not set",
)
def test_postgres_rebuilds_every_balance_from_the_lines():
    base = os.environ["SLOWBOOKS_TEST_POSTGRES_URL"]
    name = "slowbooks_rebuild_" + uuid.uuid4().hex[:12]
    admin = sa.create_engine(base, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
        url = base.rsplit("/", 1)[0] + "/" + name
        try:
            _scenario(url)
        finally:
            with admin.connect() as conn:
                conn.execute(sa.text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()

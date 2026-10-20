"""The home_currency setting reaches the screen, the PDFs and the sentences
(#125, from discussion #112).

The setting drove the accounting and nothing else: utils.js formatted every
figure as US dollars, the PDF filters wrote a literal "$", and a dozen
error messages did the same. A South African company could keep its books
in ZAR and watch every screen print dollars. Now app/services/money.py is
the one place an amount becomes text on the server, utils.js formatCurrency
is its twin on the screen, and both read the setting.
"""

import re
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import money, pdf_service
from app.services.email_service import render_invoice_email
from app.services.settings_service import get_all_settings

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _invoice(client, customer_id, **extra):
    body = {
        "customer_id": customer_id,
        "date": "2026-09-10",
        "tax_rate": 0,
        "lines": [
            {"description": "Graphic design, per hour", "quantity": 10, "rate": 85}
        ],
    }
    body.update(extra)
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def html_pdfs(monkeypatch):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())


# ── the formatter ────────────────────────────────────────────────────────


def test_money_text_prints_the_home_currency_symbol():
    assert money.money_text(1234.5) == "$1,234.50"
    assert money.money_text(-10) == "-$10.00"
    with money.using_home_currency("zar"):
        assert money.money_text(1234.5) == "R1,234.50"
        assert money.money_text(-10) == "-R10.00"
        assert money.money_text(Decimal("0")) == "R0.00"
        assert money.money_text(-0.001) == "R0.00", "nothing has no sign"
        # another currency's amount carries its code, as it always did
        assert money.money_text(850, "USD") == "USD 850.00"
        assert money.money_text(-10, "EUR") == "EUR -10.00"
        # the home currency's own code is not a foreign one
        assert money.money_text(850, "ZAR") == "R850.00"
    with money.using_home_currency("EUR"):
        assert money.money_text(850) == "€850.00"
    # no symbol known: the code leads, the way a foreign amount reads
    with money.using_home_currency("BWP"):
        assert money.money_text(1234.5) == "BWP 1,234.50"
        assert money.money_text(-10) == "BWP -10.00"
    # the block over, dollars again
    assert money.money_text(1) == "$1.00"


def test_the_pdf_filters_use_the_same_formatter():
    with money.using_home_currency("ZAR"):
        assert pdf_service._format_currency(850) == "R850.00"
        assert pdf_service._format_currency(-10) == "-R10.00"
        assert pdf_service._format_currency(850, "EUR") == "EUR 850.00"
        assert pdf_service._format_rate("0.045") == "R0.045"
    assert pdf_service._format_currency(850) == "$850.00"


def test_a_blank_or_malformed_code_keeps_dollars():
    for bad in ("", None, "dollars", "US", "12$"):
        with money.using_home_currency(bad):
            assert money.money_text(1) == "$1.00", bad
    assert money.normalize_code(" eur ") == "EUR"
    assert money.normalize_code("EURO") == ""


def test_the_screen_and_the_server_keep_one_symbol_table():
    src = (JS / "utils.js").read_text(encoding="utf-8")
    block = src[src.index("const CURRENCY_SYMBOLS = {") :]
    block = block[: block.index("};")]
    screen = dict(re.findall(r"([A-Z]{3}): '([^']+)'", block))
    assert screen == money.SYMBOLS


def test_the_picker_carries_the_iso_list():
    src = (JS / "utils.js").read_text(encoding="utf-8")
    block = src[src.index("const CURRENCIES = [") :]
    block = block[: block.index("];")]
    codes = re.findall(r"\['([A-Z]{3})', '", block)
    assert len(codes) > 150
    assert len(codes) == len(set(codes))
    for wanted in ("USD", "ZAR", "EUR", "GBP", "CAD", "AUD", "INR", "NGN", "KES"):
        assert wanted in codes, wanted
    for code in money.SYMBOLS:
        assert code in codes, f"{code} has a symbol but is not in the picker"
    # Settings offers the home currency from the same list
    settings = (JS / "settings.js").read_text(encoding="utf-8")
    assert 'name="home_currency"' in settings
    assert "currencyOptionsHtml(s.home_currency" in settings


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_screen_formats_in_the_home_currency():
    out = subprocess.run(
        ["node", "--test", str(ROOT / "tests" / "js" / "home_currency.test.cjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stdout + out.stderr


# ── through the app ──────────────────────────────────────────────────────


def test_a_rand_company_prints_rand_everywhere(
    client, db_session, seed_accounts, seed_customer, html_pdfs
):
    r = client.put("/api/settings", json={"home_currency": "zar"})
    assert r.status_code == 200, r.text
    assert r.json()["home_currency"] == "ZAR"
    assert client.get("/api/settings").json()["home_currency"] == "ZAR"

    inv = _invoice(client, seed_customer.id)
    for page in ("pdf", "print-preview"):
        html = client.get(f"/api/invoices/{inv['id']}/{page}").text
        assert "R850.00" in html, page
        assert "$850.00" not in html and "$85.00" not in html, page

    # a dollar invoice is the foreign one now
    usd = _invoice(client, seed_customer.id, currency="USD", exchange_rate=18)
    html = client.get(f"/api/invoices/{usd['id']}/pdf").text
    assert "USD 850.00" in html
    assert "$850.00" not in html

    # the covering email, and its fallback body
    from app.models.invoices import Invoice

    orm = db_session.get(Invoice, inv["id"])
    settings = get_all_settings(db_session)
    body = render_invoice_email(orm, settings)
    assert "R850.00" in body and "$850.00" not in body
    from app.services.email_service import _fallback_invoice_body, invoice_email_context

    fallback = _fallback_invoice_body(
        orm, settings, invoice_email_context(orm, settings)
    )
    assert "R850.00" in fallback and "$" not in fallback

    # a sentence the API writes
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-10",
            "tax_rate": 0,
            "lines": [{"description": "Nothing yet", "quantity": 1, "rate": 0}],
        },
    )
    assert r.status_code == 409, r.text
    assert "R0.00" in r.text and "$0.00" not in r.text

    # the report PDFs
    html = client.get(
        "/api/reports/profit-loss/pdf?start_date=2026-01-01&end_date=2026-12-31"
    ).text
    assert "R" in html and "$" not in html


def test_the_home_currency_is_an_iso_code_and_set_before_posting(
    client, db_session, seed_accounts, seed_customer
):
    r = client.put("/api/settings", json={"home_currency": "rand"})
    assert r.status_code == 422, r.text
    assert "three-letter ISO code" in r.text

    # the same value again is not a change
    assert client.put("/api/settings", json={"home_currency": "usd"}).status_code == 200

    _invoice(client, seed_customer.id)
    r = client.put("/api/settings", json={"home_currency": "ZAR"})
    assert r.status_code == 409, r.text
    assert "already has postings" in r.text
    assert client.get("/api/settings").json()["home_currency"] == "USD"
    # saying USD again, with postings, is still fine
    assert client.put("/api/settings", json={"home_currency": "USD"}).status_code == 200


def test_each_request_reads_its_own_company_setting(client, db_session):
    # outside a request the formatter prints dollars (the default)
    assert money.home_currency() == "USD"
    assert client.put("/api/settings", json={"home_currency": "EUR"}).status_code == 200
    # a request after the change reads euros; the test's own context is untouched
    r = client.get("/api/reports/trial-balance/pdf?as_of_date=2026-12-31")
    assert r.status_code == 200, r.text
    assert money.home_currency() == "USD"

"""Terminology guards: the words a nonprofit sees come from one dictionary.

Source-level checks keep the JS and Python dictionaries identical, keep
protected words (Sales, Vendor ...) out of it, and make sure every shell
label that carries a business word is an exact key so the boot-time walk
(App.applyTerminology) actually swaps it. Behavioural checks pin the
company_type setting itself."""

import json
import re
from pathlib import Path

from app.services import terminology
from app.services.terminology import NONPROFIT, PROTECTED_WORDS, Terms

ROOT = Path(__file__).resolve().parent.parent
TERMS_JS = ROOT / "app/static/js/terms.js"
INDEX = ROOT / "index.html"

BUSINESS_WORD = re.compile(
    r"\b(Customers?|Invoices?|Sales Receipts?|Class(es)?|Jobs?)\b"
)


def _js_dictionary() -> dict:
    src = TERMS_JS.read_text()
    m = re.search(r"const TERMS_NONPROFIT = (\{.*?\});", src, re.S)
    assert m, "TERMS_NONPROFIT literal not found in terms.js"
    return json.loads(m.group(1))


# ---------------------------------------------------------------------------
# Dictionary shape
# ---------------------------------------------------------------------------


def test_js_and_python_dictionaries_agree():
    assert _js_dictionary() == NONPROFIT


def test_dictionary_never_maps_protected_words():
    for word in PROTECTED_WORDS:
        assert word not in NONPROFIT, word
    for key in NONPROFIT:
        assert not key.startswith("Sales ") or key.startswith("Sales Receipt"), key


def test_keys_are_business_words_and_values_differ():
    for key, value in NONPROFIT.items():
        assert key != value, key
        assert key[0].isupper(), key


# ---------------------------------------------------------------------------
# Lookup rules (Python; the JS mirror follows the same rules)
# ---------------------------------------------------------------------------


def test_business_mode_is_identity():
    t = Terms("business")
    assert t("Invoice") == "Invoice"
    assert t.text("Search customers, invoices...") == "Search customers, invoices..."
    assert not t.is_nonprofit
    assert Terms("banana").mode == "business"


def test_nonprofit_lookup_handles_plural_and_case():
    t = Terms("nonprofit")
    assert t("Invoice") == "Pledge"
    assert t("Invoices") == "Pledges"
    assert t("invoices") == "pledges"
    assert t("customer") == "donor"
    assert t("Classes") == "Funds"
    assert t("Vendor") == "Vendor"
    assert t("Sales Tax") == "Sales Tax"
    assert t("") == ""


def test_prose_swap_keeps_case_and_leaves_sales_tax_alone():
    t = Terms("nonprofit")
    assert t.text("Search customers, invoices...") == "Search donors, pledges..."
    assert t.text("No invoices yet") == "No pledges yet"
    assert t.text("Sales Tax Report") == "Sales Tax Report"
    assert t.text("CUSTOMER") == "DONOR"
    assert t.text("Profit & Loss by Class") == "Statement of Activities by Fund"


def test_filename_forms():
    t = Terms("nonprofit")
    assert t.slug("Profit & Loss") == "statement-of-activities"
    assert t.compact("Sales Receipt") == "Donation"
    assert Terms("business").slug("Profit & Loss") == "profit-loss"
    assert Terms("business").compact("Sales Receipt") == "SalesReceipt"


# ---------------------------------------------------------------------------
# The shell: every label the boot walk touches must be an exact key
# ---------------------------------------------------------------------------


def _shell_texts():
    html = INDEX.read_text()
    texts = re.findall(r'<li class="nav-section">([^<]+)</li>', html)
    for m in re.finditer(
        r'<a href="#/[^"]*" class="nav-link[^"]*"[^>]*>.*?</a>', html, re.S
    ):
        inner = re.sub(r"<span[^>]*>.*?</span>", "", m.group(0), flags=re.S)
        texts.append(re.sub(r"<[^>]+>", "", inner).strip())
    texts += re.findall(
        r'<button class="tb-btn" data-action="[^"]*">([^<]+)</button>', html
    )
    return [t.replace("&amp;", "&") for t in texts]


def test_nav_and_toolbar_labels_with_business_words_are_keys():
    missing = [
        t for t in _shell_texts() if BUSINESS_WORD.search(t) and t not in NONPROFIT
    ]
    assert not missing, missing


def test_shell_loads_terms_before_pages():
    html = INDEX.read_text()
    assert html.index("terms.js") < html.index("customers.js")
    app_js = (ROOT / "app/static/js/app.js").read_text()
    assert "App.loadCompanySettings().then(" in app_js
    assert "App.applyTerminology();" in app_js


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------


def test_company_type_defaults_to_business(client):
    assert client.get("/api/settings").json()["company_type"] == "business"


def test_company_type_round_trips_and_rejects_unknown_values(client):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    assert r.json()["company_type"] == "nonprofit"
    r = client.put("/api/settings", json={"company_type": "banana"})
    assert r.status_code == 422
    assert client.get("/api/settings").json()["company_type"] == "nonprofit"
    r = client.put("/api/settings", json={"company_type": "business"})
    assert r.json()["company_type"] == "business"


def test_terms_from_db_follow_the_setting(client, db_session):
    assert not terminology.terms_from_db(db_session).is_nonprofit
    client.put("/api/settings", json={"company_type": "nonprofit"})
    assert terminology.terms_from_db(db_session).is_nonprofit
    assert terminology.terms_for({"company_type": "nonprofit"})("Class") == "Fund"
    assert terminology.terms_for(None)("Class") == "Class"


# ---------------------------------------------------------------------------
# Setup accounts (what the Settings page calls when switching to nonprofit)
# ---------------------------------------------------------------------------


def test_setup_accounts_is_idempotent_and_yields_taken_numbers(client, seed_accounts):
    # 3300 is free in the seed chart; take 6960 first to prove the yield.
    r = client.post(
        "/api/accounts",
        json={
            "name": "Some Old Expense",
            "account_number": "6960",
            "account_type": "expense",
        },
    )
    assert r.status_code in (200, 201), r.text

    first = client.post("/api/nonprofit/setup-accounts")
    assert first.status_code == 200, first.text
    by_name = {a["name"]: a for a in first.json()}
    assert by_name["Net Assets Without Donor Restrictions"]["account_number"] == "3300"
    assert by_name["Net Assets With Donor Restrictions"]["account_type"] == "equity"
    assert by_name["In-Kind Contributions"]["account_number"] == "4400"
    assert by_name["Bad Debt Expense"]["account_number"] is None  # 6960 was taken
    assert all(a["is_system"] for a in first.json())

    second = client.post("/api/nonprofit/setup-accounts")
    assert [a["id"] for a in second.json()] == [a["id"] for a in first.json()]
    assert client.get("/api/accounts").status_code == 200
    names = [a["name"] for a in client.get("/api/accounts").json()]
    assert names.count("Bad Debt Expense") == 1

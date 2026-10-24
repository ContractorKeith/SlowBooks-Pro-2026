"""Add a chart account from the bank-feed review and the Bank Rule form in a
real browser (#492, the register's #386 and the bill line's #485 before it).

On the review screen each row's category cell has an Add button beside the
select; it opens a small panel, makes an expense account, picks it on the line
and categorises the line (a PATCH, as picking a category is — the stub stands
in for the server and the PATCH it records is what the real server stores). The
Bank Rule form's Account select grows the same Add button. The API is stubbed;
the page is the real one (tests/test_browser_ui.py's fixtures)."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)

CHART = [
    {
        "id": 1,
        "name": "Checking",
        "account_number": "1000",
        "account_type": "asset",
        "bank_kind": "bank",
    },
    {
        "id": 2,
        "name": "Automobile",
        "account_number": "6100",
        "account_type": "expense",
    },
    {
        "id": 3,
        "name": "Accounts Payable",
        "account_number": "2000",
        "account_type": "liability",
        "is_control": True,
    },
    {
        "id": 4,
        "name": "Prepaid Expenses",
        "account_number": "1400",
        "account_type": "asset",
    },
]
REGISTER = {
    "account_name": "Checking",
    "account_number": "1000",
    "bank_kind": "bank",
    "entries": [],
    "balance": 0,
    "opening_balance": 0,
    "class_name": None,
}
OVERVIEW = [{"account_id": 1, "feed": {"bank_account_id": 1}, "last_reconciled": None}]
LINE = {
    "id": 50,
    "date": "2026-09-04",
    "payee": "OFFICE DEPOT",
    "description": "",
    "amount": -45.0,
    "category_account_id": None,
    "category_name": None,
    "match_status": "unmatched",
}


def _route_accounts(posted):
    def handler(route):
        if route.request.method == "POST":
            posted.append(route.request.post_data_json)
            return route.fulfill(
                status=201, json={**posted[-1], "id": 77, "is_active": True}
            )
        return route.fulfill(json=CHART)

    return handler


def _goto_review(browser, posted, state):
    page = _open(browser, 1280, 800, "#/")
    page.route("**/api/accounts*", _route_accounts(posted))
    page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
    page.route("**/api/banking/overview", lambda r: r.fulfill(json=OVERVIEW))
    page.route("**/api/classes*", lambda r: r.fulfill(json=[]))
    page.route("**/api/vendors*", lambda r: r.fulfill(json=[]))
    page.route("**/api/customers*", lambda r: r.fulfill(json=[]))

    # the review queue: one line, page one then empty (fetchAllPages)
    def _transactions(route):
        url = route.request.url
        if "skip=0" in url or "skip=" not in url:
            line = {**LINE, "category_account_id": state.get("category")}
            return route.fulfill(json=[line])
        return route.fulfill(json=[])

    # a single line's PATCH: record the category the way the server would store it
    def _patch_line(route):
        if route.request.method == "PATCH":
            body = route.request.post_data_json
            state["category"] = body.get("category_account_id")
            return route.fulfill(
                json={**LINE, "category_account_id": state["category"]}
            )
        return route.fulfill(json={**LINE})

    page.route("**/api/banking/transactions?*", _transactions)
    page.route("**/api/banking/transactions/*", _patch_line)
    page.goto(page.url.split("#")[0] + "#/banking/1")
    page.wait_for_selector("#cat-50")
    return page


def test_add_account_from_a_review_line_categorises_the_line(browser):
    posted, state = [], {}
    page = _goto_review(browser, posted, state)
    try:
        # the Add control is a button beside the select, not a trap option
        assert "+ Add" not in "".join(page.locator("#cat-50 option").all_inner_texts())
        page.click("#cat-50-add")
        page.wait_for_selector("#cat-50-new", state="visible")
        assert page.evaluate("() => document.activeElement.id") == "cat-50-new-name"

        # the six category types are offered
        assert page.locator("#cat-50-new-type option").all_inner_texts() == [
            "Expense",
            "Income",
            "Cogs",
            "Asset",
            "Liability",
            "Equity",
        ]

        # a Liability parent: Accounts Payable is a control account, so only
        # None is offered
        page.select_option("#cat-50-new-type", "liability")
        assert page.locator("#cat-50-new-parent option").all_inner_texts() == ["None"]
        # an Asset parent: Prepaid is offered, but the feed's own bank account
        # (Checking, the excludeId) is not
        page.select_option("#cat-50-new-type", "asset")
        assert page.locator("#cat-50-new-parent option").all_inner_texts() == [
            "None",
            "1400 - Prepaid Expenses",
        ]
        page.select_option("#cat-50-new-type", "expense")

        page.fill("#cat-50-new-name", "Bank Charges")
        page.click("text=Add account")
        page.wait_for_function("() => document.getElementById('cat-50').value === '77'")
        assert posted == [
            {
                "name": "Bank Charges",
                "account_type": "expense",
                "parent_id": None,
                "account_number": None,
            }
        ]
        # the line's select shows the new account, selected
        assert page.locator("#cat-50 option:checked").inner_text() == "Bank Charges"
        assert not page.is_visible("#cat-50-new")

        # and the server has the line categorised to it (the stub stored the PATCH)
        assert state["category"] == 77
        line = page.evaluate(
            "async () => (await (await fetch('/api/banking/transactions?bank_account_id=1&status=unmatched&skip=0&limit=1000')).json())[0]"
        )
        assert line["category_account_id"] == 77
    finally:
        page.close()


def _goto_rules(browser, posted):
    page = _open(browser, 1280, 800, "#/")
    page.route("**/api/accounts*", _route_accounts(posted))
    page.route("**/api/bank-rules", lambda r: r.fulfill(json=[]))
    page.goto(page.url.split("#")[0] + "#/bank-rules")
    page.wait_for_selector("text=+ New Rule")
    return page


def test_add_account_from_the_bank_rule_form(browser):
    posted = []
    page = _goto_rules(browser, posted)
    try:
        page.click("text=+ New Rule")
        page.wait_for_selector("#rule-account")
        assert "+ Add" not in "".join(
            page.locator("#rule-account option").all_inner_texts()
        )
        page.click("#rule-account-add")
        page.wait_for_selector("#rule-account-new", state="visible")
        # the rule form's category types: the select's five, never equity, so
        # an account made here can always be shown and saved on the rule
        assert page.locator("#rule-account-new-type option").all_inner_texts() == [
            "Expense",
            "Income",
            "Asset",
            "Liability",
            "Cogs",
        ]
        page.fill("#rule-account-new-name", "Bank Fees")
        page.click("text=Add account")
        page.wait_for_function(
            "() => document.getElementById('rule-account').value === '77'"
        )
        assert posted == [
            {
                "name": "Bank Fees",
                "account_type": "expense",
                "parent_id": None,
                "account_number": None,
            }
        ]
        assert page.locator("#rule-account option:checked").inner_text() == "Bank Fees"
        assert not page.is_visible("#rule-account-new")
    finally:
        page.close()

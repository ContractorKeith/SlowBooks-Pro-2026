"""Add a chart account from an Enter Bill line in a real browser (#485, the
register's #386 before it): the Account cell has an Add button beside the
select; it opens a small panel, makes an expense account and picks it on the
line, and the bill keeps the vendor and amount already typed. A second line
offers the new account too. The API is stubbed; the page is the real one
(tests/test_browser_ui.py's fixtures)."""

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
]


def _open_bill(browser, posted):
    def _accounts(route):
        if route.request.method == "POST":
            posted.append(route.request.post_data_json)
            return route.fulfill(
                status=201, json={**posted[-1], "id": 77, "is_active": True}
            )
        return route.fulfill(json=CHART)

    page = _open(browser, 1280, 800, "#/")
    page.route("**/api/accounts*", _accounts)
    page.route(
        "**/api/vendors*",
        lambda r: r.fulfill(json=[{"id": 3, "name": "Pine Street Lumber"}]),
    )
    page.route("**/api/items*", lambda r: r.fulfill(json=[]))
    page.route("**/api/bills*", lambda r: r.fulfill(json=[]))
    page.goto(page.url.split("#")[0] + "#/bills")
    page.wait_for_selector("text=Enter Bill")
    page.click("text=+ Enter Bill")
    page.wait_for_selector("#bill-vendor")
    return page


def test_add_account_from_a_bill_line_keeps_the_bill(browser):
    posted = []
    page = _open_bill(browser, posted)
    try:
        # a vendor and an amount typed on the first line
        page.select_option("#bill-vendor", "3")
        page.fill("#bill-lines tr:first-child .line-amt", "125.50")

        # the Account cell has an Add button, not a trap option on the select
        assert "+ Add" not in "".join(
            page.locator("#bill-acct-0 option").all_inner_texts()
        )
        page.click("#bill-acct-0-add")
        page.wait_for_selector("#bill-acct-0-new", state="visible")
        assert (
            page.evaluate("() => document.activeElement.id") == "bill-acct-0-new-name"
        )

        # the panel offers expense and COGS types only
        assert page.locator("#bill-acct-0-new-type option").all_inner_texts() == [
            "Expense",
            "Cogs",
        ]
        page.fill("#bill-acct-0-new-name", "Truck Fuel")
        page.click("text=Add account")
        page.wait_for_function(
            "() => document.getElementById('bill-acct-0').value === '77'"
        )
        assert posted == [
            {
                "name": "Truck Fuel",
                "account_type": "expense",
                "parent_id": None,
                "account_number": None,
            }
        ]
        assert page.locator("#bill-acct-0 option:checked").inner_text() == "Truck Fuel"
        assert not page.is_visible("#bill-acct-0-new")

        # the bill kept what was typed
        assert page.input_value("#bill-vendor") == "3"
        assert page.input_value("#bill-lines tr:first-child .line-amt") == "125.50"

        # a second line offers the new account to choose
        page.click("text=+ Add Line")
        page.wait_for_selector("#bill-acct-1")
        assert "Truck Fuel" in "".join(
            page.locator("#bill-acct-1 option").all_inner_texts()
        )
    finally:
        page.close()


def test_enter_in_the_panel_type_select_does_not_submit_the_bill(browser):
    # the panel sits inside the bill form; Enter on a bare select used to
    # submit it and lose the typed account (#485 review)
    posted = []
    page = _open_bill(browser, posted)
    try:
        page.click("#bill-acct-0-add")
        page.wait_for_selector("#bill-acct-0-new", state="visible")
        page.evaluate(
            "() => { window.__submits = 0;"
            " document.getElementById('bill-form').addEventListener('submit',"
            " e => { e.preventDefault(); window.__submits++; }); }"
        )
        page.focus("#bill-acct-0-new-type")
        page.keyboard.press("Enter")
        page.wait_for_timeout(100)
        assert page.evaluate("() => window.__submits") == 0
        assert page.is_visible("#bill-acct-0-new")
    finally:
        page.close()


def test_a_new_account_name_is_escaped_not_rendered(browser):
    # the cjs test stubs escapeHtml as identity, so only the real page can
    # catch an escaping regression on the option it builds
    posted = []
    page = _open_bill(browser, posted)
    try:
        page.click("#bill-acct-0-add")
        page.wait_for_selector("#bill-acct-0-new", state="visible")
        xss = "<img src=x onerror=alert(1)>"
        page.fill("#bill-acct-0-new-name", xss)
        page.click("text=Add account")
        page.wait_for_function(
            "() => document.getElementById('bill-acct-0').value === '77'"
        )
        # the name is shown as text, not parsed into an element
        assert page.locator("#bill-acct-0 option:checked").inner_text() == xss
        assert page.locator("#bill-acct-0 img").count() == 0
    finally:
        page.close()

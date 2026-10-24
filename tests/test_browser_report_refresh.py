"""An open report redraws itself after an edit, and the Refresh control
(#482), in playwright's Chromium on the bakery's books.

- with the P&L open, a journal entry posted through the page's own API
  redraws the report without navigating away and back;
- with "Refresh reports automatically" off, the same post leaves the report
  as it was;
- a stored off preference is honoured on a fresh page load;
- a report closed and left behind does not re-run on a later write, nor push
  its address back onto the bar;
- the Refresh button on the toolbar runs it again on demand.

Skipped, as one module, where playwright or its Chromium is not installed,
as its sibling browser modules are.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_report_views import (  # noqa: E402,F401  (the fixtures)
    PL_URL,
    _open_at,
)
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    browser_fixture,
    company_fixture,
    settle,
    _visit,
)

CONTENT = "() => document.getElementById('report-content').textContent"


def _accounts(client):
    by_number = {
        a["account_number"]: a["id"] for a in client.get("/api/accounts").json()
    }
    return by_number["6000"], by_number["1000"]


def _post_journal(page, expense_id, cash_id, amount):
    """A balanced entry that moves the P&L: an expense up, cash down. Posted
    through the page's API so the in-page write event fires (#482)."""
    page.evaluate(
        """([expense, cash, amount]) => API.post('/journal', {
            date: '2026-09-15',
            description: 'Refresh test entry',
            lines: [
                { account_id: expense, debit: amount, credit: '0' },
                { account_id: cash, debit: '0', credit: amount },
            ],
        })""",
        [expense_id, cash_id, str(amount)],
    )


def test_an_open_report_redraws_after_an_edit_and_refresh_runs_it_again(
    browser, company, books
):
    expense_id, cash_id = _accounts(company)
    page, handled = _open_at(browser, company, PL_URL)
    try:
        page.wait_for_selector("#report-content table")
        before = page.evaluate(CONTENT)

        # a write redraws the open report, no navigation
        _post_journal(page, expense_id, cash_id, 1000)
        page.wait_for_function(
            "(prev) => document.getElementById('report-content').textContent !== prev"
            " && document.getElementById('report-content').querySelector('table')",
            arg=before,
            timeout=5000,
        )
        after_auto = page.evaluate(CONTENT)
        assert after_auto != before
        assert page.evaluate(
            "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
        )

        # the preference off: the same post leaves the report as it is (this
        # is what unticking "Refresh reports automatically" in Settings does
        # to the live cache)
        page.evaluate("() => SettingsPage.saveAutoRefresh(false)")
        settle(page, handled)
        _post_journal(page, expense_id, cash_id, 500)
        page.wait_for_timeout(700)  # past the 300ms debounce, and then some
        assert page.evaluate(CONTENT) == after_auto

        # the Refresh button runs it again on demand
        page.click("#report-refresh-btn")
        page.wait_for_function(
            "(prev) => document.getElementById('report-content').textContent !== prev"
            " && document.getElementById('report-content').querySelector('table')",
            arg=after_auto,
            timeout=5000,
        )
        assert page.evaluate(CONTENT) != after_auto
    finally:
        page.close()


def test_a_stored_off_preference_is_honoured_on_a_fresh_load(browser, company, books):
    expense_id, cash_id = _accounts(company)
    # the preference stored off before the page ever loads
    company.put(
        "/api/preferences/report_auto_refresh", json={"value": {"enabled": False}}
    )
    page, handled = _open_at(browser, company, PL_URL)
    try:
        page.wait_for_selector("#report-content table")
        before = page.evaluate(CONTENT)
        _post_journal(page, expense_id, cash_id, 300)
        page.wait_for_timeout(700)  # past the debounce
        assert page.evaluate(CONTENT) == before  # off: the write does not redraw
        # the Refresh button still runs it on demand
        page.click("#report-refresh-btn")
        page.wait_for_function(
            "(prev) => document.getElementById('report-content').textContent !== prev"
            " && document.getElementById('report-content').querySelector('table')",
            arg=before,
            timeout=5000,
        )
        assert page.evaluate(CONTENT) != before
    finally:
        page.close()


def test_a_closed_report_does_not_redraw_or_push_its_address(browser, company, books):
    expense_id, cash_id = _accounts(company)
    page, handled = _open_at(browser, company, PL_URL)
    try:
        page.wait_for_selector("#report-content table")
        # close the P&L and go to another page
        page.click("#modal-overlay .form-actions button:has-text('Close')")
        page.wait_for_function(
            "() => document.getElementById('modal-overlay').classList.contains('hidden')"
        )
        _visit(page, handled, "#/invoices")
        page.wait_for_function("() => location.hash === '#/invoices'")
        length = page.evaluate("() => history.length")
        # a write from elsewhere must not re-run the closed report nor push
        # #/reports/profit-loss back onto the address bar (the CRITICAL bug)
        _post_journal(page, expense_id, cash_id, 250)
        page.wait_for_timeout(700)
        assert page.evaluate("() => location.hash") == "#/invoices"
        assert page.evaluate("() => history.length") == length
        assert page.evaluate(
            "() => document.getElementById('modal-overlay').classList.contains('hidden')"
        )
    finally:
        page.close()

"""Construction mode — the contractor accounts, the cost codes, and the
one-time setup the Settings page runs when a company switches.

Mirrors the nonprofit setup checks: the accounts are idempotent and yield
already-used numbers; the setup route leaves existing data alone and
changes nothing on a second call."""

from pathlib import Path

from app.models.cost_codes import STANDARD_COST_CODES
from app.services.accounting import CONSTRUCTION_ACCOUNTS

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "app/static/js"


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


def test_construction_accounts_detail_types_are_right(client, seed_accounts):
    r = client.post("/api/construction/setup")
    assert r.status_code == 200, r.text
    by_name = {a["name"]: a for a in r.json()}
    # the number-and-name rule would call these Other Current / COGS anyway,
    # but the setup pins them so the balance sheet never drifts (#431)
    assert by_name["Retainage Receivable"]["detail_type"] == "other_current_asset"
    assert by_name["Construction in Progress"]["detail_type"] == "other_current_asset"
    assert by_name["Retainage Payable"]["detail_type"] == "other_current_liability"
    assert by_name["Customer Deposits"]["detail_type"] == "other_current_liability"
    assert by_name["Use Tax Payable"]["detail_type"] == "other_current_liability"
    assert by_name["Equipment Cost"]["detail_type"] == "cost_of_goods_sold"
    assert by_name["Other Job Costs"]["detail_type"] == "cost_of_goods_sold"
    assert all(a["is_system"] for a in r.json())


def test_construction_accounts_are_idempotent_and_yield_taken_numbers(
    client, seed_accounts
):
    # 1350 is free in the seed chart; take 1150 first to prove the yield.
    r = client.post(
        "/api/accounts",
        json={
            "name": "Old Retainage",
            "account_number": "1150",
            "account_type": "asset",
        },
    )
    assert r.status_code in (200, 201), r.text

    first = client.post("/api/construction/setup")
    assert first.status_code == 200, first.text
    by_name = {a["name"]: a for a in first.json()}
    assert by_name["Retainage Receivable"]["account_number"] is None  # yielded
    assert by_name["Construction in Progress"]["account_number"] == "1350"
    assert by_name["Equipment Cost"]["account_number"] == "5400"

    # a second pass creates nothing new
    second = client.post("/api/construction/setup")
    assert [a["id"] for a in second.json()] == [a["id"] for a in first.json()]
    names = [a["name"] for a in client.get("/api/accounts").json()]
    assert names.count("Retainage Receivable") == 1


# ---------------------------------------------------------------------------
# Setup route
# ---------------------------------------------------------------------------


def test_setup_loads_accounts_and_cost_codes_and_repeats_cleanly(client, seed_accounts):
    first = client.post("/api/construction/setup")
    assert first.status_code == 200
    names = {a["name"] for a in first.json()}
    assert names == {name for _n, name, _t, _d in CONSTRUCTION_ACCOUNTS}

    codes = {c["code"] for c in client.get("/api/cost-codes").json()}
    for code, _name, _type in STANDARD_COST_CODES:
        assert code in codes, code

    # second call: same accounts, no duplicate cost codes
    accounts_before = client.get("/api/accounts").json()
    codes_before = client.get("/api/cost-codes").json()
    second = client.post("/api/construction/setup")
    assert second.status_code == 200
    assert len(client.get("/api/accounts").json()) == len(accounts_before)
    assert len(client.get("/api/cost-codes").json()) == len(codes_before)


def test_setup_creates_the_cost_types_the_codes_reference(client, seed_accounts):
    client.post("/api/construction/setup")
    rows = client.get("/api/cost-codes").json()
    types = {c["cost_type"] for c in rows}
    assert {"subcontract", "material", "labor", "equipment", "other"} <= {
        t for t in types if t
    }


# ---------------------------------------------------------------------------
# Switch points (source assertions) — the SPA wires the new type in
# ---------------------------------------------------------------------------


def test_report_center_leads_with_contractor_cards_in_construction():
    src = (JS / "reports.js").read_text(encoding="utf-8")
    assert "Terms.isConstruction()" in src
    # the three contractor reports that exist today, in issue order, lead
    assert (
        "const lead = ['job-profitability', 'job-budget-vs-actual', 'profit-loss-by-job'];"
        in src
    )


def test_nav_toggles_data_construction_and_guards_construction_routes():
    app = (JS / "app.js").read_text(encoding="utf-8")
    # toggled in the same applyTerminology pass as data-nonprofit, so the
    # NEW-145 sidebar picker excludes any construction-only page (it reads
    # only non-hidden <li>s). Phase 1 adds none; the hook is here for when
    # one lands.
    assert "$$('[data-construction]').forEach(el => { el.hidden = !con; });" in app
    assert "route.construction && !Terms.isConstruction()" in app
    assert "_constructionOnlyHtml" in app


def test_any_construction_nav_item_is_hidden_by_default():
    """NEW-145 invariant: a construction-only sidebar page must ship with
    `hidden` so the Sidebar picker leaves it out of other company types.
    Phase 1 adds none, so this guards future additions."""
    import re

    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for m in re.finditer(r"<li\b[^>]*\bdata-construction\b[^>]*>", html):
        assert "hidden" in m.group(0), m.group(0)


def test_settings_offers_construction_and_runs_setup_on_switch():
    s = (JS / "settings.js").read_text(encoding="utf-8")
    assert '<option value="construction"' in s
    # the switch posts the construction setup, as nonprofit posts setup-accounts
    assert "/construction/setup" in s


def test_vendor_form_reveals_the_sub_fields():
    v = (JS / "vendors.js").read_text(encoding="utf-8")
    assert "VendorsPage.toggleSubFields(this)" in v
    for name in ("license_number", "coi_carrier", "coi_policy_number", "coi_expires"):
        assert f'name="{name}"' in v
    # a new vendor is a 1099 vendor in construction mode by default
    assert "is_1099_vendor: Terms.isConstruction()" in v

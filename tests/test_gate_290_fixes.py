"""Regressions for the 2.9.0 release-gate findings (SlowBooks-Pro-Testing,
reports/2.9.0). Each test names the finding it pins."""

from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.models.fixed_assets import FixedAssetType


def _customer(client):
    r = client.post("/api/customers", json={"name": "Gate Donor"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---- R3: unknown fields are a 422, not a silent $0 document ---------------


def test_unknown_field_is_rejected_and_named(client):
    cid = _customer(client)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-03-01",
            "line_items": [{"description": "x", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 422, r.text
    assert "line_items" in r.text


def test_unknown_nested_field_is_rejected(client):
    cid = _customer(client)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-03-01",
            "lines": [{"description": "x", "quantity": 1, "rate": 100, "price": 5}],
        },
    )
    assert r.status_code == 422
    assert "price" in r.text


def test_every_request_body_schema_forbids_extras(client):
    """The whole surface, from the spec an agent reads: every object schema
    reachable from a request body declares additionalProperties: false.
    Settings is the one deliberate exception (its key list is data)."""
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    seen: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                if name not in seen:
                    seen.add(name)
                    walk(schemas[name])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for ops in spec["paths"].values():
        for op in ops.values():
            if isinstance(op, dict) and op.get("requestBody"):
                walk(op["requestBody"])
    lax = sorted(
        name
        for name in seen
        if schemas[name].get("type") == "object"
        and "properties" in schemas[name]
        and not name.startswith("Body_")
        and name != "SettingsUpdate"
        and schemas[name].get("additionalProperties") is not False
    )
    assert lax == [], f"request models still accept unknown fields: {lax}"


# ---- R4: tax_rate is a fraction, documented, bounded ---------------------


def test_tax_rate_percent_is_rejected_naming_the_unit(client):
    cid = _customer(client)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-03-01",
            "tax_rate": 8.9,
            "lines": [{"description": "x", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 422, r.text
    assert "fraction" in r.text and "percent" in r.text


def test_tax_rate_fraction_still_books_tax(client):
    cid = _customer(client)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-03-01",
            "tax_rate": 0.089,
            "lines": [{"description": "x", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    assert Decimal(str(r.json()["tax_amount"])) == Decimal("8.90")


@pytest.mark.parametrize(
    "schema",
    [
        "InvoiceCreate",
        "InvoiceUpdate",
        "BillCreate",
        "EstimateCreate",
        "EstimateUpdate",
        "CreditMemoCreate",
        "RecurringCreate",
        "RecurringUpdate",
        "POCreate",
        "POUpdate",
        "SalesReceiptCreate",
    ],
)
def test_tax_rate_unit_is_in_the_spec(client, schema):
    spec = client.get("/openapi.json").json()
    field = spec["components"]["schemas"][schema]["properties"]["tax_rate"]
    text = str(field)
    assert "FRACTION" in text and "default_tax_rate" in text, field
    assert "0.089" in text


def test_settings_default_tax_rate_is_documented_as_percent(client):
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/api/settings"]["get"]
    assert "percent" in (op.get("description") or "").lower()


# ---- R5: an empty pay run is refused, naming the roster ------------------


def test_empty_pay_run_is_refused(client):
    client.post(
        "/api/employees",
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "pay_type": "hourly",
            "pay_rate": 30,
        },
    )
    r = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-03-01",
            "period_end": "2026-03-15",
            "pay_date": "2026-03-20",
            "stubs": [],
        },
    )
    assert r.status_code == 422, r.text
    assert "Ada Lovelace" in r.json()["detail"]
    assert client.get("/api/payroll").json() == []


# ---- R6: PTO enums are in the spec ---------------------------------------


def test_pto_enums_in_spec(client):
    spec = client.get("/openapi.json").json()
    props = spec["components"]["schemas"]["PTOPolicyCreate"]["properties"]
    for name in ("pto_type", "accrual_method"):
        ref = props[name].get("$ref") or props[name].get("allOf", [{}])[0].get("$ref")
        assert ref, props[name]
        assert "enum" in spec["components"]["schemas"][ref.split("/")[-1]]
    r = client.post("/api/pto/policies", json={"name": "Bogus", "pto_type": "nap"})
    assert r.status_code == 422


# ---- R7: DELETE on a posted document names the void route ----------------


def test_delete_invoice_405_names_void(client):
    cid = _customer(client)
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-03-01",
            "lines": [{"description": "x", "quantity": 1, "rate": 100}],
        },
    ).json()
    r = client.delete(f"/api/invoices/{inv['id']}")
    assert r.status_code == 405
    assert r.json()["detail"] == (
        f"Posted documents are voided, not deleted: use POST /api/invoices/{inv['id']}/void"
    )
    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200


def test_plain_405_is_unchanged_without_a_void_route(client):
    r = client.delete("/api/reports/profit-loss")
    assert r.status_code == 405
    assert r.json() == {"detail": "Method Not Allowed"}


# ---- R8: depreciation works on a fresh company ---------------------------


def test_fresh_company_has_depreciation_expense_and_a_default_type(
    client, db_session, seed_accounts
):
    from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS

    assert any(a["account_number"] == "6810" for a in CHART_OF_ACCOUNTS)
    types = client.get("/api/fixed-assets/types").json()
    assert [t["name"] for t in types] == ["Equipment"]
    t = types[0]
    assert t["asset_account_id"] and t["accumulated_depreciation_account_id"]
    expense = db_session.get(Account, t["depreciation_expense_account_id"])
    assert expense.name == "Depreciation Expense"
    assert expense.account_type == AccountType.EXPENSE
    # idempotent
    assert len(client.get("/api/fixed-assets/types").json()) == 1
    assert db_session.query(FixedAssetType).count() == 1

    asset = client.post(
        "/api/fixed-assets",
        json={
            "name": "Lathe",
            "asset_type_id": t["id"],
            "purchase_date": "2026-01-01",
            "purchase_price": 6000,
        },
    )
    assert asset.status_code == 201, asset.text
    run = client.post(
        "/api/fixed-assets/run-depreciation", json={"run_date": "2026-03-31"}
    )
    assert run.status_code in (200, 201), run.text


# ---- R9: a missing manifest is logged, not silent ------------------------


def test_missing_manifest_warns_once(monkeypatch, tmp_path, caplog):
    import logging

    from app.services import company_service

    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setattr(company_service, "DATABASE_URL", "sqlite:///x.db")
    monkeypatch.setattr(company_service, "_warned_missing_manifest", False)
    with caplog.at_level(logging.WARNING, logger="app.services.company_service"):
        msg = company_service.warn_if_manifest_missing()
        assert msg and "nowhere" in msg and "SLOWBOOKS_DATA_DIR" in msg
        assert company_service.manifest_list_companies() == []
    assert sum("Company manifest not found" in r.message for r in caplog.records) == 1


# ---- R2/R2b: Save PDF attempts the write and explains a refusal ----------


def test_save_report_falls_back_when_documents_refuses(monkeypatch, tmp_path):
    import desktop_launcher

    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    data = tmp_path / "data"
    monkeypatch.setattr(desktop_launcher.Path, "home", lambda: home)
    monkeypatch.setattr(desktop_launcher, "get_data_dir", lambda: data)
    monkeypatch.setattr(desktop_launcher.sys, "platform", "darwin")

    real = desktop_launcher._write_unique

    def refusing(folder, name, payload):
        if str(folder).startswith(str(home / "Documents")):
            raise PermissionError(1, "Operation not permitted")
        return real(folder, name, payload)

    monkeypatch.setattr(desktop_launcher, "_write_unique", refusing)
    dest, note = desktop_launcher._save_report("r.pdf", b"%PDF-")
    assert dest == data / "Reports" / "r.pdf" and dest.read_bytes() == b"%PDF-"
    assert "was not allowed to write to" in note
    assert str(home / "Documents" / "SlowBooks Pro" / "Reports") in note
    assert "Files and Folders" in note

    # the reveal bridge accepts the fallback folder too
    api = desktop_launcher.PickerApi(3001)
    monkeypatch.setattr(desktop_launcher.subprocess, "Popen", lambda *a, **k: None)
    assert api.reveal_path(str(dest)) == {"success": True}


def test_save_report_prefers_documents(monkeypatch, tmp_path):
    import desktop_launcher

    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setattr(desktop_launcher.Path, "home", lambda: home)
    monkeypatch.setattr(desktop_launcher.sys, "platform", "linux")
    dest, note = desktop_launcher._save_report("r.pdf", b"a")
    again, _ = desktop_launcher._save_report("r.pdf", b"b")
    assert note is None
    assert dest == home / "Documents" / "SlowBooks Pro" / "Reports" / "r.pdf"
    assert again.name == "r (2).pdf"


def test_backup_permission_error_names_folder_and_backup(monkeypatch, tmp_path):
    import shutil

    import desktop_launcher
    from app.services import backup_service

    home = tmp_path / "home"
    home.mkdir()
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "slowbooks_20260101_000000.db").write_bytes(b"x")
    monkeypatch.setattr(desktop_launcher.Path, "home", lambda: home)
    monkeypatch.setattr(backup_service, "BACKUP_DIR", backups)
    monkeypatch.setattr(desktop_launcher.sys, "platform", "darwin")

    def refuse(*a, **k):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(shutil, "copy2", refuse)
    r = desktop_launcher.PickerApi(3001).save_backup_file(
        "slowbooks_20260101_000000.db"
    )
    assert r["success"] is False
    assert str(home / "Downloads") in r["error"]
    assert "slowbooks_20260101_000000.db" in r["error"]
    assert "Files and Folders" in r["error"]


def test_mac_bundle_declares_folder_usage_strings():
    spec = open("packaging/macos/SlowBooksPro-mac.spec").read()
    assert "NSDocumentsFolderUsageDescription" in spec
    assert "NSDownloadsFolderUsageDescription" in spec


def test_release_staples_the_app_before_the_dmg():
    src = open("packaging/macos/release.py").read()
    app_staple = src.index('_notarize(notary_zip, notary_profile, report_dir, "app")')
    assert src.index("_staple(app, report_dir)") > app_staple
    assert src.index('stage = work_dir / "dmg-stage"') > src.index(
        "_staple(app, report_dir)"
    )
    assert "_verify_dmg_contents_stapled(final_dmg" in src


def test_openapi_documents_void_over_delete(client):
    spec = client.get("/openapi.json").json()
    assert "void" in spec["info"]["description"].lower()

"""Square provider: payment-link request shape, real HMAC webhook
verification vectors, order-id invoice resolution, and polling."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.payments import get_provider, square as sq
from app.services.payments.square import SquareProvider, webhook_signature

SETTINGS = {
    "square_enabled": "true",
    "square_environment": "sandbox",
    "square_access_token": "sq-token",
    "square_location_id": "LOC123",
    "square_webhook_signature_key": "sig-key-abc",
    "square_notification_url": "https://books.example/api/payments/square/webhook",
}


def _invoice():
    return SimpleNamespace(
        id=42,
        invoice_number="INV-1042",
        balance_due=Decimal("123.45"),
        payment_token="tok-abc",
        customer=None,
        date=date(2026, 7, 1),
    )


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


# ── Registry + request shapes ────────────────────────────────────────────


def test_registry_resolves_square():
    assert isinstance(get_provider("square"), SquareProvider)


def test_payment_link_request_shape():
    req = sq.build_payment_link_request(
        _invoice(), SETTINGS, "https://books.example", idempotency_key="fixed-key"
    )
    assert req["url"] == (
        "https://connect.squareupsandbox.com/v2/online-checkout/payment-links"
    )
    assert req["headers"]["Authorization"] == "Bearer sq-token"
    assert req["headers"]["Square-Version"] == sq.SQUARE_VERSION
    body = req["json"]
    assert body["idempotency_key"] == "fixed-key"
    qp = body["quick_pay"]
    assert qp["price_money"] == {"amount": 12345, "currency": "USD"}
    assert qp["location_id"] == "LOC123"
    assert body["checkout_options"]["redirect_url"] == (
        "https://books.example/pay/tok-abc?status=success&provider=square"
    )


def test_production_environment_switches_base_url():
    prod = dict(SETTINGS, square_environment="production")
    req = sq.build_get_order_request("ORD1", prod)
    assert req["url"].startswith("https://connect.squareup.com/")


def test_checkout_stores_order_id_not_link_id(monkeypatch):
    monkeypatch.setattr(
        sq._http,
        "send",
        lambda req, **kw: FakeResponse(
            200,
            {
                "payment_link": {
                    "id": "LINK1",
                    "order_id": "ORDER77",
                    "url": "https://square.link/u/abc",
                }
            },
        ),
    )
    checkout = SquareProvider().create_checkout(
        _invoice(), SETTINGS, "https://books.example"
    )
    assert checkout.external_id == "ORDER77"  # order id, NOT the link id
    assert checkout.url == "https://square.link/u/abc"


# ── Webhook: real HMAC vectors ───────────────────────────────────────────


def _event_body():
    import json

    return json.dumps(
        {
            "type": "payment.updated",
            "data": {
                "object": {
                    "payment": {
                        "status": "COMPLETED",
                        "order_id": "ORDER77",
                        "amount_money": {"amount": 12345, "currency": "USD"},
                    }
                }
            },
        }
    ).encode()


def test_webhook_valid_signature_yields_paid_result():
    body = _event_body()
    sig = webhook_signature(
        SETTINGS["square_webhook_signature_key"],
        SETTINGS["square_notification_url"],
        body,
    )
    result = SquareProvider().verify_webhook(
        body, {"x-square-hmacsha256-signature": sig}, SETTINGS
    )
    assert result.status == "paid"
    assert result.external_id == "ORDER77"
    assert result.amount == Decimal("123.45")
    assert result.invoice_id is None  # resolved by checkout_external_id


def test_webhook_tampered_body_rejected():
    body = _event_body()
    sig = webhook_signature(
        SETTINGS["square_webhook_signature_key"],
        SETTINGS["square_notification_url"],
        body,
    )
    tampered = body.replace(b"12345", b"99999")
    with pytest.raises(ValueError):
        SquareProvider().verify_webhook(
            tampered, {"x-square-hmacsha256-signature": sig}, SETTINGS
        )


def test_webhook_wrong_key_rejected():
    body = _event_body()
    sig = webhook_signature("wrong-key", SETTINGS["square_notification_url"], body)
    with pytest.raises(ValueError):
        SquareProvider().verify_webhook(
            body, {"x-square-hmacsha256-signature": sig}, SETTINGS
        )


def test_webhook_missing_signature_rejected():
    with pytest.raises(ValueError):
        SquareProvider().verify_webhook(_event_body(), {}, SETTINGS)


def test_webhook_requires_notification_url():
    settings = dict(SETTINGS, square_notification_url="")
    with pytest.raises(ValueError, match="square_notification_url"):
        SquareProvider().verify_webhook(_event_body(), {}, settings)


def test_webhook_non_completed_payment_ignored():
    import json

    body = json.dumps(
        {
            "type": "payment.updated",
            "data": {"object": {"payment": {"status": "PENDING", "order_id": "O"}}},
        }
    ).encode()
    sig = webhook_signature(
        SETTINGS["square_webhook_signature_key"],
        SETTINGS["square_notification_url"],
        body,
    )
    result = SquareProvider().verify_webhook(
        body, {"x-square-hmacsha256-signature": sig}, SETTINGS
    )
    assert result is None


# ── Polling ──────────────────────────────────────────────────────────────


def test_poll_completed_order(monkeypatch):
    monkeypatch.setattr(
        sq._http,
        "send",
        lambda req, **kw: FakeResponse(
            200,
            {
                "order": {
                    "id": "ORDER77",
                    "state": "COMPLETED",
                    "total_money": {"amount": 12345, "currency": "USD"},
                }
            },
        ),
    )
    result = SquareProvider().poll_status("ORDER77", SETTINGS)
    assert result.status == "paid"
    assert result.amount == Decimal("123.45")


def test_poll_open_order_pending(monkeypatch):
    monkeypatch.setattr(
        sq._http,
        "send",
        lambda req, **kw: FakeResponse(200, {"order": {"id": "O", "state": "OPEN"}}),
    )
    assert SquareProvider().poll_status("O", SETTINGS).status == "pending"


# ── Invoice resolution through the webhook route ─────────────────────────


def test_webhook_route_resolves_invoice_by_stored_order_id(
    client, db_session, seed_accounts, monkeypatch
):
    """End-to-end: Square webhook carries only the order id; the route
    resolves the invoice via checkout_external_id and records payment."""
    from app.models.contacts import Customer
    from app.models.invoices import Invoice, InvoiceStatus
    from app.models.payments import Payment
    from app.services.settings_service import set_setting

    for key, value in SETTINGS.items():
        set_setting(db_session, key, value)
    db_session.commit()

    customer = Customer(name="Sq Customer", is_active=True)
    db_session.add(customer)
    db_session.flush()
    inv = Invoice(
        invoice_number="SQ-1001",
        customer_id=customer.id,
        date=date(2026, 7, 1),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("123.45"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("123.45"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("123.45"),
        payment_token="tok-sq-1001",
        checkout_provider="square",
        checkout_external_id="ORDER77",
    )
    db_session.add(inv)
    db_session.commit()

    body = _event_body()
    sig = webhook_signature(
        SETTINGS["square_webhook_signature_key"],
        SETTINGS["square_notification_url"],
        body,
    )
    resp = client.post(
        "/api/payments/square/webhook",
        content=body,
        headers={"x-square-hmacsha256-signature": sig},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "payment_recorded"

    db_session.refresh(inv)
    assert inv.status == InvoiceStatus.PAID
    payment = db_session.query(Payment).filter_by(reference="ORDER77").one()
    assert payment.method == "square"
    assert payment.amount == Decimal("123.45")

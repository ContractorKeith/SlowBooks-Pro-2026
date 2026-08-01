# Square Online Payments Setup

Accept Square payments on invoices alongside Stripe and/or PayPal. When Square is enabled, the public invoice pay page shows a **Pay with Square** button; the customer pays on Square's hosted page and the payment auto-records in Slowbooks with the standard journal entries (DR Undeposited Funds / CR Accounts Receivable).

See `docs/setup-stripe.md` and `docs/setup-paypal.md` for the other providers — any combination can be enabled.

---

## Prerequisites

- A Square account ([squareup.com](https://squareup.com))
- Slowbooks Pro running and accessible

---

## Step 1: Get sandbox credentials

1. Log in to the [Square Developer Dashboard](https://developer.squareup.com/apps)
2. Create an application (e.g. "Slowbooks")
3. In the app's **Sandbox** tab, copy:
   - **Sandbox Access Token**
   - A **Location ID** from Sandbox Locations

## Step 2: Configure Slowbooks

1. Open **Company Settings → Online Payments → Square**
2. Set **Square Payments** to Enabled and **Environment** to `Sandbox`
3. Paste the **Access Token** and **Location ID**
4. Save

## Step 3 (server installs): Add a webhook

Skip this on desktop installs — webhooks can't reach `127.0.0.1`; payments record when the customer returns from Square, or via the **Check Payment Status** button on the invoice.

1. In the developer dashboard: your app → **Webhooks → Subscriptions → Add subscription**
2. URL: `https://your-server/api/payments/square/webhook`
3. Subscribe to **payment.updated**
4. Copy the subscription's **Signature Key** into Slowbooks
5. **Important:** paste the exact same URL into Slowbooks' **Webhook Notification URL** field — Square signs each delivery over that exact URL string, so verification fails if they differ (e.g. behind a proxy).

## Step 4: Test in sandbox

1. In Slowbooks, open an unpaid invoice → **Copy Payment Link** → open in a private window
2. Click **Pay with Square** and pay with the sandbox test card `4111 1111 1111 1111` (any future expiry, any CVV, any ZIP)
3. On return you should see "Payment received — thank you!"
4. Verify: invoice Paid, payment row (method `square`), journal entry posted

## Going live

Switch to the app's **Production** tab, copy the production Access Token and a real Location ID, set **Environment** to `Production`, and (server installs) recreate the webhook subscription in production mode with its own signature key.

## Notes

- Amounts are charged in USD for the invoice's balance due at checkout time.
- Square payment links carry no metadata, so Slowbooks matches payments to invoices by the **order id** stored when the checkout was created — don't reuse one payment link across invoices.
- Refunds are issued from the Square dashboard; record them in Slowbooks as a credit memo.

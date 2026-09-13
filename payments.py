import os
import hmac
import hashlib
import httpx

PLANS = {
    "starter": {"name": "Starter", "usd": 99, "inr": 8199, "credits": 3000},
    "growth": {"name": "Growth", "usd": 199, "inr": 16499, "credits": 8000},
    "enterprise": {"name": "Enterprise", "usd": 499, "inr": 41499, "credits": 25000, "custom_above": True},
}

# Given to every new signup before they choose a paid plan. Not sold, not shown on
# the pricing page, and intentionally capped — see models.User default plan/credits.
TRIAL_CREDITS = 50

# ---------------- Razorpay ----------------
def get_razorpay_client():
    import razorpay
    key_id = os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")
    if not key_id or not key_secret:
        raise RuntimeError("Razorpay keys not configured (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET).")
    return razorpay.Client(auth=(key_id, key_secret))


def razorpay_create_order(amount_inr: float, receipt: str) -> dict:
    client = get_razorpay_client()
    order = client.order.create(
        {
            "amount": int(amount_inr * 100),  # paise
            "currency": "INR",
            "receipt": receipt,
            "payment_capture": 1,
        }
    )
    return order


def razorpay_verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    body = f"{order_id}|{payment_id}"
    expected = hmac.new(key_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------- Stripe ----------------
def stripe_create_checkout_session(plan_key: str, success_url: str, cancel_url: str) -> dict:
    import stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    if not stripe.api_key:
        raise RuntimeError("Stripe key not configured (STRIPE_SECRET_KEY).")
    plan = PLANS[plan_key]
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": f"Kestrel AI — {plan['name']} plan"},
                    "unit_amount": int(plan["usd"] * 100),
                    "recurring": {"interval": "month"},
                },
                "quantity": 1,
            }
        ],
        mode="subscription",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"plan": plan_key},
    )
    return {"id": session.id, "url": session.url}


# ---------------- PayPal ----------------
async def paypal_get_access_token() -> str:
    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")
    base = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    if not client_id or not client_secret:
        raise RuntimeError("PayPal keys not configured (PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET).")
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{base}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def paypal_create_order(plan_key: str) -> dict:
    plan = PLANS[plan_key]
    base = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    token = await paypal_get_access_token()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{base}/v2/checkout/orders",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "amount": {"currency_code": "USD", "value": str(plan["usd"])},
                        "description": f"Kestrel AI — {plan['name']} plan",
                    }
                ],
            },
        )
        resp.raise_for_status()
        return resp.json()


async def paypal_capture_order(order_id: str) -> dict:
    base = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    token = await paypal_get_access_token()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{base}/v2/checkout/orders/{order_id}/capture",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()

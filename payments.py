import os
import hmac
import hashlib

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

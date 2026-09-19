import os
import hmac
import hashlib

PLANS = {
    "solo": {
        "name": "Solo", "usd": 49, "inr": 4059, "credits": 1200,
        "features": ["Up to 1 active workflow", "All 16 agents, standard usage", "Community support"],
    },
    "starter": {
        "name": "Starter", "usd": 99, "inr": 8199, "credits": 3000,
        "features": ["Up to 3 active workflows", "All 16 agents, standard usage", "Email support"],
    },
    "growth": {
        "name": "Growth", "usd": 199, "inr": 16499, "credits": 8000,
        "features": ["Unlimited workflows", "All agents, full usage", "CSV/Excel data analysis", "Priority support"],
    },
    "scale": {
        "name": "Scale", "usd": 349, "inr": 28899, "credits": 15000,
        "features": ["Everything in Growth", "AI Page Builder included", "Higher rate limits", "Priority support"],
    },
    "pro": {
        "name": "Pro", "usd": 499, "inr": 41499, "credits": 25000,
        "features": ["Everything in Scale", "Dedicated onboarding", "Custom agent tuning", "SLA support"],
    },
    "enterprise": {
        "name": "Enterprise", "usd": 999, "inr": 82899, "credits": 60000, "custom_above": True,
        "features": ["Everything in Pro", "Custom credit limits above 60,000/month", "Dedicated account manager", "SLA + priority support"],
    },
}

# Add-ons: separate recurring subscriptions layered on top of any plan above,
# rather than a credit tier of their own. Renews monthly, tracked via
# User.has_voice_addon / voice_addon_expires rather than User.plan/credits.
ADDONS = {
    "voice_assistant": {
        "name": "Voice Assistant Add-on", "usd": 29, "inr": 2415,
        "description": "Multilingual voice mode for chatting with any agent by speaking instead of typing.",
    },
}

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

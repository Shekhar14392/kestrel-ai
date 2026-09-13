import os
import httpx

# ---- Agent registry: industry-specific personas ----
AGENTS = {
    "general": {
        "name": "Kestrel",
        "role": "General AI Assistant",
        "system": "You are Kestrel, a sharp, helpful general-purpose business AI assistant. Be concise and practical.",
    },
    "sales": {
        "name": "Orion",
        "role": "Sales & Lead Qualification Agent",
        "system": "You are Orion, an AI sales agent. Qualify leads, answer product questions persuasively but honestly, "
                   "identify buying intent, and always propose a clear next step (demo, call, or pricing info).",
    },
    "support": {
        "name": "Iris",
        "role": "Customer Support Agent",
        "system": "You are Iris, a calm and precise customer support agent. Diagnose the issue, give clear step-by-step "
                   "fixes, and escalate politely to a human when something is billing-sensitive or unresolved.",
    },
    "ecommerce": {
        "name": "Marlow",
        "role": "E-commerce Assistant",
        "system": "You are Marlow, an e-commerce AI assistant. Help with product recommendations, order status "
                   "questions, returns policy, and upsells, in a friendly retail tone.",
    },
    "realestate": {
        "name": "Everly",
        "role": "Real Estate Agent Assistant",
        "system": "You are Everly, a real-estate AI assistant. Help qualify buyer/renter intent (budget, location, "
                   "timeline), summarize property details, and schedule viewings.",
    },
    "finance": {
        "name": "Sterling",
        "role": "Finance & Accounting Assistant",
        "system": "You are Sterling, a finance AI assistant for small businesses. Explain invoices, cash flow, and "
                   "basic bookkeeping questions clearly. Always add a short disclaimer that you are not a licensed "
                   "financial advisor for anything resembling investment or tax advice.",
    },
    "healthcare_ops": {
        "name": "Wren",
        "role": "Healthcare Front-Office Assistant",
        "system": "You are Wren, a front-office assistant for clinics: appointment scheduling, insurance/billing "
                   "questions, and general clinic info. Never give medical diagnoses or treatment advice — redirect "
                   "clinical questions to a licensed professional.",
    },
    "legal_ops": {
        "name": "Callum",
        "role": "Legal & Compliance Front-Office Assistant",
        "system": "You are Callum, a front-office assistant for a legal practice or compliance team: intake "
                   "questions, document checklist status, appointment scheduling, and plain-language explanations "
                   "of process and next steps. Never give legal advice or interpret a specific contract or case — "
                   "redirect substantive legal questions to a licensed attorney.",
    },
    "hr_recruiting": {
        "name": "Priya",
        "role": "HR & Recruiting Assistant",
        "system": "You are Priya, an HR and recruiting AI assistant. Screen candidates against a role's stated "
                   "requirements, answer applicant FAQs (process, timeline, benefits basics), and draft interview "
                   "scheduling messages. Stay neutral and consistent across candidates, and avoid asking about or "
                   "commenting on protected characteristics (age, gender, religion, disability, etc.).",
    },
    "hospitality_travel": {
        "name": "Reeve",
        "role": "Hospitality & Travel Assistant",
        "system": "You are Reeve, an AI assistant for hotels, short-term rentals, and travel bookings. Handle "
                   "availability questions, booking changes, local recommendations, and check-in/check-out logistics "
                   "in a warm, concierge-style tone.",
    },
    "education_admissions": {
        "name": "Isla",
        "role": "Education & Admissions Assistant",
        "system": "You are Isla, an AI assistant for schools, courses, and training programs. Answer questions about "
                   "curriculum, enrollment steps, deadlines, and fees, and help prospective students figure out if a "
                   "program fits their goals. Be encouraging but accurate — never guarantee admission or outcomes.",
    },
    "logistics_supply": {
        "name": "Dax",
        "role": "Logistics & Supply Chain Assistant",
        "system": "You are Dax, an AI assistant for logistics and supply chain operations: shipment status "
                   "questions, delay explanations, delivery scheduling, and vendor/customer updates. Be precise "
                   "about timelines and clear when information is an estimate versus a confirmed fact.",
    },
    "marketing_agency": {
        "name": "Fenn",
        "role": "Marketing & Influencer Agency Assistant",
        "system": "You are Fenn, an AI assistant for a marketing or influencer agency. Help with campaign brief "
                   "questions, creator/client outreach drafts, content calendar status, and basic performance "
                   "summaries. Keep a confident, agency-voice tone and flag when a request needs a human strategist's "
                   "sign-off (contracts, pricing negotiations, brand-sensitive messaging).",
    },
}


async def call_llm(system_prompt: str, messages: list[dict]) -> str:
    """
    Multi-provider adapter. Tries Anthropic first (ANTHROPIC_API_KEY), falls back to
    OpenAI (OPENAI_API_KEY), then Gemini (GEMINI_API_KEY). Raises if none configured.
    """
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")

    if anthropic_key:
        return await _call_anthropic(anthropic_key, system_prompt, messages)
    if openai_key:
        return await _call_openai(openai_key, system_prompt, messages)
    if gemini_key:
        return await _call_gemini(gemini_key, system_prompt, messages)
    raise RuntimeError(
        "No LLM provider configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY."
    )


async def _call_anthropic(api_key: str, system_prompt: str, messages: list[dict]) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": messages,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


async def _call_openai(api_key: str, system_prompt: str, messages: list[dict]) -> str:
    oi_messages = [{"role": "system", "content": system_prompt}] + [
        {"role": m["role"], "content": m["content"]} for m in messages
    ]
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini", "messages": oi_messages, "max_tokens": 1024},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def _call_gemini(api_key: str, system_prompt: str, messages: list[dict]) -> str:
    contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in messages]
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            json={"system_instruction": {"parts": [{"text": system_prompt}]}, "contents": contents},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

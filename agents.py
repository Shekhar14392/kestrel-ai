import os
import httpx

# ---- Agent registry: industry-specific personas ----
AGENTS = {
    "tara": {
        "name": "Tara",
        "role": "Customer & Client Experience Assistant",
        "system": "You are Tara, a warm, sharp AI assistant handling customer-facing work: sales and lead "
                   "qualification, support troubleshooting, e-commerce questions (orders, returns, upsells), "
                   "real estate and hospitality inquiries, and education/admissions questions. Diagnose what the "
                   "person actually needs, give clear next steps, and always identify buying or booking intent when "
                   "relevant. Never give medical diagnoses or legal advice — redirect those to a licensed "
                   "professional. Escalate billing-sensitive or unresolved issues to a human.",
        "avatar_svg": """<svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg" class="agent-avatar-svg">
  <circle cx="100" cy="100" r="96" fill="#F6F4EE" stroke="#C9A227" stroke-width="3"/>
  <path d="M45 150 Q45 95 100 95 Q155 95 155 150 L155 175 L45 175 Z" fill="#123039"/>
  <path d="M62 78 Q62 32 100 32 Q138 32 138 78 Q138 92 130 105 Q120 118 100 118 Q80 118 70 105 Q62 92 62 78 Z" fill="#E7B27B"/>
  <path d="M60 70 Q55 30 100 26 Q148 30 142 72 Q140 55 118 48 Q100 60 78 48 Q62 55 60 70 Z" fill="#3B2415"/>
  <g class="agent-blink">
    <ellipse cx="84" cy="80" rx="5" ry="6" fill="#123039"/>
    <ellipse cx="116" cy="80" rx="5" ry="6" fill="#123039"/>
  </g>
  <path d="M88 102 Q100 110 112 102" stroke="#8A4B32" stroke-width="3" fill="none" stroke-linecap="round"/>
  <path d="M76 150 Q100 138 124 150" stroke="#C9A227" stroke-width="4" fill="none" stroke-linecap="round"/>
</svg>""",
    },
    "ved": {
        "name": "Ved",
        "role": "Operations & Business Advisor",
        "system": "You are Ved, a precise, practical AI assistant handling operations and back-office work: "
                   "finance and bookkeeping questions, HR and recruiting screening, legal/compliance intake, "
                   "logistics and supply chain status, marketing and agency campaign support, and general AI "
                   "business-opportunity advice (concrete service ideas, launch plans, pricing, demo pitches). "
                   "Be concrete, not motivational filler. Add a short disclaimer before anything resembling "
                   "financial, tax, or legal advice, and flag when something needs a licensed professional or a "
                   "human's sign-off (contracts, pricing negotiations).",
        "avatar_svg": """<svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg" class="agent-avatar-svg">
  <circle cx="100" cy="100" r="96" fill="#F6F4EE" stroke="#4E8B6C" stroke-width="3"/>
  <path d="M45 152 Q45 97 100 97 Q155 97 155 152 L155 175 L45 175 Z" fill="#1C2B33"/>
  <path d="M64 76 Q64 30 100 30 Q136 30 136 76 Q136 90 128 103 Q118 116 100 116 Q82 116 72 103 Q64 90 64 76 Z" fill="#C98A5E"/>
  <path d="M62 68 Q58 26 100 24 Q142 26 138 68 Q138 50 100 44 Q62 50 62 68 Z" fill="#20201E"/>
  <path d="M70 108 Q80 122 100 122 Q120 122 130 108 L130 118 Q118 130 100 130 Q82 130 70 118 Z" fill="#20201E"/>
  <g class="agent-blink">
    <ellipse cx="86" cy="80" rx="5" ry="6" fill="#1C2B33"/>
    <ellipse cx="114" cy="80" rx="5" ry="6" fill="#1C2B33"/>
  </g>
  <path d="M90 102 Q100 107 110 102" stroke="#7A4A2E" stroke-width="3" fill="none" stroke-linecap="round"/>
  <path d="M76 150 Q100 140 124 150" stroke="#4E8B6C" stroke-width="4" fill="none" stroke-linecap="round"/>
</svg>""",
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

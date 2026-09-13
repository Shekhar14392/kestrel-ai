# Kestrel AI

A no-code AI agent platform: industry-specific agents, a visual workflow builder,
a live dashboard, CSV/Excel data analysis, and built-in billing via Razorpay, Stripe,
and PayPal.

"Kestrel AI" is a working name — rename it freely (search/replace "Kestrel AI" across
`templates/` and update `render.yaml`'s service name).

## What's actually inside

- **Auth** — email/password signup & login, JWT in an httpOnly cookie.
- **13 industry agents** — general, sales, support, e-commerce, real estate, finance,
  clinic front-office, legal/compliance front-office, HR & recruiting, hospitality &
  travel, education & admissions, logistics & supply chain, and marketing/influencer
  agency — each with its own system prompt (`agents.py`). Add more by adding a key to
  the `AGENTS` dict; it shows up in chat, the workflow builder, and the landing page
  automatically.
- **Multi-provider LLM adapter** — uses Anthropic if `ANTHROPIC_API_KEY` is set,
  falls back to OpenAI, then Gemini. You only need one key configured.
- **No-code workflow builder** — a real drag-and-drop canvas (`/workflows`): trigger →
  agent → condition (branching) → webhook/action → delay nodes, connected visually,
  saved as JSON, and executed server-side (`workflows.py`).
- **Live dashboard** — polls `/api/metrics` every 8s for run counts, success rate, and
  a stacked bar chart of recent activity (Chart.js).
- **Data analysis** — upload a `.csv`/`.xls`/`.xlsx`, get row/column counts, numeric
  column stats, and a preview table (pandas).
- **Payments** — Razorpay (Orders API + client-side Checkout.js + signature
  verification), Stripe (Checkout Sessions, subscription mode), and PayPal (Orders v2,
  sandbox by default). Successful payment upgrades the user's plan and credit balance.
- **Pricing** — three paid tiers, priced in USD, each with a defined (non-unlimited)
  monthly credit limit: Starter $99/mo (3,000 credits), Growth $199/mo (8,000
  credits), Enterprise $499/mo (25,000 credits, higher limits available on request).
  New signups get a 50-credit trial (`plan="trial"` in `models.py`) to explore the
  product before choosing a plan — the trial isn't sold or shown on the pricing page.
  Everything lives in `payments.py`'s `PLANS` dict; the pricing page and Razorpay/
  Stripe/PayPal checkout all read from that one source, so there's only one place to
  change numbers. Razorpay charges the INR equivalent (`inr` field); Stripe and
  PayPal charge the USD amount directly.

## What is NOT included (be realistic about this)

This is a strong, genuinely working MVP — not a clone of Abacus.AI's actual
infrastructure. It does **not** include: 100+ bundled model integrations (you get
whichever provider(s) you configure keys for), AI video generation, a general-purpose
"build any app from a prompt" agent, or a background job queue for long-running
workflow steps (the `delay` node currently just logs the delay rather than pausing
execution — wiring in a real queue like Celery/RQ or Render Cron is a next step if
you need long-running workflows).

## Local setup (do this from a machine with terminal access, or Replit/Render directly)

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in at least one LLM key
uvicorn main:app --reload
```

Visit `http://localhost:8000`.

## Deploying on Render (matches your existing stack)

1. Push this folder to a new GitHub repo.
2. In Render: **New → Blueprint**, point it at the repo. `render.yaml` is already
   configured — it will create one web service.
3. After the first deploy, go to the service's **Environment** tab and fill in:
   - `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` / `GEMINI_API_KEY`) — required for
     agents/chat/workflows to respond.
   - `DATABASE_URL` — optional; leave unset for SQLite (fine for testing, but Render's
     disk is ephemeral on redeploy — point this at a Supabase Postgres connection
     string for anything real).
   - Razorpay / Stripe / PayPal keys as you get them.
4. Redeploy after adding env vars so the service picks them up.

### Razorpay specifically
- Sign up at https://dashboard.razorpay.com, complete KYC (this step is unavoidable
  and happens on Razorpay's side, not something any code can skip).
- Use **test mode** keys first (`rzp_test_...`) to confirm checkout works end-to-end
  before switching to live keys.

### Stripe / PayPal
- Stripe: use a test secret key (`sk_test_...`) first.
- PayPal: sandbox is the default (`PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com`);
  switch to `https://api-m.paypal.com` with live credentials when ready to charge
  real cards.

## Editing pricing or agents

- Prices/credits: `payments.py` → `PLANS` dict.
- Agents/personas: `agents.py` → `AGENTS` dict. Add a new key and it automatically
  appears in the chat page, the workflow builder's agent dropdown, and the landing
  page's agent strip.

## Known mobile-workflow notes

- Editing files directly through GitHub's mobile web editor has corrupted files in
  past projects — prefer uploading the whole folder as a zip via GitHub's "Add file →
  Upload files" instead of editing individual files in-browser.
- SQLite works out of the box for a quick live demo, but switch to your Supabase
  Postgres instance (`DATABASE_URL`) before taking real signups/payments, since
  Render's free-tier disk does not persist across deploys.

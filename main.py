import os
import json
import io
import datetime as dt
from fastapi import FastAPI, Depends, Request, HTTPException, UploadFile, File, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
import pandas as pd

from database import Base, engine, get_db
from models import User, Workflow, WorkflowRun, ChatMessage, Payment, GeneratedPage, SiteSettings
from auth import hash_password, verify_password, create_token, get_current_user, get_current_user_optional, get_current_admin, is_configured_admin_email
from agents import AGENTS, call_llm, call_gemini_required, PAGE_BUILDER_SYSTEM_PROMPT
from workflows import run_workflow
import payments as pay
import re
import secrets

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Kestrel AI")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------- Landing / Pricing
def get_site_settings(db: Session) -> SiteSettings:
    settings = db.query(SiteSettings).filter(SiteSettings.id == "default").first()
    if not settings:
        settings = SiteSettings(id="default")
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@app.get("/", response_class=HTMLResponse)
def landing(request: Request, user: User = Depends(get_current_user_optional), db: Session = Depends(get_db)):
    settings = get_site_settings(db)
    return templates.TemplateResponse(
        "index.html", {"request": request, "user": user, "plans": pay.PLANS, "agents": AGENTS, "seo": settings, "addons": pay.ADDONS}
    )


@app.get("/robots.txt", response_class=Response)
def robots_txt(request: Request):
    base = str(request.base_url).rstrip("/")
    body = f"User-agent: *\nAllow: /\nDisallow: /dashboard\nDisallow: /admin\nDisallow: /chat\nDisallow: /workflows\nDisallow: /page-builder\nDisallow: /data-analysis\nDisallow: /welcome\n\nSitemap: {base}/sitemap.xml\n"
    return Response(content=body, media_type="text/plain")


@app.get("/sitemap.xml", response_class=Response)
def sitemap_xml(request: Request):
    base = str(request.base_url).rstrip("/")
    urls = ["/", "/pricing", "/login", "/signup"]
    items = "".join(f"<url><loc>{base}{u}</loc></url>" for u in urls)
    body = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'
    return Response(content=body, media_type="application/xml")


# ---------------------------------------------------------------- Auth pages
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("auth.html", {"request": request, "mode": "login"})


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return templates.TemplateResponse("auth.html", {"request": request, "mode": "signup"})


@app.post("/api/signup")
def signup(payload: dict, response: Response, db: Session = Depends(get_db)):
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    full_name = payload.get("full_name", "")
    company = payload.get("company", "")
    if not email or not password or len(password) < 6:
        raise HTTPException(400, "Valid email and a password of at least 6 characters are required.")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "An account with this email already exists.")
    user = User(email=email, hashed_password=hash_password(password), full_name=full_name, company=company,
                is_admin=is_configured_admin_email(email))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "An account with this email already exists.")
    db.refresh(user)
    token = create_token(user.id)
    response = JSONResponse({"ok": True, "redirect": "/welcome"})
    response.set_cookie("kestrel_token", token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax")
    return response


@app.post("/api/login")
def login(payload: dict, db: Session = Depends(get_db)):
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(401, "Incorrect email or password.")
    if is_configured_admin_email(email) and not user.is_admin:
        user.is_admin = True
        db.commit()
    token = create_token(user.id)
    response = JSONResponse({"ok": True, "redirect": "/dashboard"})
    response.set_cookie("kestrel_token", token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax")
    return response


@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True, "redirect": "/"})
    response.delete_cookie("kestrel_token")
    return response


# ---------------------------------------------------------------- Welcome / Thank you
@app.get("/welcome", response_class=HTMLResponse)
def welcome(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("welcome.html", {"request": request, "user": user, "agents": AGENTS})


@app.get("/thank-you", response_class=HTMLResponse)
def thank_you(request: Request, user: User = Depends(get_current_user)):
    plan_key = request.query_params.get("plan")
    gateway = request.query_params.get("gateway", "")
    plan = pay.PLANS.get(plan_key)
    return templates.TemplateResponse(
        "thank_you.html", {"request": request, "user": user, "plan": plan, "gateway": gateway}
    )




# ---------------------------------------------------------------- Dashboard
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    workflows = db.query(Workflow).filter(Workflow.owner_id == user.id).all()
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "user": user, "workflows": workflows, "plans": pay.PLANS}
    )


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    total_users = db.query(User).count()
    users_by_plan = dict(db.query(User.plan, func.count(User.id)).group_by(User.plan).all())

    total_revenue = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status == "paid").scalar()
    revenue_by_plan = dict(
        db.query(Payment.plan, func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.status == "paid").group_by(Payment.plan).all()
    )
    payment_count = db.query(Payment).filter(Payment.status == "paid").count()

    messages_by_agent = dict(
        db.query(ChatMessage.agent_key, func.count(ChatMessage.id))
        .filter(ChatMessage.role == "user").group_by(ChatMessage.agent_key).all()
    )
    total_messages = db.query(ChatMessage).filter(ChatMessage.role == "user").count()

    recent_signups = db.query(User).order_by(User.created_at.desc()).limit(10).all()
    recent_payments = db.query(Payment).filter(Payment.status == "paid").order_by(Payment.created_at.desc()).limit(10).all()
    recent_chats = (
        db.query(ChatMessage, User)
        .join(User, ChatMessage.user_id == User.id)
        .filter(ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.desc())
        .limit(20)
        .all()
    )

    seo = get_site_settings(db)

    provider_status = [
        {"name": "Anthropic (Claude)", "env_var": "ANTHROPIC_API_KEY", "configured": bool(os.getenv("ANTHROPIC_API_KEY")), "free_tier": False},
        {"name": "OpenAI (ChatGPT)", "env_var": "OPENAI_API_KEY", "configured": bool(os.getenv("OPENAI_API_KEY")), "free_tier": False},
        {"name": "Gemini", "env_var": "GEMINI_API_KEY", "configured": bool(os.getenv("GEMINI_API_KEY")), "free_tier": True},
        {"name": "Groq", "env_var": "GROQ_API_KEY", "configured": bool(os.getenv("GROQ_API_KEY")), "free_tier": True},
        {"name": "Mistral", "env_var": "MISTRAL_API_KEY", "configured": bool(os.getenv("MISTRAL_API_KEY")), "free_tier": True},
    ]
    configured_count = sum(1 for p in provider_status if p["configured"])

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "user": admin,
            "agents": AGENTS,
            "total_users": total_users,
            "users_by_plan": users_by_plan,
            "total_revenue": total_revenue,
            "revenue_by_plan": revenue_by_plan,
            "payment_count": payment_count,
            "messages_by_agent": messages_by_agent,
            "total_messages": total_messages,
            "recent_signups": recent_signups,
            "recent_payments": recent_payments,
            "recent_chats": recent_chats,
            "seo": seo,
            "provider_status": provider_status,
            "configured_count": configured_count,
        },
    )


@app.post("/api/admin/seo")
def update_seo(payload: dict, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    settings = get_site_settings(db)
    title = payload.get("meta_title", "").strip()
    description = payload.get("meta_description", "").strip()
    keywords = payload.get("meta_keywords", "").strip()
    og_image = payload.get("og_image_url", "").strip()

    if not title or not description:
        raise HTTPException(400, "Title and description can't be empty.")
    if len(title) > 70:
        raise HTTPException(400, "Title should be 70 characters or fewer for search results.")
    if len(description) > 160:
        raise HTTPException(400, "Description should be 160 characters or fewer for search results.")

    settings.meta_title = title
    settings.meta_description = description
    settings.meta_keywords = keywords
    settings.og_image_url = og_image
    db.commit()
    return {"ok": True}


@app.get("/api/metrics")
def metrics(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wf_ids = [w.id for w in db.query(Workflow).filter(Workflow.owner_id == user.id).all()]
    runs = db.query(WorkflowRun).filter(WorkflowRun.workflow_id.in_(wf_ids)).order_by(WorkflowRun.started_at.desc()).limit(50).all()
    total_runs = len(runs)
    success = len([r for r in runs if r.status == "success"])
    failed = len([r for r in runs if r.status == "failed"])
    msgs = db.query(ChatMessage).filter(ChatMessage.user_id == user.id).count()

    by_day = {}
    for r in runs:
        day = r.started_at.strftime("%Y-%m-%d")
        by_day.setdefault(day, {"success": 0, "failed": 0})
        by_day[day]["success" if r.status == "success" else "failed"] += 1 if r.status in ("success", "failed") else 0

    return JSONResponse(
        {
            "total_runs": total_runs,
            "success": success,
            "failed": failed,
            "chat_messages": msgs,
            "credits_remaining": user.credits_remaining,
            "plan": user.plan,
            "timeline": [{"day": k, **v} for k, v in sorted(by_day.items())],
            "recent_runs": [
                {
                    "id": r.id,
                    "workflow_id": r.workflow_id,
                    "status": r.status,
                    "duration_ms": r.duration_ms,
                    "started_at": r.started_at.isoformat(),
                }
                for r in runs[:10]
            ],
        }
    )


# ---------------------------------------------------------------- Chat / Agents
def voice_addon_active(user: User) -> bool:
    if user.is_admin:
        return True
    return bool(user.has_voice_addon and user.voice_addon_expires and user.voice_addon_expires > dt.datetime.utcnow())


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse(
        "chat.html", {"request": request, "user": user, "agents": AGENTS, "voice_active": voice_addon_active(user), "voice_addon": pay.ADDONS["voice_assistant"]}
    )


@app.get("/api/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pages_built = db.query(GeneratedPage).filter(GeneratedPage.owner_id == user.id).count()
    workflow_count = db.query(Workflow).filter(Workflow.owner_id == user.id).count()
    plan_info = pay.PLANS.get(user.plan, {})
    return {
        "full_name": user.full_name,
        "email": user.email,
        "plan": user.plan,
        "plan_name": plan_info.get("name", user.plan),
        "credits_remaining": user.credits_remaining,
        "is_admin": user.is_admin,
        "voice_active": voice_addon_active(user),
        "pages_built": pages_built,
        "workflow_count": workflow_count,
    }


@app.post("/api/chat/{agent_key}")
async def chat_send(agent_key: str, payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.is_admin and user.credits_remaining <= 0:
        raise HTTPException(402, "Out of credits. Please upgrade your plan.")
    agent = AGENTS.get(agent_key, AGENTS["tara"])
    text = payload.get("message", "")
    if not text.strip():
        raise HTTPException(400, "Message cannot be empty.")

    db.add(ChatMessage(user_id=user.id, agent_key=agent_key, role="user", content=text))
    db.commit()
    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == user.id, ChatMessage.agent_key == agent_key)
        .order_by(ChatMessage.created_at.desc())
        .limit(10)
        .all()
    )
    history.reverse()
    msg_payload = [{"role": h.role, "content": h.content} for h in history] + [{"role": "user", "content": text}]

    try:
        reply = await call_llm(agent["system"], msg_payload)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))

    db.add(ChatMessage(user_id=user.id, agent_key=agent_key, role="assistant", content=reply))
    if not user.is_admin:
        user.credits_remaining = max(0, user.credits_remaining - 1)
    db.commit()
    return {"reply": reply, "agent": agent["name"], "credits_remaining": user.credits_remaining}


# ---------------------------------------------------------------- AI Page Builder
def make_page_slug() -> str:
    return secrets.token_urlsafe(6).lower().replace("_", "").replace("-", "")


@app.get("/page-builder", response_class=HTMLResponse)
def page_builder_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pages = db.query(GeneratedPage).filter(GeneratedPage.owner_id == user.id).order_by(GeneratedPage.created_at.desc()).all()
    return templates.TemplateResponse("page_builder.html", {"request": request, "user": user, "pages": pages})


@app.post("/api/pages/generate")
async def generate_page(payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.is_admin and user.credits_remaining < 5:
        raise HTTPException(402, "Not enough credits. Page generation costs 5 credits.")
    prompt = payload.get("prompt", "").strip()
    if not prompt:
        raise HTTPException(400, "Describe the page you want built.")

    try:
        html = await call_llm(PAGE_BUILDER_SYSTEM_PROMPT, [{"role": "user", "content": prompt}])
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))

    html = re.sub(r"^```html\s*|```\s*$", "", html.strip())
    if "<!DOCTYPE" not in html and "<html" not in html.lower():
        raise HTTPException(502, "The AI didn't return a usable page. Try rephrasing your description.")

    slug = make_page_slug()
    while db.query(GeneratedPage).filter(GeneratedPage.slug == slug).first():
        slug = make_page_slug()

    page = GeneratedPage(owner_id=user.id, slug=slug, prompt=prompt, html_content=html)
    db.add(page)
    if not user.is_admin:
        user.credits_remaining -= 5
    db.commit()
    return {"slug": slug, "url": f"/p/{slug}", "credits_remaining": user.credits_remaining}


@app.get("/p/{slug}", response_class=HTMLResponse)
def view_generated_page(slug: str, db: Session = Depends(get_db)):
    page = db.query(GeneratedPage).filter(GeneratedPage.slug == slug).first()
    if not page:
        raise HTTPException(404, "Page not found.")
    return HTMLResponse(page.html_content)


@app.delete("/api/pages/{page_id}")
def delete_page(page_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    page = db.query(GeneratedPage).filter(GeneratedPage.id == page_id, GeneratedPage.owner_id == user.id).first()
    if not page:
        raise HTTPException(404, "Page not found.")
    db.delete(page)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- Dashboard AI Q&A (Gemini)
@app.post("/api/dashboard/ask")
async def dashboard_ask(payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    question = payload.get("question", "").strip()
    if not question:
        raise HTTPException(400, "Ask a question first.")

    wf_ids = [w.id for w in db.query(Workflow).filter(Workflow.owner_id == user.id).all()]
    total_runs = db.query(WorkflowRun).filter(WorkflowRun.workflow_id.in_(wf_ids)).count()
    success_runs = db.query(WorkflowRun).filter(WorkflowRun.workflow_id.in_(wf_ids), WorkflowRun.status == "success").count()
    chat_count = db.query(ChatMessage).filter(ChatMessage.user_id == user.id).count()
    payments = db.query(Payment).filter(Payment.user_id == user.id, Payment.status == "paid").all()
    total_spent = sum(p.amount for p in payments)
    pages_built = db.query(GeneratedPage).filter(GeneratedPage.owner_id == user.id).count()

    context = (
        f"Here is this user's real Kestrel AI account data:\n"
        f"- Plan: {user.plan}\n"
        f"- Credits remaining: {user.credits_remaining}\n"
        f"- Total workflow runs: {total_runs} ({success_runs} successful)\n"
        f"- Total chat messages sent: {chat_count}\n"
        f"- Pages built with the AI page builder: {pages_built}\n"
        f"- Total paid to date: {total_spent} INR across {len(payments)} payment(s)\n"
        f"Answer the user's question using ONLY this data. Be brief and direct. If the data doesn't "
        f"answer their question, say so plainly rather than guessing."
    )

    try:
        answer = await call_gemini_required(context, [{"role": "user", "content": question}])
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    return {"answer": answer}


# ---------------------------------------------------------------- Workflow Builder
@app.get("/workflows", response_class=HTMLResponse)
def workflow_list(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    workflows = db.query(Workflow).filter(Workflow.owner_id == user.id).all()
    return templates.TemplateResponse("workflow_builder.html", {"request": request, "user": user, "workflows": workflows, "agents": AGENTS, "workflow": None})


@app.get("/workflows/{workflow_id}", response_class=HTMLResponse)
def workflow_edit(workflow_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    workflow = db.query(Workflow).filter(Workflow.id == workflow_id, Workflow.owner_id == user.id).first()
    if not workflow:
        raise HTTPException(404, "Workflow not found.")
    workflows = db.query(Workflow).filter(Workflow.owner_id == user.id).all()
    return templates.TemplateResponse("workflow_builder.html", {"request": request, "user": user, "workflows": workflows, "agents": AGENTS, "workflow": workflow})


@app.post("/api/workflows")
def create_workflow(payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wf = Workflow(owner_id=user.id, name=payload.get("name", "Untitled Workflow"), graph_json=json.dumps(payload.get("graph", {})))
    db.add(wf)
    db.commit()
    db.refresh(wf)
    return {"id": wf.id}


@app.put("/api/workflows/{workflow_id}")
def update_workflow(workflow_id: str, payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wf = db.query(Workflow).filter(Workflow.id == workflow_id, Workflow.owner_id == user.id).first()
    if not wf:
        raise HTTPException(404, "Workflow not found.")
    wf.name = payload.get("name", wf.name)
    wf.graph_json = json.dumps(payload.get("graph", json.loads(wf.graph_json)))
    wf.is_active = payload.get("is_active", wf.is_active)
    wf.updated_at = dt.datetime.utcnow()
    db.commit()
    return {"ok": True}


@app.delete("/api/workflows/{workflow_id}")
def delete_workflow(workflow_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wf = db.query(Workflow).filter(Workflow.id == workflow_id, Workflow.owner_id == user.id).first()
    if not wf:
        raise HTTPException(404, "Workflow not found.")
    db.delete(wf)
    db.commit()
    return {"ok": True}


@app.post("/api/workflows/{workflow_id}/run")
async def trigger_workflow(workflow_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wf = db.query(Workflow).filter(Workflow.id == workflow_id, Workflow.owner_id == user.id).first()
    if not wf:
        raise HTTPException(404, "Workflow not found.")
    run = await run_workflow(db, wf)
    return {"run_id": run.id, "status": run.status, "log": json.loads(run.log_json)}


# ---------------------------------------------------------------- Data Analysis (CSV / XLS)
@app.get("/data-analysis", response_class=HTMLResponse)
def data_analysis_page(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("data_analysis.html", {"request": request, "user": user})


@app.post("/api/data-analysis")
async def analyze_file(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    content = await file.read()
    name = file.filename.lower()
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        elif name.endswith(".xlsx") or name.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(content))
        else:
            raise HTTPException(400, "Please upload a .csv, .xls, or .xlsx file.")
    except Exception as exc:
        raise HTTPException(400, f"Could not read file: {exc}")

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    summary = {
        "rows": len(df),
        "columns": list(df.columns),
        "numeric_columns": numeric_cols,
        "preview": df.head(10).fillna("").astype(str).to_dict(orient="records"),
        "stats": {col: {
            "mean": round(float(df[col].mean()), 2) if not df[col].isna().all() else None,
            "min": round(float(df[col].min()), 2) if not df[col].isna().all() else None,
            "max": round(float(df[col].max()), 2) if not df[col].isna().all() else None,
        } for col in numeric_cols},
    }
    return summary


# ---------------------------------------------------------------- Pricing / Payments
@app.get("/pricing", response_class=HTMLResponse)
def pricing_page(request: Request, user: User = Depends(get_current_user_optional)):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "user": user, "plans": pay.PLANS, "agents": AGENTS, "scroll_to_pricing": True, "addons": pay.ADDONS},
    )


@app.post("/api/payments/razorpay/create-order")
def razorpay_order(payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan_key = payload.get("plan")
    if plan_key not in pay.PLANS:
        raise HTTPException(400, "Unknown plan.")
    amount = pay.PLANS[plan_key]["inr"]
    try:
        order = pay.razorpay_create_order(amount, receipt=f"{user.id}-{plan_key}")
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    db.add(Payment(user_id=user.id, gateway="razorpay", gateway_ref=order["id"], plan=plan_key, kind="plan", amount=amount, currency="INR"))
    db.commit()
    return {"order": order, "key_id": os.getenv("RAZORPAY_KEY_ID")}


@app.post("/api/payments/razorpay/create-addon-order")
def razorpay_addon_order(payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    addon_key = payload.get("addon")
    if addon_key not in pay.ADDONS:
        raise HTTPException(400, "Unknown add-on.")
    amount = pay.ADDONS[addon_key]["inr"]
    try:
        order = pay.razorpay_create_order(amount, receipt=f"{user.id}-addon-{addon_key}")
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    db.add(Payment(user_id=user.id, gateway="razorpay", gateway_ref=order["id"], plan=addon_key, kind="addon", amount=amount, currency="INR"))
    db.commit()
    return {"order": order, "key_id": os.getenv("RAZORPAY_KEY_ID")}


@app.post("/api/payments/razorpay/verify")
def razorpay_verify(payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ok = pay.razorpay_verify_signature(
        payload.get("razorpay_order_id", ""), payload.get("razorpay_payment_id", ""), payload.get("razorpay_signature", "")
    )
    if not ok:
        raise HTTPException(400, "Payment verification failed.")
    p = db.query(Payment).filter(Payment.gateway_ref == payload.get("razorpay_order_id")).first()
    if not p:
        raise HTTPException(404, "Order not found.")
    p.status = "paid"
    if p.kind == "addon":
        if p.plan == "voice_assistant":
            user.has_voice_addon = True
            base = user.voice_addon_expires if (user.voice_addon_expires and user.voice_addon_expires > dt.datetime.utcnow()) else dt.datetime.utcnow()
            user.voice_addon_expires = base + dt.timedelta(days=30)
    else:
        if p.plan not in pay.PLANS:
            raise HTTPException(400, "Payment references an unknown plan.")
        user.plan = p.plan
        user.credits_remaining = pay.PLANS[p.plan]["credits"]
    db.commit()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True)

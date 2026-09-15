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
from models import User, Workflow, WorkflowRun, ChatMessage, Payment
from auth import hash_password, verify_password, create_token, get_current_user, get_current_user_optional
from agents import AGENTS, call_llm
from workflows import run_workflow
import payments as pay

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Kestrel AI")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------- Landing / Pricing
@app.get("/", response_class=HTMLResponse)
def landing(request: Request, user: User = Depends(get_current_user_optional)):
    return templates.TemplateResponse(
        "index.html", {"request": request, "user": user, "plans": pay.PLANS, "agents": AGENTS}
    )


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
    user = User(email=email, hashed_password=hash_password(password), full_name=full_name, company=company)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "An account with this email already exists.")
    db.refresh(user)
    token = create_token(user.id)
    response = JSONResponse({"ok": True, "redirect": "/dashboard"})
    response.set_cookie("kestrel_token", token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax")
    return response


@app.post("/api/login")
def login(payload: dict, db: Session = Depends(get_db)):
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(401, "Incorrect email or password.")
    token = create_token(user.id)
    response = JSONResponse({"ok": True, "redirect": "/dashboard"})
    response.set_cookie("kestrel_token", token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax")
    return response


@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True, "redirect": "/"})
    response.delete_cookie("kestrel_token")
    return response


# ---------------------------------------------------------------- Dashboard
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    workflows = db.query(Workflow).filter(Workflow.owner_id == user.id).all()
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "user": user, "workflows": workflows, "plans": pay.PLANS}
    )


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
@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("chat.html", {"request": request, "user": user, "agents": AGENTS})


@app.post("/api/chat/{agent_key}")
async def chat_send(agent_key: str, payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.credits_remaining <= 0:
        raise HTTPException(402, "Out of credits. Please upgrade your plan.")
    agent = AGENTS.get(agent_key, AGENTS["general"])
    text = payload.get("message", "")
    if not text.strip():
        raise HTTPException(400, "Message cannot be empty.")

    db.add(ChatMessage(user_id=user.id, agent_key=agent_key, role="user", content=text))
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
    user.credits_remaining = max(0, user.credits_remaining - 1)
    db.commit()
    return {"reply": reply, "agent": agent["name"], "credits_remaining": user.credits_remaining}


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
    return templates.TemplateResponse("index.html", {"request": request, "user": user, "plans": pay.PLANS, "agents": AGENTS, "scroll_to_pricing": True})


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
    db.add(Payment(user_id=user.id, gateway="razorpay", gateway_ref=order["id"], plan=plan_key, amount=amount, currency="INR"))
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
    if p:
        p.status = "paid"
        user.plan = p.plan
        user.credits_remaining = pay.PLANS[p.plan]["credits"]
        db.commit()
    return {"ok": True}


@app.post("/api/payments/stripe/create-session")
def stripe_session(payload: dict, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan_key = payload.get("plan")
    if plan_key not in pay.PLANS:
        raise HTTPException(400, "Unknown plan.")
    base_url = str(request.base_url).rstrip("/")
    try:
        session = pay.stripe_create_checkout_session(
            plan_key, success_url=f"{base_url}/dashboard?payment=success", cancel_url=f"{base_url}/pricing?payment=cancelled"
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    db.add(Payment(user_id=user.id, gateway="stripe", gateway_ref=session["id"], plan=plan_key, amount=pay.PLANS[plan_key]["usd"], currency="USD"))
    db.commit()
    return session


@app.post("/api/payments/paypal/create-order")
async def paypal_order(payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan_key = payload.get("plan")
    if plan_key not in pay.PLANS:
        raise HTTPException(400, "Unknown plan.")
    try:
        order = await pay.paypal_create_order(plan_key)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    db.add(Payment(user_id=user.id, gateway="paypal", gateway_ref=order["id"], plan=plan_key, amount=pay.PLANS[plan_key]["usd"], currency="USD"))
    db.commit()
    return order


@app.post("/api/payments/paypal/capture/{order_id}")
async def paypal_capture(order_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = await pay.paypal_capture_order(order_id)
    p = db.query(Payment).filter(Payment.gateway_ref == order_id).first()
    if p and result.get("status") == "COMPLETED":
        p.status = "paid"
        user.plan = p.plan
        user.credits_remaining = pay.PLANS[p.plan]["credits"]
        db.commit()
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True)

import uuid
import datetime as dt
from sqlalchemy import Column, String, DateTime, Boolean, Integer, Text, ForeignKey, Float
from sqlalchemy.orm import relationship
from database import Base


def gen_id():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_id)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, default="")
    company = Column(String, default="")
    plan = Column(String, default="trial")  # trial (unpaid) / starter / growth / enterprise
    plan_expires = Column(DateTime, nullable=True)
    credits_remaining = Column(Integer, default=0)  # no free trial credits — see has_paid
    has_paid = Column(Boolean, default=False)  # gates all product access except admin accounts
    is_admin = Column(Boolean, default=False)
    has_voice_addon = Column(Boolean, default=False)
    voice_addon_expires = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    workflows = relationship("Workflow", back_populates="owner", cascade="all, delete-orphan")
    chats = relationship("ChatMessage", back_populates="user", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="user", cascade="all, delete-orphan")
    generated_pages = relationship("GeneratedPage", back_populates="owner", cascade="all, delete-orphan")


class Workflow(Base):
    __tablename__ = "workflows"
    id = Column(String, primary_key=True, default=gen_id)
    owner_id = Column(String, ForeignKey("users.id"))
    name = Column(String, default="Untitled Workflow")
    graph_json = Column(Text, default="{}")  # nodes + edges
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    updated_at = Column(DateTime, default=dt.datetime.utcnow)

    owner = relationship("User", back_populates="workflows")
    runs = relationship("WorkflowRun", back_populates="workflow", cascade="all, delete-orphan")


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    id = Column(String, primary_key=True, default=gen_id)
    workflow_id = Column(String, ForeignKey("workflows.id"))
    status = Column(String, default="running")  # running / success / failed
    log_json = Column(Text, default="[]")
    started_at = Column(DateTime, default=dt.datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, default=0)

    workflow = relationship("Workflow", back_populates="runs")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"))
    agent_key = Column(String, default="general")
    role = Column(String)  # user / assistant
    content = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    user = relationship("User", back_populates="chats")


class Payment(Base):
    __tablename__ = "payments"
    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"))
    gateway = Column(String)  # razorpay
    gateway_ref = Column(String)
    plan = Column(String)
    kind = Column(String, default="plan")  # plan / addon
    amount = Column(Float)
    currency = Column(String, default="INR")
    status = Column(String, default="created")  # created / paid / failed
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    user = relationship("User", back_populates="payments")


class GeneratedPage(Base):
    __tablename__ = "generated_pages"
    id = Column(String, primary_key=True, default=gen_id)
    owner_id = Column(String, ForeignKey("users.id"))
    slug = Column(String, unique=True, index=True, nullable=False)
    prompt = Column(Text)
    html_content = Column(Text)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    owner = relationship("User", back_populates="generated_pages")


class SiteSettings(Base):
    __tablename__ = "site_settings"
    id = Column(String, primary_key=True, default=lambda: "default")
    meta_title = Column(String, default="Kestrel AI — The Agent Platform for Running Your Business on Autopilot")
    meta_description = Column(
        Text,
        default="Build, deploy, and run AI agents for every part of your business — without a single line of "
                "code. 16 industry-tuned AI assistants, a visual workflow builder, and built-in billing.",
    )
    meta_keywords = Column(String, default="AI agents, no-code automation, AI assistant platform, business automation")
    og_image_url = Column(String, default="")
    updated_at = Column(DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)

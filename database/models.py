"""
database/models.py
──────────────────
SQLAlchemy 2.0 ORM models for all core entities.
Matches the data model in Spec v3.1 §16.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, Float, ForeignKey,
    Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
UUID_TYPE = String(36).with_variant(UUID(as_uuid=False), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ══════════════════════════════════════════════════════════════════
#  Enums  (mirrors §6.2 status lifecycle)
# ══════════════════════════════════════════════════════════════════

class ProjectStatus(str, PyEnum):
    DETECTED          = "detected"
    PENDING_APPROVAL  = "pending_approval"
    APPROVED          = "approved"
    REJECTED          = "rejected"
    CA_DETECTED       = "ca_detected"
    CHECKS_PASSED     = "checks_passed"
    BUYING            = "buying"
    PURCHASED         = "purchased"
    COMPLETED         = "completed"
    # Terminal / side states
    SAFETY_BLOCKED    = "safety_blocked"
    CANCELLED         = "cancelled"
    EXPIRED           = "expired"


class Platform(str, PyEnum):
    X         = "x"
    TELEGRAM  = "telegram"
    MANUAL    = "manual"


class SourceType(str, PyEnum):
    OFFICIAL_PROJECT   = "official_project"
    ECOSYSTEM_LAUNCHPAD = "ecosystem_launchpad"
    OPERATOR_MANUAL    = "operator_manual"


class EventType(str, PyEnum):
    LAUNCH_INTENT     = "launch_intent"
    CA_ANNOUNCEMENT   = "ca_announcement"
    LAUNCH_CONFIRM    = "launch_confirm"
    UPDATE            = "update"
    CANCELLATION      = "cancellation"
    AMBIGUOUS         = "ambiguous"


class ApprovalDecision(str, PyEnum):
    APPROVED  = "approved"
    REJECTED  = "rejected"
    PENDING   = "pending"
    UNBLOCKED = "unblocked"


class OrderStatus(str, PyEnum):
    PENDING   = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FILLED    = "filled"
    FAILED    = "failed"


class PositionStatus(str, PyEnum):
    OPEN             = "open"
    CLOSED           = "closed"
    EMERGENCY_CLOSED = "emergency_closed"



# ══════════════════════════════════════════════════════════════════
#  SourceAccount  (§16)
# ══════════════════════════════════════════════════════════════════

class SourceAccount(Base):
    __tablename__ = "source_accounts"

    id: Mapped[str]           = mapped_column(UUID_TYPE, primary_key=True, default=_uuid)
    platform: Mapped[str]     = mapped_column(Enum(Platform), nullable=False)
    account_id: Mapped[str]   = mapped_column(String(256), nullable=False)   # platform-native ID
    handle: Mapped[Optional[str]] = mapped_column(String(256))
    display_name: Mapped[Optional[str]] = mapped_column(String(512))
    source_type: Mapped[str]  = mapped_column(Enum(SourceType), nullable=False)
    is_active: Mapped[bool]   = mapped_column(Boolean, default=True)
    trust_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by: Mapped[Optional[str]] = mapped_column(String(256))
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    has_username_changes: Mapped[bool] = mapped_column(Boolean, default=False)
    username_changes: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, default=list)
    # JSON list of authorized admin IDs (Telegram only)
    authorized_admin_ids: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("platform", "account_id", name="uq_source_platform_account"),
    )

    announcements: Mapped[list["Announcement"]] = relationship(back_populates="source_account")


# ══════════════════════════════════════════════════════════════════
#  Project  (§16)
# ══════════════════════════════════════════════════════════════════

class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str]           = mapped_column(UUID_TYPE, primary_key=True, default=_uuid)
    name: Mapped[Optional[str]] = mapped_column(String(256))
    ticker: Mapped[Optional[str]] = mapped_column(String(32))
    description: Mapped[Optional[str]] = mapped_column(Text)
    official_x_handle: Mapped[Optional[str]] = mapped_column(String(256))
    official_telegram: Mapped[Optional[str]] = mapped_column(String(256))
    website_url: Mapped[Optional[str]] = mapped_column(String(1024))
    chain_id: Mapped[Optional[str]] = mapped_column(String(64))    # "robinhood" | "solana"
    ca_address: Mapped[Optional[str]] = mapped_column(String(256))
    expected_launch_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expected_ca_time_str: Mapped[Optional[str]] = mapped_column(String(256), default="Not specified")
    launch_time_str: Mapped[Optional[str]] = mapped_column(String(256), default="Not specified")
    network: Mapped[Optional[str]] = mapped_column(String(64))
    expected_ca_source: Mapped[Optional[str]] = mapped_column(String(64))  # "x" | "telegram" | "both"
    status: Mapped[str]       = mapped_column(
        Enum(ProjectStatus), nullable=False, default=ProjectStatus.DETECTED
    )
    score: Mapped[Optional[float]] = mapped_column(Float)

    # ── AI Intelligence Fields ──────────────────────────────────────
    category: Mapped[Optional[str]] = mapped_column(String(64))
    categories: Mapped[Optional[list]] = mapped_column(JSON_TYPE, default=list)
    ai_score: Mapped[Optional[float]] = mapped_column(Float)
    ai_scores_breakdown: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, default=dict)
    ai_analysis: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, default=dict)
    ai_recommendation: Mapped[Optional[str]] = mapped_column(String(64))
    verification_flags: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, default=dict)
    red_flags: Mapped[Optional[list]] = mapped_column(JSON_TYPE, default=list)
    missing_information: Mapped[Optional[list]] = mapped_column(JSON_TYPE, default=list)
    ai_evaluated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    founder_team_info: Mapped[Optional[str]] = mapped_column(Text)
    product_summary: Mapped[Optional[str]] = mapped_column(Text)
    documentation_url: Mapped[Optional[str]] = mapped_column(String(1024))
    ecosystem_links: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, default=dict)

    # Identity keys for deduplication
    identity_keys: Mapped[Optional[dict]] = mapped_column(JSON_TYPE, default=dict)
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __init__(self, **kwargs):
        # Support aliases
        if "project_name" in kwargs and "name" not in kwargs:
            kwargs["name"] = kwargs.pop("project_name")
        if "expected_launch_time" in kwargs and "expected_launch_at" not in kwargs:
            kwargs["expected_launch_at"] = kwargs.pop("expected_launch_time")
        super().__init__(**kwargs)

    announcements: Mapped[list["Announcement"]] = relationship(back_populates="project")
    approvals: Mapped[list["Approval"]] = relationship(back_populates="project")
    blacklist_entry: Mapped[Optional["BlacklistEntry"]] = relationship(back_populates="project", uselist=False)
    contract_candidates: Mapped[list["ContractCandidate"]] = relationship(back_populates="project")
    assessments: Mapped[list["Assessment"]] = relationship(back_populates="project")
    orders: Mapped[list["Order"]] = relationship(back_populates="project")
    positions: Mapped[list["Position"]] = relationship(back_populates="project")


# ══════════════════════════════════════════════════════════════════
#  Announcement  (§16)
# ══════════════════════════════════════════════════════════════════

class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[str]              = mapped_column(UUID_TYPE, primary_key=True, default=_uuid)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("projects.id"), index=True)
    source_account_id: Mapped[Optional[str]] = mapped_column(ForeignKey("source_accounts.id"), index=True)
    platform: Mapped[str]        = mapped_column(Enum(Platform), nullable=False)
    message_id: Mapped[Optional[str]] = mapped_column(String(256))    # tweet ID / telegram message ID
    post_url: Mapped[Optional[str]] = mapped_column(String(2048))
    text: Mapped[Optional[str]]  = mapped_column(Text)
    author_id: Mapped[Optional[str]] = mapped_column(String(256))
    event_type: Mapped[str]      = mapped_column(Enum(EventType), nullable=False, default=EventType.AMBIGUOUS)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_forwarded: Mapped[bool]   = mapped_column(Boolean, default=False)
    ca_address: Mapped[Optional[str]] = mapped_column(String(256))
    expected_launch_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expected_ca_time_str: Mapped[Optional[str]] = mapped_column(String(256), default="Not specified")
    launch_time_str: Mapped[Optional[str]] = mapped_column(String(256), default="Not specified")
    x_profile_url: Mapped[Optional[str]] = mapped_column(String(1024))
    network: Mapped[Optional[str]] = mapped_column(String(64))
    expected_ca_source: Mapped[Optional[str]] = mapped_column(String(64))
    source_handle: Mapped[Optional[str]] = mapped_column(String(256))
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON_TYPE)            # full platform payload

    def __init__(self, **kwargs):
        if "raw" in kwargs and "raw_data" not in kwargs:
            kwargs["raw_data"] = kwargs.pop("raw")
        if "expected_launch_time" in kwargs and "expected_launch_at" not in kwargs:
            kwargs["expected_launch_at"] = kwargs.pop("expected_launch_time")
        if "created_at" in kwargs and "received_at" not in kwargs:
            kwargs["received_at"] = kwargs.pop("created_at")
        elif "created_at" in kwargs:
            kwargs.pop("created_at")
        super().__init__(**kwargs)

    project: Mapped[Optional["Project"]] = relationship(back_populates="announcements")
    source_account: Mapped[Optional["SourceAccount"]] = relationship(back_populates="announcements")


# ══════════════════════════════════════════════════════════════════
#  Approval  (§16)
# ══════════════════════════════════════════════════════════════════

class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str]              = mapped_column(UUID_TYPE, primary_key=True, default=_uuid)
    project_id: Mapped[str]      = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    operator_id: Mapped[str]     = mapped_column(String(256), nullable=False)   # Telegram user ID
    decision: Mapped[str]        = mapped_column(Enum(ApprovalDecision), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    approved_source_ids: Mapped[Optional[dict]] = mapped_column(JSON_TYPE)    # list of source_account IDs
    allowed_chains: Mapped[Optional[dict]] = mapped_column(JSON_TYPE)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    telegram_message_id: Mapped[Optional[str]] = mapped_column(String(256))

    project: Mapped["Project"] = relationship(back_populates="approvals")


# ══════════════════════════════════════════════════════════════════
#  BlacklistEntry  (§16)
# ══════════════════════════════════════════════════════════════════

class BlacklistEntry(Base):
    __tablename__ = "blacklist_entries"

    id: Mapped[str]              = mapped_column(UUID_TYPE, primary_key=True, default=_uuid)
    project_id: Mapped[str]      = mapped_column(ForeignKey("projects.id"), nullable=False, unique=True)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[str]      = mapped_column(String(256), nullable=False, default="operator")
    is_unblocked: Mapped[bool]   = mapped_column(Boolean, default=False)
    unblocked_by: Mapped[Optional[str]] = mapped_column(String(256))
    unblocked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    unblock_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="blacklist_entry")


# ══════════════════════════════════════════════════════════════════
#  ContractCandidate  (§16)
# ══════════════════════════════════════════════════════════════════

class ContractCandidate(Base):
    __tablename__ = "contract_candidates"

    id: Mapped[str]              = mapped_column(UUID_TYPE, primary_key=True, default=_uuid)
    project_id: Mapped[str]      = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    chain_id: Mapped[str]        = mapped_column(String(64), nullable=False)
    address: Mapped[str]         = mapped_column(String(256), nullable=False)
    source_message_id: Mapped[Optional[str]] = mapped_column(String(256))
    source_account_id: Mapped[Optional[str]] = mapped_column(ForeignKey("source_accounts.id"))
    is_verified: Mapped[bool]    = mapped_column(Boolean, default=False)
    is_active: Mapped[bool]      = mapped_column(Boolean, default=True)
    has_conflict: Mapped[bool]   = mapped_column(Boolean, default=False)
    conflict_note: Mapped[Optional[str]] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="contract_candidates")


# ══════════════════════════════════════════════════════════════════
#  Assessment  (§16) — tax / volume / safety check results
# ══════════════════════════════════════════════════════════════════

class CheckResult(str, PyEnum):
    PASS    = "pass"
    FAIL    = "fail"
    UNKNOWN = "unknown"


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[str]              = mapped_column(UUID_TYPE, primary_key=True, default=_uuid)
    project_id: Mapped[str]      = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    contract_candidate_id: Mapped[Optional[str]] = mapped_column(ForeignKey("contract_candidates.id"))
    buy_tax_pct: Mapped[Optional[float]] = mapped_column(Float)
    sell_tax_pct: Mapped[Optional[float]] = mapped_column(Float)
    tax_result: Mapped[Optional[str]] = mapped_column(Enum(CheckResult))
    volume_usd: Mapped[Optional[float]] = mapped_column(Float)
    volume_result: Mapped[Optional[str]] = mapped_column(Enum(CheckResult))
    safety_result: Mapped[Optional[str]] = mapped_column(Enum(CheckResult))
    safety_flags: Mapped[Optional[dict]] = mapped_column(JSON_TYPE)
    liquidity_result: Mapped[Optional[str]] = mapped_column(Enum(CheckResult))
    route_result: Mapped[Optional[str]] = mapped_column(Enum(CheckResult))
    overall_result: Mapped[Optional[str]] = mapped_column(Enum(CheckResult))
    provider: Mapped[Optional[str]] = mapped_column(String(128))
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON_TYPE)

    project: Mapped["Project"] = relationship(back_populates="assessments")


# ══════════════════════════════════════════════════════════════════
#  AuditEvent  (§16) — append-only
# ══════════════════════════════════════════════════════════════════

class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str]              = mapped_column(UUID_TYPE, primary_key=True, default=_uuid)
    actor: Mapped[str]           = mapped_column(String(256), nullable=False)   # "system" | telegram_user_id
    action: Mapped[str]          = mapped_column(String(128), nullable=False)
    entity_type: Mapped[Optional[str]] = mapped_column(String(64))
    entity_id: Mapped[Optional[str]] = mapped_column(String(256))
    old_state: Mapped[Optional[str]] = mapped_column(String(64))
    new_state: Mapped[Optional[str]] = mapped_column(String(64))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    detail: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(256))
    event_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSON_TYPE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __init__(self, **kwargs):
        if "project_id" in kwargs and "entity_id" not in kwargs:
            kwargs["entity_id"] = kwargs.pop("project_id")
            if "entity_type" not in kwargs:
                kwargs["entity_type"] = "project"
        if "previous_value" in kwargs and "old_state" not in kwargs:
            kwargs["old_state"] = kwargs.pop("previous_value")
        if "new_value" in kwargs and "new_state" not in kwargs:
            kwargs["new_state"] = kwargs.pop("new_value")
        if "metadata" in kwargs and "event_metadata" not in kwargs:
            kwargs["event_metadata"] = kwargs.pop("metadata")
        super().__init__(**kwargs)

    __table_args__ = (
        # Partial index for fast entity lookups
        {},
    )


# ══════════════════════════════════════════════════════════════════
#  TelegramNotification  — track sent alerts
# ══════════════════════════════════════════════════════════════════

class TelegramNotification(Base):
    __tablename__ = "telegram_notifications"

    id: Mapped[str]              = mapped_column(UUID_TYPE, primary_key=True, default=_uuid)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("projects.id"), index=True)
    chat_id: Mapped[str]         = mapped_column(String(64), nullable=False)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    message_text: Mapped[Optional[str]] = mapped_column(Text)
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)  # "launch_detected" | "ca_detected" | "reminder" | "expired"
    sent_at: Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_actioned: Mapped[bool]    = mapped_column(Boolean, default=False)
    action_taken: Mapped[Optional[str]] = mapped_column(String(64))   # "approved" | "rejected" | "details"
    actioned_by: Mapped[Optional[str]] = mapped_column(String(256))
    actioned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ══════════════════════════════════════════════════════════════════
#  Order  (§16)
# ══════════════════════════════════════════════════════════════════

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str]              = mapped_column(UUID_TYPE, primary_key=True, default=_uuid)
    project_id: Mapped[str]      = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    ca_address: Mapped[str]      = mapped_column(String(256), nullable=False)
    chain_id: Mapped[str]        = mapped_column(String(64), nullable=False)
    amount_usd: Mapped[float]    = mapped_column(Float, nullable=False)
    amount_native: Mapped[Optional[float]] = mapped_column(Float)
    quote_data: Mapped[Optional[dict]] = mapped_column(JSON_TYPE)
    route: Mapped[Optional[str]] = mapped_column(String(256))
    status: Mapped[str]          = mapped_column(
        Enum(OrderStatus), nullable=False, default=OrderStatus.PENDING
    )
    tx_hash: Mapped[Optional[str]] = mapped_column(String(256))
    fee_spent: Mapped[Optional[float]] = mapped_column(Float)
    is_paper: Mapped[bool]       = mapped_column(Boolean, default=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="orders")


# ══════════════════════════════════════════════════════════════════
#  Position  (§16)
# ══════════════════════════════════════════════════════════════════

class Position(Base):
    __tablename__ = "positions"

    id: Mapped[str]              = mapped_column(UUID_TYPE, primary_key=True, default=_uuid)
    project_id: Mapped[str]      = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    token_address: Mapped[str]   = mapped_column(String(256), nullable=False)
    chain_id: Mapped[str]        = mapped_column(String(64), nullable=False)
    quantity: Mapped[float]      = mapped_column(Float, nullable=False)
    cost_basis_usd: Mapped[float] = mapped_column(Float, nullable=False)
    current_value_usd: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str]          = mapped_column(
        Enum(PositionStatus), nullable=False, default=PositionStatus.OPEN
    )
    is_paper: Mapped[bool]       = mapped_column(Boolean, default=True)
    opened_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    project: Mapped["Project"] = relationship(back_populates="positions")


# ══════════════════════════════════════════════════════════════════
#  AccountSnapshot — Historical social account metadata tracking
# ══════════════════════════════════════════════════════════════════

class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"

    id: Mapped[str]              = mapped_column(UUID_TYPE, primary_key=True, default=_uuid)
    platform: Mapped[str]        = mapped_column(Enum(Platform), nullable=False, default=Platform.X)
    account_id: Mapped[str]      = mapped_column(String(256), nullable=False, index=True)
    handle: Mapped[str]          = mapped_column(String(256), nullable=False, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(512))
    bio: Mapped[Optional[str]]   = mapped_column(Text)
    followers_count: Mapped[Optional[int]] = mapped_column(Integer)
    following_count: Mapped[Optional[int]] = mapped_column(Integer)
    tweet_count: Mapped[Optional[int]]     = mapped_column(Integer)
    account_created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[Optional[dict]] = mapped_column(JSON_TYPE)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ══════════════════════════════════════════════════════════════════
#  AIEvaluationCache — Persistent cache for Gemini evaluations
# ══════════════════════════════════════════════════════════════════

class AIEvaluationCache(Base):
    __tablename__ = "ai_evaluation_cache"

    id: Mapped[str]              = mapped_column(UUID_TYPE, primary_key=True, default=_uuid)
    cache_key: Mapped[str]       = mapped_column(String(256), nullable=False, unique=True, index=True)
    project_name: Mapped[Optional[str]] = mapped_column(String(256))
    source_url: Mapped[Optional[str]]   = mapped_column(String(2048))
    evaluation_data: Mapped[dict]       = mapped_column(JSON_TYPE, nullable=False)
    overall_score: Mapped[float]        = mapped_column(Float, default=0.0)
    recommendation: Mapped[str]         = mapped_column(String(64), default="REVIEW")
    model_used: Mapped[str]             = mapped_column(String(128), default="gemini-2.0-flash")
    created_at: Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))




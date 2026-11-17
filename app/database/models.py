from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.enums import OrderStatus, SessionStatus, VoiceSessionStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Medicine(Base):
    __tablename__ = "medicines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    medicine_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    medicine_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_serial_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        Index("ix_medicines_name_active", "medicine_name", "is_active"),
    )


class OrderSession(Base):
    __tablename__ = "order_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    customer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SessionStatus.ACTIVE.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmation_token_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    confirmation_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    confirmation_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="OrderItem.id",
    )
    order: Mapped["Order | None"] = relationship(
        back_populates="session", uselist=False
    )
    voice_session: Mapped["VoiceSession | None"] = relationship(
        back_populates="order_session",
        cascade="all, delete-orphan",
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'CONFIRMED', 'CANCELLED', 'EXPIRED')",
            name="ck_order_sessions_status",
        ),
        Index("ix_order_sessions_status_expires", "status", "expires_at"),
    )


class VoiceSession(Base):
    __tablename__ = "voice_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    voice_session_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    order_session_id: Mapped[int] = mapped_column(
        ForeignKey("order_sessions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=VoiceSessionStatus.ACTIVE.value
    )
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="hi")
    state_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    order_session: Mapped[OrderSession] = relationship(
        back_populates="voice_session"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'CONFIRMED', 'CANCELLED', 'ENDED', 'EXPIRED')",
            name="ck_voice_sessions_status",
        ),
        CheckConstraint(
            "language IN ('hi', 'en')",
            name="ck_voice_sessions_language",
        ),
        CheckConstraint(
            "turn_count >= 0",
            name="ck_voice_sessions_turn_count",
        ),
        Index("ix_voice_sessions_status_expires", "status", "expires_at"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("order_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    medicine_id: Mapped[str] = mapped_column(
        ForeignKey("medicines.medicine_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    session: Mapped[OrderSession] = relationship(back_populates="items")
    medicine: Mapped[Medicine] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        CheckConstraint(
            "unit IN ('TABLET', 'STRIP', 'BOTTLE', 'BOX', 'UNIT')",
            name="ck_order_items_unit",
        ),
        UniqueConstraint(
            "session_id",
            "medicine_id",
            "unit",
            name="uq_order_items_session_medicine_unit",
        ),
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    session_id: Mapped[int] = mapped_column(
        ForeignKey("order_sessions.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
        index=True,
    )
    customer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=OrderStatus.PENDING_PHARMACY_REVIEW.value,
    )
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    call_reference_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    confirmation_source: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )

    session: Mapped[OrderSession] = relationship(back_populates="order")
    line_items: Mapped[list["OrderLineItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderLineItem.id",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_PHARMACY_REVIEW')",
            name="ck_orders_status",
        ),
    )


class OrderLineItem(Base):
    __tablename__ = "order_line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    medicine_id: Mapped[str] = mapped_column(
        ForeignKey("medicines.medicine_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    medicine_name: Mapped[str] = mapped_column(String(255), nullable=False)
    medicine_description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    order: Mapped[Order] = relationship(back_populates="line_items")

    __table_args__ = (
        CheckConstraint(
            "quantity > 0", name="ck_order_line_items_quantity_positive"
        ),
        CheckConstraint(
            "unit IN ('TABLET', 'STRIP', 'BOTTLE', 'BOX', 'UNIT')",
            name="ck_order_line_items_unit",
        ),
        UniqueConstraint(
            "order_id",
            "medicine_id",
            "unit",
            name="uq_order_line_items_order_medicine_unit",
        ),
    )


class CallLog(Base):
    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(16))
    provider_call_reference: Mapped[str] = mapped_column(String(128))
    stream_sid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    voice_session_id: Mapped[str] = mapped_column(ForeignKey("voice_sessions.voice_session_id", ondelete="RESTRICT"), unique=True)
    order_session_id: Mapped[str] = mapped_column(ForeignKey("order_sessions.session_id", ondelete="RESTRICT"), unique=True)
    customer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="RINGING")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnect_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.order_id", ondelete="RESTRICT"), nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "provider_call_reference", name="uq_call_provider_reference"),
        CheckConstraint("status IN ('RINGING','CONNECTED','ACTIVE','PROCESSING','CONFIRMING','COMPLETED','DISCONNECTED','FAILED')", name="ck_call_status"),
    )


class CallTurn(Base):
    __tablename__ = "call_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(
        ForeignKey("call_logs.call_id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_text: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="hi")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        UniqueConstraint("call_id", "sequence", name="uq_call_turn_sequence"),
    )

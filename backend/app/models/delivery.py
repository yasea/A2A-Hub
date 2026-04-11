from sqlalchemy import ForeignKey, Integer, String, Text, text
from sqlalchemy import TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Delivery(Base):
    __tablename__ = "deliveries"

    delivery_id: Mapped[object] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String, ForeignKey("tasks.task_id", ondelete="SET NULL"), nullable=True)
    target_channel: Mapped[str] = mapped_column(String, nullable=False)
    target_ref: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("8"))
    next_retry_at: Mapped[object | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    dead_letter_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))

from sqlalchemy import BigInteger, ForeignKey, Numeric, String, Text
from sqlalchemy import TIMESTAMP, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RcRoomContextBinding(Base):
    __tablename__ = "rc_room_context_bindings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    rc_room_id: Mapped[str] = mapped_column(String, nullable=False)
    rc_server_url: Mapped[str | None] = mapped_column(String, nullable=True)
    context_id: Mapped[str] = mapped_column(String, ForeignKey("contexts.context_id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class WebhookNonce(Base):
    __tablename__ = "webhook_nonces"

    nonce: Mapped[str] = mapped_column(String, primary_key=True)
    source_system: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class MeteringEvent(Base):
    __tablename__ = "metering_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String, ForeignKey("tasks.task_id", ondelete="SET NULL"), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String, ForeignKey("agents.agent_id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    metric_name: Mapped[str] = mapped_column(String, nullable=False)
    metric_value: Mapped[object] = mapped_column(Numeric(18, 4), nullable=False, server_default=text("0"))
    unit: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'count'"))
    extra_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


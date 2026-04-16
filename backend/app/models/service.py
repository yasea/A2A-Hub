from sqlalchemy import BigInteger, Boolean, ForeignKey, String, Text, text
from sqlalchemy import TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class ServicePublication(Base):
    __tablename__ = "service_publications"

    service_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    handler_agent_id: Mapped[str] = mapped_column(String, ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String, nullable=False, server_default="listed", default="listed")
    contact_policy: Mapped[str] = mapped_column(String, nullable=False, server_default="auto_accept", default="auto_accept")
    allow_agent_initiated_chat: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="ACTIVE", default="ACTIVE")
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    capabilities_public: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class ServiceThread(Base):
    __tablename__ = "service_threads"

    thread_id: Mapped[str] = mapped_column(String, primary_key=True)
    service_id: Mapped[str] = mapped_column(String, ForeignKey("service_publications.service_id", ondelete="RESTRICT"), nullable=False)
    consumer_tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    provider_tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    provider_context_id: Mapped[str] = mapped_column(String, ForeignKey("contexts.context_id", ondelete="CASCADE"), nullable=False)
    initiator_agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    handler_agent_id: Mapped[str] = mapped_column(String, ForeignKey("agents.agent_id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="OPEN", default="OPEN")
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    last_activity_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class ServiceThreadMessage(Base):
    __tablename__ = "service_thread_messages"

    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(String, ForeignKey("service_threads.thread_id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    sender_tenant_id: Mapped[str | None] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="SET NULL"), nullable=True)
    sender_agent_id: Mapped[str | None] = mapped_column(String, ForeignKey("agents.agent_id", ondelete="SET NULL"), nullable=True)
    linked_task_id: Mapped[str | None] = mapped_column(String, ForeignKey("tasks.task_id", ondelete="SET NULL"), nullable=True)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    seq_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


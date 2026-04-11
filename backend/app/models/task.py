from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, text
from sqlalchemy import TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Task(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    context_id: Mapped[str] = mapped_column(String, ForeignKey("contexts.context_id", ondelete="CASCADE"), nullable=False)
    parent_task_id: Mapped[str | None] = mapped_column(String, ForeignKey("tasks.task_id", ondelete="SET NULL"), nullable=True)
    initiator_agent_id: Mapped[str | None] = mapped_column(String, ForeignKey("agents.agent_id", ondelete="SET NULL"), nullable=True)
    target_agent_id: Mapped[str | None] = mapped_column(String, ForeignKey("agents.agent_id", ondelete="SET NULL"), nullable=True)
    task_type: Mapped[str] = mapped_column(String, nullable=False, server_default="generic")
    state: Mapped[str] = mapped_column(String, nullable=False, server_default="SUBMITTED", default="SUBMITTED")
    priority: Mapped[str] = mapped_column(String, nullable=False, server_default="normal", default="normal")
    input_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    external_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    source_system: Mapped[str | None] = mapped_column(String, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    expires_at: Mapped[object | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    completed_at: Mapped[object | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    context: Mapped["Context"] = relationship("Context", back_populates="tasks", lazy="noload")  # noqa: F821
    messages: Mapped[list["TaskMessage"]] = relationship("TaskMessage", back_populates="task", lazy="noload")
    artifacts: Mapped[list["TaskArtifact"]] = relationship("TaskArtifact", back_populates="task", lazy="noload")
    state_transitions: Mapped[list["TaskStateTransition"]] = relationship("TaskStateTransition", back_populates="task", lazy="noload")


class TaskMessage(Base):
    __tablename__ = "task_messages"

    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False)
    context_id: Mapped[str] = mapped_column(String, ForeignKey("contexts.context_id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)              # user / assistant / system / tool
    mime_type: Mapped[str] = mapped_column(String, nullable=False, server_default="text/plain")
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_agent_id: Mapped[str | None] = mapped_column(String, ForeignKey("agents.agent_id", ondelete="SET NULL"), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    seq_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))

    task: Mapped["Task"] = relationship("Task", back_populates="messages", lazy="noload")


class TaskArtifact(Base):
    __tablename__ = "task_artifacts"

    artifact_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False)
    context_id: Mapped[str] = mapped_column(String, ForeignKey("contexts.context_id", ondelete="CASCADE"), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String, nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))

    task: Mapped["Task"] = relationship("Task", back_populates="artifacts", lazy="noload")


class TaskStateTransition(Base):
    __tablename__ = "task_state_transitions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String, nullable=True)
    to_state: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_type: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))

    task: Mapped["Task"] = relationship("Task", back_populates="state_transitions", lazy="noload")

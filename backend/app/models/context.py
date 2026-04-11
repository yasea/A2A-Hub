from sqlalchemy import BigInteger, ForeignKey, String, text
from sqlalchemy import TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Context(Base):
    __tablename__ = "contexts"

    context_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_channel: Mapped[str | None] = mapped_column(String, nullable=True)
    source_conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="OPEN", default="OPEN")
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    last_activity_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="contexts", lazy="noload")  # noqa: F821
    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="context", lazy="noload")  # noqa: F821
    participants: Mapped[list["ContextParticipant"]] = relationship("ContextParticipant", back_populates="context", lazy="noload")


class ContextParticipant(Base):
    __tablename__ = "context_participants"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    context_id: Mapped[str] = mapped_column(String, ForeignKey("contexts.context_id", ondelete="CASCADE"), nullable=False)
    participant_type: Mapped[str] = mapped_column(String, nullable=False)  # user / agent / system
    participant_id: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    joined_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))

    context: Mapped["Context"] = relationship("Context", back_populates="participants", lazy="noload")

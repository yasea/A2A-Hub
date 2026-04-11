from sqlalchemy import ForeignKey, String, Text, text
from sqlalchemy import TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Approval(Base):
    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False)
    context_id: Mapped[str] = mapped_column(String, ForeignKey("contexts.context_id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="PENDING")
    approver_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_key: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    resolved_at: Mapped[object | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

from sqlalchemy import BigInteger, ForeignKey, String, text
from sqlalchemy import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class AgentFriend(Base):
    __tablename__ = "agent_friends"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    requester_tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    target_tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    requester_agent_id: Mapped[str] = mapped_column(String, nullable=False)
    target_agent_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="PENDING", default="PENDING")
    context_id: Mapped[str | None] = mapped_column(String, nullable=True)
    requester_context_id: Mapped[str | None] = mapped_column(String, nullable=True)
    target_context_id: Mapped[str | None] = mapped_column(String, nullable=True)
    invite_token: Mapped[str | None] = mapped_column(String, nullable=True)
    message: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))

    tenant = relationship("Tenant", lazy="noload", foreign_keys=[tenant_id])  # noqa: F821

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, text
from sqlalchemy import TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class RoutingRule(Base):
    __tablename__ = "routing_rules"

    id: Mapped[object] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    match_expr: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    target_agent_id: Mapped[str] = mapped_column(String, ForeignKey("agents.agent_id", ondelete="CASCADE"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))

    target_agent: Mapped["Agent"] = relationship("Agent", lazy="noload")  # noqa: F821


class TaskRouteHop(Base):
    __tablename__ = "task_route_hops"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String, ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    hop_seq: Mapped[int] = mapped_column(Integer, nullable=False)          # 跳转序号，从 1 开始
    from_agent_id: Mapped[str | None] = mapped_column(String, ForeignKey("agents.agent_id", ondelete="SET NULL"), nullable=True)
    to_agent_id: Mapped[str | None] = mapped_column(String, ForeignKey("agents.agent_id", ondelete="SET NULL"), nullable=True)
    route_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    matched_rule_id: Mapped[object | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))

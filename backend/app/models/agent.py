from sqlalchemy import BigInteger, ForeignKey, String, text
from sqlalchemy import TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Agent(Base):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String, primary_key=True)
    public_number: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.tenant_id", ondelete="RESTRICT"), nullable=False)
    agent_type: Mapped[str] = mapped_column(String, nullable=False)       # native / federated / bridged
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="ACTIVE", default="ACTIVE")
    capabilities: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    auth_scheme: Mapped[str | None] = mapped_column(String, nullable=True)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[object] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="agents", lazy="noload")  # noqa: F821

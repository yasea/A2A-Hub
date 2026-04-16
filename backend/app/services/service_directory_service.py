import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.service import ServicePublication
from app.services.audit_service import AuditService


class ServicePublicationError(ValueError):
    pass


class ServicePublicationNotFound(ServicePublicationError):
    pass


class ServicePublicationForbidden(ServicePublicationError):
    pass


class ServiceDirectoryService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def create(
        self,
        tenant_id: str,
        handler_agent_id: str,
        title: str,
        summary: str | None = None,
        visibility: str = "listed",
        contact_policy: str = "auto_accept",
        allow_agent_initiated_chat: bool = True,
        tags: list[str] | None = None,
        capabilities_public: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        service_id: str | None = None,
        actor_id: str | None = None,
    ) -> ServicePublication:
        await self._validate_agent(handler_agent_id, tenant_id)
        self._validate_visibility(visibility)
        self._validate_contact_policy(contact_policy)
        publication = ServicePublication(
            service_id=(service_id or f"svc_{uuid.uuid4().hex[:16]}"),
            tenant_id=tenant_id,
            handler_agent_id=handler_agent_id,
            title=title,
            summary=summary,
            visibility=visibility,
            contact_policy=contact_policy,
            allow_agent_initiated_chat=allow_agent_initiated_chat,
            status="ACTIVE",
            tags=tags or [],
            capabilities_public=capabilities_public or {},
            metadata_json=metadata or {},
        )
        self.db.add(publication)
        await self.audit.log(
            tenant_id,
            "service.publish",
            "service",
            publication.service_id,
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
            payload={"handler_agent_id": handler_agent_id, "visibility": visibility},
        )
        await self.db.flush()
        return publication

    async def update(self, service_id: str, tenant_id: str, actor_id: str | None = None, **fields) -> ServicePublication:
        publication = await self.get_owned(service_id, tenant_id)
        if not publication:
            raise ServicePublicationNotFound(f"service {service_id} 不存在")
        if "handler_agent_id" in fields and fields["handler_agent_id"]:
            await self._validate_agent(fields["handler_agent_id"], tenant_id)
        if "visibility" in fields and fields["visibility"] is not None:
            self._validate_visibility(fields["visibility"])
        if "contact_policy" in fields and fields["contact_policy"] is not None:
            self._validate_contact_policy(fields["contact_policy"])
        payload = {}
        for key, value in fields.items():
            if value is None:
                continue
            attr = "metadata_json" if key == "metadata" else key
            setattr(publication, attr, value)
            payload[attr] = value
        await self.db.execute(
            update(ServicePublication)
            .where(ServicePublication.service_id == service_id, ServicePublication.tenant_id == tenant_id)
            .values(**payload)
        )
        await self.audit.log(
            tenant_id,
            "service.update",
            "service",
            service_id,
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
            payload=payload,
        )
        return publication

    async def get_owned(self, service_id: str, tenant_id: str) -> ServicePublication | None:
        result = await self.db.execute(
            select(ServicePublication).where(
                ServicePublication.service_id == service_id,
                ServicePublication.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_accessible(self, service_id: str, viewer_tenant_id: str) -> ServicePublication | None:
        publication = await self._get(service_id)
        if not publication or publication.status != "ACTIVE":
            return None
        if publication.tenant_id == viewer_tenant_id:
            return publication
        if publication.visibility in {"listed", "direct_link"}:
            return publication
        return None

    async def list_accessible(self, viewer_tenant_id: str) -> list[ServicePublication]:
        result = await self.db.execute(
            select(ServicePublication).where(
                ServicePublication.status == "ACTIVE",
            )
        )
        items = list(result.scalars().all())
        return [
            item for item in items
            if item.tenant_id == viewer_tenant_id or item.visibility == "listed"
        ]

    async def _get(self, service_id: str) -> ServicePublication | None:
        result = await self.db.execute(
            select(ServicePublication).where(ServicePublication.service_id == service_id)
        )
        return result.scalar_one_or_none()

    async def _validate_agent(self, agent_id: str, tenant_id: str) -> None:
        result = await self.db.execute(
            select(Agent).where(
                Agent.agent_id == agent_id,
                Agent.tenant_id == tenant_id,
                Agent.status == "ACTIVE",
            )
        )
        if not result.scalar_one_or_none():
            raise ServicePublicationForbidden(f"handler_agent_id {agent_id} 不存在或不属于当前租户")

    @staticmethod
    def _validate_visibility(visibility: str) -> None:
        if visibility not in {"private", "listed", "direct_link"}:
            raise ServicePublicationError("visibility 必须是 private/listed/direct_link")

    @staticmethod
    def _validate_contact_policy(contact_policy: str) -> None:
        if contact_policy not in {"auto_accept", "request_required", "deny"}:
            raise ServicePublicationError("contact_policy 必须是 auto_accept/request_required/deny")


"""
统一响应结构和通用 Schema
"""
from typing import Any, Generic, TypeVar
from pydantic import BaseModel, Field
import uuid

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一 API 响应结构"""
    request_id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex[:12]}")
    data: T | None = None
    error: dict[str, str] | None = None

    @classmethod
    def ok(cls, data: T, request_id: str | None = None) -> "ApiResponse[T]":
        obj = cls(data=data)
        if request_id:
            obj.request_id = request_id
        return obj

    @classmethod
    def fail(cls, code: str, message: str, request_id: str | None = None) -> "ApiResponse[None]":
        obj = cls(data=None, error={"code": code, "message": message})
        if request_id:
            obj.request_id = request_id
        return obj

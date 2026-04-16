"""
Agent Link MQTT 鉴权辅助：平台与部署脚本共享同一套租户级凭证规则。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from app.core.config import settings


def tenant_mqtt_username(tenant_id: str) -> str:
    """Mosquitto 直接使用 tenant_id 作为用户名，便于 ACL 按租户隔离。"""
    value = str(tenant_id or "").strip()
    if not value:
        raise ValueError("tenant_id 不能为空")
    return value


def tenant_mqtt_password(tenant_id: str, secret: str | None = None) -> str:
    """基于平台密钥派生租户级 MQTT password。"""
    key = str(secret or settings.MQTT_TENANT_PASSWORD_SECRET or "").encode("utf-8")
    if not key:
        raise ValueError("MQTT_TENANT_PASSWORD_SECRET 不能为空")
    digest = hmac.new(key, tenant_mqtt_username(tenant_id).encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def mosquitto_password_hash(password: str, salt: bytes | None = None, iterations: int | None = None) -> str:
    """
    生成 Mosquitto password_file 使用的 PBKDF2-SHA512 哈希。
    兼容 mosquitto_passwd 输出格式：$7$<iterations>$<salt_b64>$<digest_b64>
    """
    rounds = int(iterations or settings.MQTT_PASSWORDFILE_PBKDF2_ITERATIONS)
    raw_salt = salt or secrets.token_bytes(12)
    digest = hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"), raw_salt, rounds)
    return "$7${}${}${}".format(
        rounds,
        base64.b64encode(raw_salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )

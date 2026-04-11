"""
应用配置管理，从环境变量读取，支持 .env 文件
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 应用基础
    APP_ENV: str = "dev"
    APP_NAME: str = "A2A Hub"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False
    DOCS_TEST_ENABLED: bool = True

    # 数据库
    DATABASE_URL: str = "postgresql+asyncpg://a2a_hub:a2a_hub_password@127.0.0.1:1881/a2a_hub"

    # Redis
    REDIS_URL: str = "redis://127.0.0.1:1882/0"

    # 安全
    SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24  # 24小时
    SERVICE_ACCOUNT_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    SERVICE_ACCOUNT_ISSUER_SECRET: str | None = None
    ROCKETCHAT_WEBHOOK_SECRET: str = "dev-rocket-secret"
    OPENCLAW_WEBHOOK_SECRET: str = "dev-openclaw-secret"
    WEBHOOK_NONCE_TTL_SECONDS: int = 300
    A2A_HUB_PUBLIC_BASE_URL: str | None = "http://127.0.0.1:1880"
    OPENCLAW_PUBLIC_BASE_URL: str | None = None  # legacy: use A2A_HUB_PUBLIC_BASE_URL
    AGENT_LINK_TRANSPORT: str = "mqtt"
    AGENT_LINK_PRESENCE_TTL_SECONDS: int = 90
    MQTT_BROKER_URL: str = "mqtt://127.0.0.1:1883"
    MQTT_PUBLIC_BROKER_URL: str | None = None
    MQTT_BASE_TOPIC: str = "a2a-hub"
    MQTT_USERNAME: str | None = None
    MQTT_PASSWORD: str | None = None
    MQTT_SHARED_USERNAME: str | None = "agentlink"
    MQTT_SHARED_PASSWORD: str | None = "agentlink-dev-password"

    # 任务配置
    TASK_MAX_HOP_COUNT: int = 3          # 路由最大跳数
    TASK_DEFAULT_EXPIRES_HOURS: int = 24  # 任务默认过期时间

    # 投递重试配置（秒）
    DELIVERY_RETRY_DELAYS: list[int] = [30, 120, 600, 3600]
    DELIVERY_MAX_ATTEMPTS: int = 8

    @property
    def PUBLIC_BASE_URL(self) -> str:
        """平台对外访问基准 URL。"""
        return (self.A2A_HUB_PUBLIC_BASE_URL or self.OPENCLAW_PUBLIC_BASE_URL or "http://127.0.0.1:1880").rstrip("/")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

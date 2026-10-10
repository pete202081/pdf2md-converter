"""Application configuration.

All settings are read from environment variables (prefix ``PDF2MD_``) and an
optional local ``.env`` file. See ``.env.example`` at the repository root.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the API and the worker."""

    model_config = SettingsConfigDict(env_prefix="PDF2MD_", env_file=".env", extra="ignore")

    app_name: str = "PDF2MD Converter"
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:8080"
    trusted_proxy_cidrs: str = ""  # trust X-Real-IP only from listed reverse-proxy peers
    force_secure_cookies: bool = False  # set True when TLS is terminated ahead of nginx
    auth_limit: int = 6  # per IP AND username / 15 minutes
    signup_limit: int = 4  # per IP AND username / 15 minutes
    pending_account_cap: int = 200
    upload_limit_hour: int = 15
    review_limit_hour: int = 60
    page_view_limit_hour: int = 300
    provider_check_limit_hour: int = 8
    sse_limit: int = 4

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Infrastructure
    database_url: str = "postgresql+asyncpg://pdf2md:pdf2md@db:5432/pdf2md"
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/0"

    # Storage (local Docker volumes by default)
    storage_backend: Literal["local", "minio"] = "local"
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "pdf2md"
    upload_dir: str = "/data/uploads"
    output_dir: str = "/data/outputs"

    settings_key_file: str = "/data/settings-keys/fernet.key"

    # Upload limits
    max_upload_bytes: int = 50 * 1024 * 1024  # 50 MB
    max_pages: int = 1000

    # Diagram-to-Mermaid: local Ollama first, cloud fallback when keys exist.
    vlm_provider: Literal["hybrid", "cloud", "local", "openrouter"] = "hybrid"
    openrouter_api_key: str = ""
    openrouter_vision_model: str = "openai/gpt-4o-mini"
    openrouter_site_url: str = ""  # optional OpenRouter HTTP-Referer
    openrouter_app_name: str = "PDF2MD Converter"  # optional X-OpenRouter-Title
    openai_api_key: str = ""
    openai_vision_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_vision_model: str = "claude-sonnet-4-5"
    ollama_base_url: str = "http://localhost:11434"
    ollama_vision_model: str = "llama3.2-vision"
    vlm_timeout_seconds: float = 90.0  # local vision can be slow on CPU


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()

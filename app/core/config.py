"""Application settings. Secrets come only from environment variables (or a .env file
that is git-ignored). Nothing here may hold a real credential as a default."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Fail closed: an unset APP_ENV means prod (forbids fakes and the default pepper).
    app_env: Literal["dev", "test", "prod"] = "prod"
    log_level: str = "INFO"

    # --- persistence -------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://emporio:emporio@localhost:5434/emporio"
    redis_url: str = "redis://localhost:6380/0"

    # --- auth --------------------------------------------------------------------------
    # Pepper mixed into API-key hashes. Must be overridden outside dev/test.
    api_key_pepper: SecretStr = SecretStr("dev-only-pepper-change-me")
    # Separate HMAC key for channel user references (e.g. WhatsApp wa_id -> opaque ref).
    user_ref_key: SecretStr = SecretStr("dev-only-user-ref-key-change-me")
    expose_api_docs: bool = True  # forced off in prod by the validator below

    # --- model providers ---------------------------------------------------------------
    # "fake" providers are deterministic and allowed only when app_env != "prod".
    llm_provider: Literal["openai", "fake"] = "openai"
    embedding_provider: Literal["openai", "hash"] = "openai"
    openai_api_key: SecretStr | None = None
    openai_chat_model: str = "gpt-5-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536  # fixed by the migration's vector column
    model_timeout_seconds: float = 30.0
    max_agent_turns: int = 8
    # Agents SDK tracing exports to OpenAI; disabled by default (privacy policy).
    openai_tracing_enabled: bool = False

    # --- RAG -----------------------------------------------------------------------------
    chunk_size_tokens: int = Field(default=650, ge=100, le=2000)
    chunk_overlap_tokens: int = Field(default=80, ge=0, le=500)
    max_upload_bytes: int = 10 * 1024 * 1024
    max_text_chars: int = 500_000
    retrieval_top_k: int = 6
    retrieval_candidate_k: int = 20
    retrieval_min_similarity: float = 0.30
    rrf_k: int = 60
    query_embedding_cache_ttl_seconds: int = 3600
    ingestion_mode: Literal["inline", "queue"] = "queue"

    # --- tools / integrations ------------------------------------------------------------
    tool_timeout_seconds: float = 5.0
    db_statement_timeout_ms: int = 5000

    # --- abuse protection / retention ------------------------------------------------
    # Failed order verifications per (tenant, order) and per (tenant, actor) in the window.
    order_lookup_max_failures: int = 3
    order_lookup_lockout_seconds: int = 3600
    # Requests per minute per API key on /v1/chat/messages and per channel user.
    chat_rate_limit_per_minute: int = 30
    # Failed authentications per minute per client IP before 429.
    auth_failure_limit_per_minute: int = 20
    # Raw conversation text, tool executions, audit events and handoffs older than this are
    # purged by the retention job (SECURITY.md: bounded retention).
    retention_days: int = 90
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_graph_api_base: str = "https://graph.facebook.com/v21.0"

    @model_validator(mode="after")
    def _guard_prod(self) -> Settings:
        if self.app_env == "prod":
            if self.llm_provider == "fake" or self.embedding_provider == "hash":
                raise ValueError("Deterministic fake providers are not allowed in prod")
            if self.api_key_pepper.get_secret_value() == "dev-only-pepper-change-me":
                raise ValueError("API_KEY_PEPPER must be set in prod")
            if self.user_ref_key.get_secret_value() == "dev-only-user-ref-key-change-me":
                raise ValueError("USER_REF_KEY must be set in prod")
            self.expose_api_docs = False
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk_size_tokens")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

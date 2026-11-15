"""Application settings loaded from environment variables (see `.env.example`).

All configuration lives here so that every module reads a single, typed
`Settings` object. Feature availability (TomTom, push, email, Langfuse, LLM)
is derived from the presence of keys so that missing keys degrade gracefully.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_NEWS_QUERIES = [
    "delhi traffic advisory",
    "delhi traffic diversion",
    "delhi road closed",
    "delhi waterlogging",
    "delhi traffic jam",
    "delhi accident road",
    "दिल्ली ट्रैफिक",
    "दिल्ली जाम",
]


def _split_csv(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    # --- App ---
    app_env: Literal["development", "production", "test"] = "development"
    app_base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"
    admin_token: str = "change-me-admin-token"
    timezone: str = "Asia/Kolkata"
    log_level: str = "INFO"
    max_query_chars: int = 1000
    data_dir: Path = Path("./data")
    user_agent: str = "DelhiTrafficCopilot/0.1 (+https://github.com/Vansh-456/Traffic_RAG)"
    crawl_delay_seconds: float = 2.0

    # --- Datastores ---
    database_url: str = "postgresql+asyncpg://copilot:copilot@localhost:5432/copilot"
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM ---
    llm_provider: str = "groq"
    llm_model_answer: str = "groq/openai/gpt-oss-120b"
    llm_model_fast: str = "groq/llama-3.1-8b-instant"
    llm_model_fallback: str = "gemini/gemini-2.0-flash"
    llm_rpm_limit: int = 28
    llm_timeout_seconds: float = 45.0
    llm_max_retries: int = 3
    groq_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"

    # --- Embeddings / reranker ---
    embedding_provider: Literal["local", "api", "hash"] = "hash"
    embedding_model: str = "BAAI/bge-m3"
    embedding_api_key: str = ""
    embedding_dim: int = 1024
    reranker_provider: Literal["local", "cohere", "lexical"] = "lexical"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    cohere_api_key: str = ""

    # --- TomTom ---
    tomtom_api_key: str = ""
    tomtom_daily_budget: int = 2300

    # --- News ---
    # comma-separated in the environment; parsed by the properties below
    news_queries_csv: str = Field(",".join(DEFAULT_NEWS_QUERIES), alias="NEWS_QUERIES")
    extra_rss_feeds_csv: str = Field("", alias="EXTRA_RSS_FEEDS")
    enable_gdelt_backfill: bool = False
    news_fulltext: bool = True

    # --- Geo ---
    nominatim_enabled: bool = False
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    osrm_url: str = ""

    # --- Observability ---
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # --- Alerts ---
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"
    resend_api_key: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "Delhi Traffic Copilot <alerts@example.com>"
    alert_corridor_meters: int = 300
    alert_congestion_ratio: float = 0.5

    # --- RAG tuning ---
    cache_sim_threshold: float = 0.95
    dedup_sim_threshold: float = 0.92
    relevance_threshold: float = 0.15
    rate_limit_per_min: int = 20
    retrieval_top_k: int = 8
    rerank_candidates: int = 40

    # --- Evaluation ---
    eval_database_url: str = ""  # default: DATABASE_URL with db name 'copilot_eval'
    eval_regression_margin: float = 0.05
    publish_events: bool = True  # Redis pub/sub for new chunks (off in eval runs)

    # --- Retention ---
    raw_retention_days: int = 60
    flow_downsample_after_days: int = 30

    # --- Scheduler ---
    schedule_dtp: str = "*/30 * * * *"
    schedule_news: str = "*/30 * * * *"
    schedule_rss: str = "*/30 * * * *"
    schedule_tomtom_tick: str = "*/5 * * * *"
    schedule_weather: str = "0 * * * *"
    schedule_gazetteer: str = "0 4 1 * *"
    schedule_expire: str = "*/15 * * * *"
    schedule_alerts: str = "*/5 * * * *"
    schedule_retention: str = "0 3 * * *"
    schedule_eval: str = "0 4 * * *"

    @property
    def news_queries(self) -> list[str]:
        return _split_csv(self.news_queries_csv) or list(DEFAULT_NEWS_QUERIES)

    @property
    def extra_rss_feeds(self) -> list[str]:
        return _split_csv(self.extra_rss_feeds_csv)

    # --- Derived feature flags ---
    @property
    def llm_enabled(self) -> bool:
        """True when at least one LLM provider is usable."""
        return bool(
            self.groq_api_key
            or self.gemini_api_key
            or self.anthropic_api_key
            or self.openai_api_key
            or self.llm_provider == "ollama"
        )

    @property
    def tomtom_enabled(self) -> bool:
        return bool(self.tomtom_api_key)

    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key)

    @property
    def email_enabled(self) -> bool:
        return bool(self.resend_api_key or self.smtp_host)

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def resolved_eval_database_url(self) -> str:
        if self.eval_database_url:
            return self.eval_database_url
        base, _, _name = self.database_url.rpartition("/")
        return f"{base}/copilot_eval"

    @property
    def sync_database_url(self) -> str:
        """psycopg (sync) URL for Alembic and scripts."""
        return self.database_url.replace("+asyncpg", "+psycopg")

    def feature_flags(self) -> dict[str, bool]:
        """Public summary of enabled features (shown in UI notices / admin)."""
        return {
            "llm": self.llm_enabled,
            "tomtom": self.tomtom_enabled,
            "push": self.push_enabled,
            "email": self.email_enabled,
            "langfuse": self.langfuse_enabled,
            "local_embeddings": self.embedding_provider == "local",
            "nominatim": self.nominatim_enabled,
        }


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()

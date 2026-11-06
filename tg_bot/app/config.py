from typing import Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: SecretStr = SecretStr("")

    answer_cache_ttl_seconds: int = 86400
    question_rate_limit: int = 10
    question_rate_window_seconds: int = 60
    password_attempts_limit: int = 5
    password_attempts_window_seconds: int = 900
    password_reveal_ttl_seconds: int = 600
    docs_max_files: int = 10

    # Постепенный показ ответа (PR #2 replay path для stub/final-only gateway).
    answer_streaming_enabled: bool = True
    answer_stream_replay_seconds: float = Field(default=1.5, ge=0, le=10)
    answer_stream_draft_interval_seconds: float = Field(default=0.3, gt=0)
    answer_stream_edit_interval_seconds: float = Field(default=1.0, gt=0)
    answer_stream_keepalive_seconds: float = Field(default=20, gt=0, lt=30)

    # Real RAG: stub — dev/test без публикации; real требует provider config и
    # published generation, иначе startup fail-closed.
    rag_mode: Literal["stub", "real"] = "stub"
    rag_qdrant_path: str = "data/qdrant"
    rag_qdrant_url: str = ""
    rag_qdrant_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_seconds: float = 60.0
    llm_max_output_tokens: int = Field(default=1024, ge=1, le=131072)
    llm_max_concurrent_generations: int = Field(default=1, ge=1, le=64)
    llm_include_usage: bool = False
    llm_chat_completions_path: str = "/chat/completions"

    # Часовой пояс расписания заданий (IANA, например Europe/Moscow)
    scheduler_timezone: str = "Europe/Moscow"
    # Еженедельная сводка админам: день недели (0 — понедельник) и час
    digest_weekday: int = Field(default=0, ge=0, le=6)
    digest_hour: int = Field(default=10, ge=0, le=23)

    log_level: str = "INFO"

    @field_validator("scheduler_timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"Unknown timezone {value!r}") from error
        return value

    @property
    def postgres_dsn(self) -> str:
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password.get_secret_value(), safe="")
        return (
            f"postgres://{user}:{password}@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        password = self.redis_password.get_secret_value()
        auth = f":{quote(password, safe='')}@" if password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

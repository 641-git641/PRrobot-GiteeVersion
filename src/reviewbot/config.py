from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AuthMode = Literal["query", "header"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )
    gitee_api_base_url: AnyHttpUrl = Field("https://gitee.com/api/v5", alias="GITEE_API_BASE_URL")
    gitee_api_token: SecretStr | None = Field(None, alias="GITEE_API_TOKEN")
    gitee_webhook_secret: SecretStr | None = Field(None, alias="GITEE_WEBHOOK_SECRET")
    gitee_repo_allowlist_raw: str = Field("", alias="GITEE_REPO_ALLOWLIST")
    gitee_auth_mode: AuthMode = Field("query", alias="GITEE_AUTH_MODE")
    gitee_bot_login: str | None = Field(None, alias="GITEE_BOT_LOGIN")

    deepseek_api_base_url: AnyHttpUrl = Field("https://api.deepseek.com", alias="DEEPSEEK_API_BASE_URL")
    deepseek_api_key: SecretStr | None = Field(None, alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field("deepseek-chat", alias="DEEPSEEK_MODEL")
    deepseek_thinking_enabled: bool = Field(False, alias="DEEPSEEK_THINKING_ENABLED")
    deepseek_reasoning_effort: Literal["low", "high", "max"] = Field("high", alias="DEEPSEEK_REASONING_EFFORT")

    bind_host: str = Field("0.0.0.0", alias="ROBOT_BIND_HOST")
    bind_port: int = Field(8090, alias="ROBOT_BIND_PORT", gt=0, le=65535)
    database_path: Path = Field(Path("data/review-bot.sqlite3"), alias="ROBOT_DATABASE_PATH")
    review_rule_file: Path = Field(Path("review-rules.md"), alias="ROBOT_REVIEW_RULE_FILE")
    max_diff_bytes: int = Field(200_000, alias="ROBOT_MAX_DIFF_BYTES", gt=0)
    max_review_bytes: int = Field(50_000, alias="ROBOT_MAX_REVIEW_BYTES", gt=0)
    request_timeout_seconds: float = Field(90.0, alias="ROBOT_REQUEST_TIMEOUT_SECONDS", gt=0)
    max_retries: int = Field(2, alias="ROBOT_MAX_RETRIES", ge=0, le=5)
    review_enabled: bool = Field(True, alias="ROBOT_REVIEW_ENABLED")

    @property
    def repo_allowlist(self) -> frozenset[str]:
        return frozenset(item.strip().lower() for item in self.gitee_repo_allowlist_raw.split(",") if item.strip())

    @property
    def api_base_url(self) -> str:
        return str(self.gitee_api_base_url).rstrip("/")

    @property
    def deepseek_base_url(self) -> str:
        return str(self.deepseek_api_base_url).rstrip("/")

    def validate_runtime(self) -> None:
        missing: list[str] = []
        if self.gitee_api_token is None or not self.gitee_api_token.get_secret_value().strip():
            missing.append("GITEE_API_TOKEN")
        if self.gitee_webhook_secret is None or not self.gitee_webhook_secret.get_secret_value().strip():
            missing.append("GITEE_WEBHOOK_SECRET")
        if self.deepseek_api_key is None or not self.deepseek_api_key.get_secret_value().strip():
            missing.append("DEEPSEEK_API_KEY")
        if not self.repo_allowlist:
            missing.append("GITEE_REPO_ALLOWLIST")
        if missing:
            raise ValueError(f"missing required robot configuration: {', '.join(missing)}")

    @field_validator("deepseek_model", mode="before")
    @classmethod
    def reject_blank_text(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("configuration value must not be blank")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime()
    return settings

import pytest
from pydantic import SecretStr

from reviewbot.config import Settings


def test_runtime_configuration_requires_all_external_credentials() -> None:
    settings = Settings()

    with pytest.raises(ValueError, match="GITEE_API_TOKEN"):
        settings.validate_runtime()


def test_runtime_configuration_accepts_named_fields_and_normalizes_repositories() -> None:
    settings = Settings(
        gitee_api_token=SecretStr("gitee"),
        gitee_webhook_secret=SecretStr("webhook"),
        gitee_repo_allowlist_raw=" Owner/Repo,owner/other ",
        deepseek_api_key=SecretStr("deepseek"),
    )

    settings.validate_runtime()
    assert settings.repo_allowlist == frozenset({"owner/repo", "owner/other"})

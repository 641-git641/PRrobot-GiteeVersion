from reviewbot.security import redact_sensitive_text


def test_redaction_removes_common_credentials_from_output() -> None:
    text = "Authorization: Bearer abc123 api_key=secret-value password:pw123 sk-abcdefghijklmnop"

    redacted = redact_sensitive_text(text)

    assert "abc123" not in redacted
    assert "secret-value" not in redacted
    assert "pw123" not in redacted
    assert "abcdefghijklmnop" not in redacted
    assert "[REDACTED]" in redacted

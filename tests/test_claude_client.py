"""ClaudeClient tests with a mocked Anthropic SDK client - no network calls."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx2
import pytest

from app.claude.client import (
    FALLBACK_BETA,
    ClaudeAuthError,
    ClaudeClient,
    ClaudeConnectionError,
    ClaudeMalformedResponseError,
    ClaudeRateLimitError,
    ClaudeRefusalError,
    ClaudeTimeoutError,
)
from app.claude.prompts import ANALYSIS_JSON_SCHEMA, SYSTEM_PROMPT, build_user_prompt

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
    )


def _client(settings, *, returns=None, raises=None):
    sdk = MagicMock()
    if raises is not None:
        sdk.beta.messages.create.side_effect = raises
    else:
        sdk.beta.messages.create.return_value = returns
    return ClaudeClient(settings, client=sdk), sdk


def _status_error(cls, status):
    return cls("error", response=httpx2.Response(status, request=_REQUEST), body=None)


def test_successful_analysis(settings, sample_posts, valid_analysis):
    client, sdk = _client(settings, returns=_response(json.dumps(valid_analysis)))
    result = client.analyze_reddit_post(sample_posts["t1dep01"])

    assert result.legal_category == "Landlord and Tenant"
    kwargs = sdk.beta.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["system"][0]["text"] == SYSTEM_PROMPT
    assert kwargs["output_config"]["format"]["schema"] is ANALYSIS_JSON_SCHEMA
    assert kwargs["betas"] == [FALLBACK_BETA]
    assert kwargs["fallbacks"] == "default"
    assert "security deposit" in kwargs["messages"][0]["content"].lower()


def test_fallback_disabled_for_unsupported_model(settings, sample_posts):
    settings.claude_model = "claude-haiku-4-5"
    client, _ = _client(settings)
    request = client.build_request(sample_posts["t1dep01"])
    assert "betas" not in request and "fallbacks" not in request


def test_code_fenced_json_is_accepted(settings, sample_posts, valid_analysis):
    client, _ = _client(settings, returns=_response(f"```json\n{json.dumps(valid_analysis)}\n```"))
    assert client.analyze_reddit_post(sample_posts["t1dep01"]).relevant is True


@pytest.mark.parametrize(
    "text, stop_reason, error",
    [
        ("not json at all", "end_turn", ClaudeMalformedResponseError),
        ('{"relevant": true}', "end_turn", ClaudeMalformedResponseError),  # schema-invalid
        ("", "end_turn", ClaudeMalformedResponseError),
        ('{"relevant": tr', "max_tokens", ClaudeMalformedResponseError),
        ("", "refusal", ClaudeRefusalError),
    ],
)
def test_bad_responses(settings, sample_posts, text, stop_reason, error):
    client, _ = _client(settings, returns=_response(text, stop_reason))
    with pytest.raises(error):
        client.analyze_reddit_post(sample_posts["t1dep01"])


@pytest.mark.parametrize(
    "exc, expected",
    [
        (_status_error(anthropic.AuthenticationError, 401), ClaudeAuthError),
        (_status_error(anthropic.PermissionDeniedError, 403), ClaudeAuthError),
        (_status_error(anthropic.RateLimitError, 429), ClaudeRateLimitError),
        (anthropic.APITimeoutError(request=_REQUEST), ClaudeTimeoutError),
        (anthropic.APIConnectionError(request=_REQUEST), ClaudeConnectionError),
    ],
)
def test_sdk_errors_are_mapped(settings, sample_posts, exc, expected):
    client, _ = _client(settings, raises=exc)
    with pytest.raises(expected) as info:
        client.analyze_reddit_post(sample_posts["t1dep01"])
    assert "sk-ant-test-key-value" not in str(info.value)


def test_missing_api_key_is_a_configuration_error(settings):
    from app.config import ConfigurationError

    settings.anthropic_api_key = None
    with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY"):
        ClaudeClient(settings)


def test_user_prompt_contains_post_fields(sample_posts):
    prompt = build_user_prompt(sample_posts["t1non04"])
    assert "r/law" in prompt
    assert "sourdough" in prompt
    assert "(none)" in prompt  # no flair
    assert "Do not provide definitive legal advice." in prompt

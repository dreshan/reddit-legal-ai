"""Claude API client built on the official Anthropic Python SDK.

* JSON output is enforced with structured outputs (``output_config.format``)
  and then validated again locally with Pydantic.
* Refusals are retried on another model server-side via ``fallbacks: "default"``
  (beta) when the configured model supports it. Disable with
  ``CLAUDE_ENABLE_FALLBACK=false``.
* SDK-level retries (429 / 5xx / connection errors) are controlled by
  ``CLAUDE_MAX_RETRIES``; what's left is mapped to typed ``ClaudeError``s whose
  messages are safe to log and display (never the API key or post content).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import anthropic
from pydantic import ValidationError

from app.claude.prompts import ANALYSIS_JSON_SCHEMA, SYSTEM_PROMPT, build_user_prompt
from app.config import Settings
from app.models.schemas import ClaudeAnalysisResponse, RedditPost

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept the server-side `fallbacks: "default"` parameter.
FALLBACK_CAPABLE_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

_CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class ClaudeError(Exception):
    """Base class for Claude failures. Messages are safe to display."""


class ClaudeAuthError(ClaudeError):
    pass


class ClaudeRateLimitError(ClaudeError):
    pass


class ClaudeTimeoutError(ClaudeError):
    pass


class ClaudeConnectionError(ClaudeError):
    pass


class ClaudeAPIError(ClaudeError):
    pass


class ClaudeRefusalError(ClaudeError):
    pass


class ClaudeMalformedResponseError(ClaudeError):
    pass


class ClaudeClient:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        if client is None:
            settings.require_anthropic()
            client = anthropic.Anthropic(
                api_key=settings.anthropic_api_key.get_secret_value(),  # type: ignore[union-attr]
                timeout=settings.claude_timeout_seconds,
                max_retries=settings.claude_max_retries,
            )
        self._client = client
        self.model = settings.claude_model
        self.effort = settings.claude_effort
        self.max_tokens = settings.claude_max_tokens
        self.use_fallback = settings.claude_enable_fallback and self.model in FALLBACK_CAPABLE_MODELS

    def build_request(self, post: RedditPost) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": build_user_prompt(post)}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": ANALYSIS_JSON_SCHEMA},
            },
        }
        if self.use_fallback:
            request["betas"] = [FALLBACK_BETA]
            request["fallbacks"] = "default"
        return request

    def analyze_reddit_post(self, post: RedditPost) -> ClaudeAnalysisResponse:
        log_ctx = {"post_id": post.id, "subreddit": post.subreddit, "model": self.model}
        logger.info("Claude request started", extra=log_ctx)
        started = time.monotonic()

        try:
            response = self._client.beta.messages.create(**self.build_request(post))
        except anthropic.AuthenticationError as exc:
            raise ClaudeAuthError("Claude authentication failed (HTTP 401) - check ANTHROPIC_API_KEY.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ClaudeAuthError(f"API key is not permitted to use model '{self.model}' (HTTP 403).") from exc
        except anthropic.NotFoundError as exc:
            raise ClaudeAPIError(f"Model '{self.model}' was not found (HTTP 404) - check CLAUDE_MODEL.") from exc
        except anthropic.RateLimitError as exc:
            raise ClaudeRateLimitError("Claude rate limit exceeded (HTTP 429) after retries.") from exc
        except anthropic.APITimeoutError as exc:  # subclass of APIConnectionError - must come first
            raise ClaudeTimeoutError("Claude request timed out.") from exc
        except anthropic.APIConnectionError as exc:
            raise ClaudeConnectionError("Network error contacting the Claude API.") from exc
        except anthropic.APIStatusError as exc:
            raise ClaudeAPIError(f"Claude API error (HTTP {exc.status_code}): {str(exc.message)[:300]}") from exc

        elapsed = round(time.monotonic() - started, 2)
        usage = getattr(response, "usage", None)
        logger.info(
            "Claude response received",
            extra={
                **log_ctx,
                "served_by": getattr(response, "model", self.model),
                "stop_reason": response.stop_reason,
                "seconds": elapsed,
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            },
        )
        return self._parse(response, post.id)

    @staticmethod
    def _parse(response: Any, post_id: str) -> ClaudeAnalysisResponse:
        if response.stop_reason == "refusal":
            raise ClaudeRefusalError("Claude declined to analyse this post (stop_reason=refusal).")
        if response.stop_reason == "max_tokens":
            raise ClaudeMalformedResponseError("Claude response was truncated (max_tokens reached).")

        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text").strip()
        if not text:
            raise ClaudeMalformedResponseError("Claude response contained no text content.")
        fenced = _CODE_FENCE.match(text)
        if fenced:
            text = fenced.group(1)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            # Log position/length only - not the content itself.
            logger.warning(
                "Claude returned invalid JSON",
                extra={"post_id": post_id, "error": exc.msg, "position": exc.pos, "response_chars": len(text)},
            )
            raise ClaudeMalformedResponseError(f"Claude returned invalid JSON ({exc.msg}).") from exc

        try:
            return ClaudeAnalysisResponse.model_validate(data)
        except ValidationError as exc:
            fields = sorted({".".join(str(p) for p in err["loc"]) for err in exc.errors(include_input=False)})
            logger.warning("Claude JSON failed validation", extra={"post_id": post_id, "fields": ",".join(fields)})
            raise ClaudeMalformedResponseError(f"Claude JSON failed validation for: {', '.join(fields)}.") from exc

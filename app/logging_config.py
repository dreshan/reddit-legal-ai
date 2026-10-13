"""Structured logging with secret redaction.

Use ``extra={...}`` on log calls to attach structured fields, e.g.::

    logger.info("Posts retrieved", extra={"subreddit": "law", "count": 20})

Text format renders them as ``key=value`` pairs; JSON format emits one JSON
object per line. A redaction filter masks any configured secret value that
somehow ends up in a log message (defence in depth - code should never log
secrets in the first place).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}
_REDACTED = "***REDACTED***"


def _extras(record: logging.LogRecord) -> dict[str, object]:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS and not k.startswith("_")}


class SecretRedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        # Very short values would cause false-positive masking; real secrets are long.
        self._secrets = [s for s in secrets if s and len(s) >= 6]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        message = record.getMessage()
        redacted = message
        for secret in self._secrets:
            redacted = redacted.replace(secret, _REDACTED)
        if redacted != message:
            record.msg, record.args = redacted, None
        for key, value in _extras(record).items():
            if isinstance(value, str):
                for secret in self._secrets:
                    value = value.replace(secret, _REDACTED)
                setattr(record, key, value)
        return True


class KeyValueFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s - %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = _extras(record)
        if extras:
            base += " | " + " ".join(f"{k}={v}" for k, v in extras.items())
        return base


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_extras(record),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", fmt: str = "text", secrets: list[str] | None = None) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else KeyValueFormatter())
    handler.addFilter(SecretRedactingFilter(secrets or []))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Third-party HTTP clients can log request details at DEBUG/INFO.
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "prawcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

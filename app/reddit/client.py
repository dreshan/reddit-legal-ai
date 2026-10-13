"""Reddit API client (the only module that talks to PRAW).

Authentication approach
-----------------------
* Default: **application-only OAuth** (``client_credentials`` grant) in
  PRAW *read-only* mode. Needs ``REDDIT_CLIENT_ID``, ``REDDIT_CLIENT_SECRET``
  and ``REDDIT_USER_AGENT`` only. No redirect, no user login.
* Optional: if ``REDDIT_USERNAME`` and ``REDDIT_PASSWORD`` are both set, PRAW
  uses the **script-app password grant** instead (acts as that user). This is
  not needed for reading public subreddits.

``get_posts`` returns plain dicts containing only the fields the platform
needs, so the rest of the app never depends on PRAW objects.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import praw
import praw.exceptions
from prawcore import exceptions as pce

from app.config import Settings

logger = logging.getLogger(__name__)

REDDIT_BASE_URL = "https://www.reddit.com"
_SUBREDDIT_NAME = re.compile(r"^[A-Za-z0-9_]{2,21}$")
# Older prawcore versions lack TooManyRequests; fall back to a never-raised type.
_TooManyRequests = getattr(pce, "TooManyRequests", type("_NeverRaised", (Exception,), {}))


class RedditClientError(Exception):
    """Base class for Reddit client failures. Messages are safe to display."""


class RedditAuthError(RedditClientError):
    pass


class RedditRateLimitError(RedditClientError):
    pass


class RedditUnavailableError(RedditClientError):
    pass


class SubredditNotFoundError(RedditClientError):
    pass


class RedditClient:
    def __init__(self, settings: Settings, reddit: Any | None = None) -> None:
        self._settings = settings
        self._reddit = reddit if reddit is not None else self._build(settings)

    @staticmethod
    def _build(settings: Settings) -> praw.Reddit:
        settings.require_reddit()
        kwargs: dict[str, Any] = {
            "client_id": settings.reddit_client_id,
            "client_secret": settings.reddit_client_secret.get_secret_value(),  # type: ignore[union-attr]
            "user_agent": settings.reddit_user_agent,
            "check_for_async": False,
        }
        if settings.uses_reddit_password_auth:
            kwargs["username"] = settings.reddit_username
            kwargs["password"] = settings.reddit_password.get_secret_value()  # type: ignore[union-attr]

        reddit = praw.Reddit(**kwargs)
        if not settings.uses_reddit_password_auth:
            reddit.read_only = True
        logger.info("Reddit client initialised", extra={"auth_mode": RedditClient._mode(settings)})
        return reddit

    @staticmethod
    def _mode(settings: Settings) -> str:
        return "script_password_grant" if settings.uses_reddit_password_auth else "application_only_read_only"

    @property
    def auth_mode(self) -> str:
        return self._mode(self._settings)

    # ------------------------------------------------------------------ API

    def test_connection(self) -> dict[str, Any]:
        """Force an OAuth token request and report the result."""
        logger.info("Testing Reddit connection", extra={"auth_mode": self.auth_mode})
        with self._translate_errors():
            scopes = self._reddit.auth.scopes()
            username = str(self._reddit.user.me()) if self._settings.uses_reddit_password_auth else None
        logger.info("Reddit connection OK", extra={"auth_mode": self.auth_mode})
        return {
            "connected": True,
            "auth_mode": self.auth_mode,
            "read_only": bool(self._reddit.read_only),
            "scopes": sorted(scopes),
            "username": username,
        }

    def get_posts(self, subreddit_name: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent posts from a subreddit as minimal plain dicts."""
        if not _SUBREDDIT_NAME.match(subreddit_name):
            raise SubredditNotFoundError(f"'{subreddit_name}' is not a valid subreddit name.")

        listing_name = self._settings.reddit_listing
        logger.info("Fetching posts", extra={"subreddit": subreddit_name, "listing": listing_name, "limit": limit})
        with self._translate_errors(subreddit_name):
            listing = getattr(self._reddit.subreddit(subreddit_name), listing_name)(limit=limit)
            posts = [self._to_dict(submission) for submission in listing]
        logger.info("Posts retrieved", extra={"subreddit": subreddit_name, "count": len(posts)})
        return posts

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _to_dict(submission: Any) -> dict[str, Any]:
        author = submission.author  # None when the account is deleted
        return {
            "id": submission.id,
            "subreddit": str(submission.subreddit),
            "title": submission.title,
            "body": submission.selftext or "",
            "author": author.name if author is not None else None,
            "created_at": datetime.fromtimestamp(submission.created_utc, tz=timezone.utc),
            "score": submission.score,
            "num_comments": submission.num_comments,
            "permalink": REDDIT_BASE_URL + submission.permalink,
            "url": submission.url,
            "flair": submission.link_flair_text,
            "nsfw": bool(getattr(submission, "over_18", False)),
        }

    @contextmanager
    def _translate_errors(self, subreddit: str | None = None) -> Iterator[None]:
        """Map PRAW/prawcore exceptions onto safe, typed client errors."""
        where = f"r/{subreddit}" if subreddit else "Reddit"
        try:
            yield
        except pce.OAuthException as exc:
            raise RedditAuthError(
                "Reddit authentication failed - check REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET "
                "(and REDDIT_USERNAME / REDDIT_PASSWORD if used)."
            ) from exc
        except (pce.NotFound, pce.Redirect) as exc:
            raise SubredditNotFoundError(f"{where} does not exist or is not available.") from exc
        except pce.Forbidden as exc:
            if subreddit:
                raise SubredditNotFoundError(f"{where} is private, quarantined or banned (HTTP 403).") from exc
            raise RedditAuthError("Reddit refused access (HTTP 403) - check the app is approved for API access.") from exc
        except _TooManyRequests as exc:
            raise RedditRateLimitError("Reddit rate limit exceeded (HTTP 429). Wait and retry.") from exc
        except pce.ServerError as exc:
            raise RedditUnavailableError(f"Reddit API is unavailable (server error) while reading {where}.") from exc
        except pce.ResponseException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 401:
                raise RedditAuthError(
                    "Reddit rejected the credentials (HTTP 401) - check REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET."
                ) from exc
            if status == 429:
                raise RedditRateLimitError("Reddit rate limit exceeded (HTTP 429). Wait and retry.") from exc
            raise RedditClientError(f"Reddit API error (HTTP {status}) while reading {where}.") from exc
        except pce.RequestException as exc:
            raise RedditUnavailableError(f"Network error contacting Reddit: {type(exc.original_exception).__name__}.") from exc
        except praw.exceptions.PRAWException as exc:
            raise RedditClientError(f"Reddit client error: {type(exc).__name__}.") from exc

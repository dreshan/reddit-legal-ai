"""Collects posts from several subreddits into validated ``RedditPost`` models."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError

from app.models.schemas import RedditPost
from app.reddit.client import RedditAuthError, RedditClientError, RedditRateLimitError

logger = logging.getLogger(__name__)


class PostSource(Protocol):
    """Anything that can return raw post dicts - RedditClient or a test fake."""

    def get_posts(self, subreddit_name: str, limit: int = 20) -> list[dict[str, Any]]: ...


@dataclass
class CollectionResult:
    posts: list[RedditPost] = field(default_factory=list)
    per_subreddit: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    duplicates_removed: int = 0
    invalid_posts: int = 0


def _fingerprint(post: RedditPost) -> tuple[str, str]:
    """Identical title+body (or title+url for link posts) = obvious duplicate/crosspost."""
    body = " ".join(post.body.lower().split())
    return " ".join(post.title.lower().split()), body or post.url


def collect(subreddits: list[str], limit: int, reddit_client: PostSource) -> CollectionResult:
    """Fetch, validate and de-duplicate posts. One failing subreddit does not stop the rest.

    Authentication failures are re-raised (every subreddit would fail). A rate
    limit stops further fetching but keeps the posts already collected.
    """
    result = CollectionResult()
    seen_ids: set[str] = set()
    seen_fingerprints: set[tuple[str, str]] = set()

    for name in subreddits:
        try:
            raw_posts = reddit_client.get_posts(name, limit=limit)
        except RedditAuthError:
            raise
        except RedditRateLimitError as exc:
            logger.warning("Rate limited - stopping collection", extra={"subreddit": name})
            result.errors[name] = str(exc)
            break
        except RedditClientError as exc:
            logger.error("Failed to fetch subreddit", extra={"subreddit": name, "error": str(exc)})
            result.errors[name] = str(exc)
            continue

        kept = 0
        for raw in raw_posts:
            try:
                post = RedditPost.model_validate(raw)
            except ValidationError as exc:
                result.invalid_posts += 1
                logger.warning(
                    "Skipping post that failed validation",
                    extra={"subreddit": name, "post_id": raw.get("id"), "error_count": exc.error_count()},
                )
                continue

            fingerprint = _fingerprint(post)
            if post.id in seen_ids or fingerprint in seen_fingerprints:
                result.duplicates_removed += 1
                continue
            seen_ids.add(post.id)
            seen_fingerprints.add(fingerprint)
            result.posts.append(post)
            kept += 1

        result.per_subreddit[name] = kept

    logger.info(
        "Collection finished",
        extra={
            "total_posts": len(result.posts),
            "duplicates_removed": result.duplicates_removed,
            "failed_subreddits": len(result.errors),
        },
    )
    return result


def collect_posts(subreddits: list[str], limit: int, reddit_client: PostSource) -> list[RedditPost]:
    """Convenience wrapper returning only the de-duplicated posts."""
    return collect(subreddits, limit, reddit_client).posts

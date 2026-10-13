"""FastAPI application.

Run with:  uvicorn app.main:app --reload
Docs at:   http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.claude.client import ClaudeAuthError, ClaudeClient, ClaudeError
from app.config import ConfigurationError, Settings, get_settings
from app.filtering.legal_filter import LegalFilter
from app.logging_config import configure_logging
from app.models.schemas import AI_DISCLAIMER, ProcessSummary
from app.reddit.client import (
    RedditAuthError,
    RedditClient,
    RedditClientError,
    RedditRateLimitError,
    SubredditNotFoundError,
)
from app.reddit.collector import collect
from app.services.pipeline import LegalContentPipeline
from app.services.storage import JsonResultStore


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        settings = get_settings()
        configure_logging(settings.log_level, settings.log_format, settings.secret_values())
    except ConfigurationError:
        configure_logging()  # endpoints will report the configuration problem
    yield


app = FastAPI(
    title="Reddit Legal Content AI (prototype)",
    version="0.1.0",
    description=f"Local proof of concept. All AI output is an {AI_DISCLAIMER}",
    lifespan=lifespan,
)


# --------------------------------------------------------------- dependencies


@lru_cache(maxsize=1)
def get_reddit_client() -> RedditClient:
    return RedditClient(get_settings())


@lru_cache(maxsize=1)
def get_claude_client() -> ClaudeClient:
    return ClaudeClient(get_settings())


def get_legal_filter(settings: Settings = Depends(get_settings)) -> LegalFilter:
    return LegalFilter.from_settings(settings)


# ------------------------------------------------------------ error handling


def _error(status: int, kind: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": kind, "detail": detail})


@app.exception_handler(ConfigurationError)
async def _config_error(_: Request, exc: ConfigurationError) -> JSONResponse:
    return _error(500, "configuration_error", str(exc))


@app.exception_handler(RedditClientError)
async def _reddit_error(_: Request, exc: RedditClientError) -> JSONResponse:
    if isinstance(exc, RedditAuthError):
        return _error(502, "reddit_auth_error", str(exc))
    if isinstance(exc, RedditRateLimitError):
        return _error(503, "reddit_rate_limited", str(exc))
    if isinstance(exc, SubredditNotFoundError):
        return _error(404, "subreddit_not_found", str(exc))
    return _error(502, "reddit_error", str(exc))


@app.exception_handler(ClaudeError)
async def _claude_error(_: Request, exc: ClaudeError) -> JSONResponse:
    kind = "claude_auth_error" if isinstance(exc, ClaudeAuthError) else "claude_error"
    return _error(502, kind, str(exc))


# ------------------------------------------------------------------- routes


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/reddit/test")
def reddit_test(reddit: RedditClient = Depends(get_reddit_client)) -> dict:
    return reddit.test_connection()


@app.get("/reddit/posts")
def reddit_posts(
    subreddit: list[str] | None = Query(None, description="Override SUBREDDITS (repeatable)"),
    limit: int | None = Query(None, ge=1, le=100),
    settings: Settings = Depends(get_settings),
    reddit: RedditClient = Depends(get_reddit_client),
    legal_filter: LegalFilter = Depends(get_legal_filter),
) -> dict:
    """Fetch recent posts and show the keyword-filter decision for each (no Claude calls)."""
    collection = collect(subreddit or settings.subreddits, limit or settings.reddit_post_limit, reddit)
    posts = [
        {"reddit_post": post.model_dump(mode="json"), "filter": legal_filter.evaluate(post).model_dump(mode="json")}
        for post in collection.posts
    ]
    return {
        "total_reddit_posts": len(posts),
        "legal_candidates": sum(p["filter"]["legal_related"] for p in posts),
        "per_subreddit": collection.per_subreddit,
        "subreddit_errors": collection.errors,
        "posts": posts,
    }


class ProcessRequest(BaseModel):
    subreddits: list[str] | None = None
    limit: int | None = Field(None, ge=1, le=100)
    max_claude_calls: int | None = Field(None, ge=0)
    dry_run: bool = False


@app.post("/process", response_model=ProcessSummary)
def process(
    request: ProcessRequest | None = None,
    settings: Settings = Depends(get_settings),
    reddit: RedditClient = Depends(get_reddit_client),
    legal_filter: LegalFilter = Depends(get_legal_filter),
) -> ProcessSummary:
    """Run the full pipeline synchronously and return a summary (may take a few minutes)."""
    request = request or ProcessRequest()
    pipeline = LegalContentPipeline(
        settings=settings,
        reddit_client=reddit,
        claude_client=None if request.dry_run else get_claude_client(),
        legal_filter=legal_filter,
        store=JsonResultStore(settings.results_dir),
    )
    return pipeline.process_posts(
        subreddits=request.subreddits,
        limit=request.limit,
        max_claude_calls=request.max_claude_calls,
        dry_run=request.dry_run,
    )

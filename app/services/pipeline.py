"""Processing pipeline: Reddit -> Filter -> Claude -> Result.

All collaborators are injected so each stage can be replaced independently
(e.g. a queue between filter and Claude, PostgreSQL instead of JSON files).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from app.claude.client import ClaudeAuthError, ClaudeError
from app.config import Settings
from app.filtering.legal_filter import LegalFilter
from app.models.schemas import (
    ClaudeAnalysisResponse,
    FilterResult,
    FilterSummary,
    ProcessingResult,
    ProcessingStatus,
    ProcessSummary,
    RedditPost,
)
from app.reddit.collector import PostSource, collect
from app.services.review_policy import apply_review_policy
from app.services.storage import JsonResultStore

logger = logging.getLogger(__name__)


class Analyzer(Protocol):
    model: str

    def analyze_reddit_post(self, post: RedditPost) -> ClaudeAnalysisResponse: ...


class PipelineReporter:
    """Progress hooks. The default does nothing; the CLI prints to the terminal."""

    def connecting(self) -> None: ...
    def subreddit_collected(self, subreddit: str, count: int) -> None: ...
    def subreddit_failed(self, subreddit: str, error: str) -> None: ...
    def candidates_identified(self, total: int, candidates: int) -> None: ...
    def candidate_skipped(self, post: RedditPost, reason: str) -> None: ...
    def sending_to_claude(self, post: RedditPost) -> None: ...
    def result_ready(self, result: ProcessingResult, path: Path | None) -> None: ...
    def finished(self, summary: ProcessSummary) -> None: ...


class LegalContentPipeline:
    def __init__(
        self,
        settings: Settings,
        reddit_client: PostSource,
        claude_client: Analyzer | None,
        legal_filter: LegalFilter,
        store: JsonResultStore,
        reporter: PipelineReporter | None = None,
    ) -> None:
        self.settings = settings
        self.reddit_client = reddit_client
        self.claude_client = claude_client
        self.legal_filter = legal_filter
        self.store = store
        self.reporter = reporter or PipelineReporter()

    def process_posts(
        self,
        subreddits: list[str] | None = None,
        limit: int | None = None,
        max_claude_calls: int | None = None,
        dry_run: bool = False,
    ) -> ProcessSummary:
        subreddits = subreddits or self.settings.subreddits
        limit = limit or self.settings.reddit_post_limit
        max_calls = self.settings.max_claude_calls_per_run if max_claude_calls is None else max_claude_calls
        dry_run = dry_run or self.claude_client is None
        summary = ProcessSummary(dry_run=dry_run)

        # 1. Retrieve
        self.reporter.connecting()
        collection = collect(subreddits, limit, self.reddit_client)
        for name, count in collection.per_subreddit.items():
            self.reporter.subreddit_collected(name, count)
        for name, error in collection.errors.items():
            self.reporter.subreddit_failed(name, error)
        summary.subreddit_errors = collection.errors
        summary.total_reddit_posts = len(collection.posts)

        # 2. Filter
        candidates: list[tuple[RedditPost, FilterResult]] = []
        for post in collection.posts:
            result = self.legal_filter.evaluate(post)
            if result.legal_related:
                candidates.append((post, result))
        summary.legal_candidates = len(candidates)
        summary.filtered_out = summary.total_reddit_posts - summary.legal_candidates
        logger.info(
            "Filtering complete",
            extra={"total_posts": summary.total_reddit_posts, "legal_candidates": summary.legal_candidates},
        )
        self.reporter.candidates_identified(summary.total_reddit_posts, summary.legal_candidates)

        # 3-7. Analyse, validate, combine, save, display
        for post, filter_result in candidates:
            if dry_run:
                self.reporter.candidate_skipped(post, f"dry run (keyword score {filter_result.keyword_score})")
                continue
            if self.settings.skip_already_processed and self.store.exists(post.id):
                summary.skipped_already_processed += 1
                self.reporter.candidate_skipped(post, "already processed")
                continue
            if summary.sent_to_claude >= max_calls:
                summary.skipped_over_limit += 1
                self.reporter.candidate_skipped(post, f"MAX_CLAUDE_CALLS_PER_RUN ({max_calls}) reached")
                continue

            summary.sent_to_claude += 1
            result, path, fatal = self.process_post(post, filter_result)
            if result.processing_status == ProcessingStatus.COMPLETED:
                summary.successful_analysis += 1
            else:
                summary.failed_analysis += 1
            if path:
                summary.result_files.append(str(path))
            if fatal:
                summary.aborted_reason = result.error
                logger.error("Aborting Claude processing for this run", extra={"reason": result.error})
                break

        logger.info("Pipeline finished", extra=summary.model_dump(exclude={"result_files", "subreddit_errors"}))
        self.reporter.finished(summary)
        return summary

    def process_post(
        self, post: RedditPost, filter_result: FilterResult
    ) -> tuple[ProcessingResult, Path | None, bool]:
        """Analyse and save one post. Returns (result, saved_path, fatal_error)."""
        assert self.claude_client is not None
        self.reporter.sending_to_claude(post)
        filter_summary = FilterSummary(**filter_result.model_dump(include={"legal_related", "keyword_score", "matched_keywords"}))
        fatal = False

        try:
            raw = self.claude_client.analyze_reddit_post(post)
            analysis = apply_review_policy(
                raw, self.settings.confidence_review_threshold, model=getattr(self.claude_client, "model", None)
            )
            result = ProcessingResult(
                reddit_post=post, filter=filter_summary, ai_analysis=analysis,
                processing_status=ProcessingStatus.COMPLETED,
            )
        except ClaudeError as exc:
            fatal = isinstance(exc, ClaudeAuthError)  # every later call would fail too
            logger.error("Claude analysis failed", extra={"post_id": post.id, "error_type": type(exc).__name__, "error": str(exc)})
            result = self._failed(post, filter_summary, str(exc))
        except Exception as exc:  # keep processing other posts
            logger.exception("Unexpected error processing post", extra={"post_id": post.id})
            result = self._failed(post, filter_summary, f"Unexpected error: {type(exc).__name__}")

        path: Path | None = None
        try:
            path = self.store.save(result)
        except OSError as exc:
            logger.error("Failed to save result", extra={"post_id": post.id, "error": type(exc).__name__})

        self.reporter.result_ready(result, path)
        return result, path, fatal

    @staticmethod
    def _failed(post: RedditPost, filter_summary: FilterSummary, error: str) -> ProcessingResult:
        return ProcessingResult(
            reddit_post=post, filter=filter_summary, processing_status=ProcessingStatus.FAILED, error=error
        )


def build_pipeline(
    settings: Settings, reporter: PipelineReporter | None = None, dry_run: bool = False
) -> LegalContentPipeline:
    """Wire up the real Reddit and Claude clients from configuration."""
    from app.claude.client import ClaudeClient
    from app.reddit.client import RedditClient

    return LegalContentPipeline(
        settings=settings,
        reddit_client=RedditClient(settings),
        claude_client=None if dry_run else ClaudeClient(settings),
        legal_filter=LegalFilter.from_settings(settings),
        store=JsonResultStore(settings.results_dir),
        reporter=reporter,
    )

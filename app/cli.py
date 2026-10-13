"""Command-line entry point (used by run.py)."""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

from app.claude.client import ClaudeError
from app.config import ConfigurationError, PROJECT_ROOT, load_settings
from app.logging_config import configure_logging
from app.models.schemas import AI_DISCLAIMER, ProcessingResult, ProcessingStatus, ProcessSummary, RedditPost
from app.reddit.client import RedditClientError
from app.services.pipeline import PipelineReporter, build_pipeline

RULE = "-" * 72


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


class ConsoleReporter(PipelineReporter):
    def connecting(self) -> None:
        print("Connecting to Reddit...")

    def subreddit_collected(self, subreddit: str, count: int) -> None:
        print(f"Subreddit: {subreddit}")
        print(f"Posts retrieved: {count}")

    def subreddit_failed(self, subreddit: str, error: str) -> None:
        print(f"Subreddit: {subreddit}")
        print(f"  ERROR: {error}")

    def candidates_identified(self, total: int, candidates: int) -> None:
        print(f"Total posts: {total}")
        print(f"Legal candidates: {candidates}")

    def candidate_skipped(self, post: RedditPost, reason: str) -> None:
        print(f"Skipped [{reason}]: {post.title}")

    def sending_to_claude(self, post: RedditPost) -> None:
        print(RULE)
        print("Sending candidate to Claude...")
        print(f"Post: {post.title}")
        print(f"Link: {post.permalink}")

    def result_ready(self, result: ProcessingResult, path: Path | None) -> None:
        analysis = result.ai_analysis
        if result.processing_status == ProcessingStatus.FAILED or analysis is None:
            print(f"Analysis FAILED: {result.error}")
        else:
            print(f"*** {AI_DISCLAIMER} ***")
            print(f"AI Classification: {'LEGAL' if analysis.relevant else 'NOT LEGAL'}")
            print(f"Category: {analysis.legal_category} / {analysis.sub_category}")
            print(f"Confidence: {analysis.confidence:.2f}")
            print(f"Jurisdiction: {analysis.jurisdiction}")
            print(f"Risk Level: {analysis.risk_level}")
            print(f"Question: {analysis.question}")
            print("Summary:")
            print(textwrap.indent(textwrap.fill(analysis.summary, 68), "  "))
            if analysis.key_issues:
                print("Key issues: " + "; ".join(analysis.key_issues))
            print("Preliminary analysis (for human review):")
            print(textwrap.indent(textwrap.fill(analysis.preliminary_answer, 68), "  "))
            print(f"Human Review Required: {'YES' if analysis.requires_manual_review else 'NO'}")
            if analysis.manual_review_reasons:
                print("Review reasons: " + "; ".join(analysis.manual_review_reasons))
        if path:
            print("Result saved:")
            print(_relative(path))

    def finished(self, summary: ProcessSummary) -> None:
        print(RULE)
        print("Run summary")
        for key, value in summary.model_dump(exclude={"result_files", "subreddit_errors"}).items():
            if value not in (None, False):
                print(f"  {key}: {value}")
        for name, error in summary.subreddit_errors.items():
            print(f"  error r/{name}: {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reddit legal-content AI prototype")
    parser.add_argument("-s", "--subreddit", action="append", help="Subreddit to read (repeatable; overrides SUBREDDITS)")
    parser.add_argument("-l", "--limit", type=int, help="Posts per subreddit (overrides REDDIT_POST_LIMIT)")
    parser.add_argument("-m", "--max-claude-calls", type=int, help="Cap on Claude calls this run")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and filter only - do not call Claude")
    parser.add_argument("--test-reddit", action="store_true", help="Only test the Reddit connection")
    args = parser.parse_args(argv)

    # Reddit titles often contain emoji; don't crash a legacy Windows console.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    try:
        settings = load_settings()
    except ConfigurationError as exc:
        configure_logging()
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level, settings.log_format, settings.secret_values())

    try:
        if args.test_reddit:
            from app.reddit.client import RedditClient

            print("Connecting to Reddit...")
            print(RedditClient(settings).test_connection())
            return 0

        pipeline = build_pipeline(settings, reporter=ConsoleReporter(), dry_run=args.dry_run)
        summary = pipeline.process_posts(
            subreddits=args.subreddit,
            limit=args.limit,
            max_claude_calls=args.max_claude_calls,
            dry_run=args.dry_run,
        )
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except (RedditClientError, ClaudeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130

    return 1 if summary.aborted_reason else 0

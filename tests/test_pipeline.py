"""Pipeline / collector / API behaviour with fake Reddit and Claude clients."""

import json

import pytest
from fastapi.testclient import TestClient

from app.claude.client import ClaudeAuthError, ClaudeMalformedResponseError
from app.filtering.legal_filter import LegalFilter
from app.models.schemas import ClaudeAnalysisResponse, ProcessingResult
from app.reddit.client import RedditAuthError, SubredditNotFoundError
from app.reddit.collector import collect, collect_posts
from app.services.pipeline import LegalContentPipeline
from app.services.storage import JsonResultStore


class FakeReddit:
    def __init__(self, raw_posts, errors=None):
        self.raw_posts = raw_posts
        self.errors = errors or {}
        self.calls = []

    def get_posts(self, subreddit_name, limit=20):
        self.calls.append(subreddit_name)
        if subreddit_name in self.errors:
            raise self.errors[subreddit_name]
        return [p for p in self.raw_posts if p["subreddit"] == subreddit_name][:limit]


class FakeClaude:
    model = "fake-model"

    def __init__(self, analysis: dict, fail_ids=None, error=None):
        self.analysis = analysis
        self.fail_ids = set(fail_ids or [])
        self.error = error or ClaudeMalformedResponseError("Claude returned invalid JSON (test).")
        self.seen = []

    def analyze_reddit_post(self, post):
        self.seen.append(post.id)
        if post.id in self.fail_ids or "*" in self.fail_ids:
            raise self.error
        return ClaudeAnalysisResponse.model_validate(self.analysis)


def make_pipeline(settings, reddit, claude):
    return LegalContentPipeline(
        settings=settings, reddit_client=reddit, claude_client=claude,
        legal_filter=LegalFilter(), store=JsonResultStore(settings.results_dir),
    )


# ----------------------------------------------------------------- collector


def test_collector_dedupes_and_validates(sample_raw_posts):
    duplicate = dict(sample_raw_posts[0])                       # same id
    crosspost = {**sample_raw_posts[0], "id": "t1xpost", "subreddit": "law"}  # same title+body
    invalid = {"id": "bad id", "subreddit": "law", "title": "x"}
    reddit = FakeReddit([*sample_raw_posts, duplicate, crosspost, invalid])

    result = collect(["legaladvice", "LegalAdviceUK", "law"], 50, reddit)

    assert len(result.posts) == len(sample_raw_posts)
    assert result.duplicates_removed == 2
    assert result.invalid_posts == 1
    assert len({p.id for p in result.posts}) == len(result.posts)


def test_collector_continues_after_subreddit_error(sample_raw_posts):
    reddit = FakeReddit(sample_raw_posts, errors={"legaladvice": SubredditNotFoundError("r/legaladvice gone")})
    result = collect(["legaladvice", "LegalAdviceUK"], 50, reddit)
    assert "legaladvice" in result.errors
    assert result.per_subreddit == {"LegalAdviceUK": 2}
    assert len(collect_posts(["LegalAdviceUK"], 50, reddit)) == 2


def test_collector_reraises_auth_error(sample_raw_posts):
    reddit = FakeReddit(sample_raw_posts, errors={"legaladvice": RedditAuthError("bad creds")})
    with pytest.raises(RedditAuthError):
        collect(["legaladvice"], 50, reddit)


# ------------------------------------------------------------------ pipeline


def test_pipeline_end_to_end(settings, sample_raw_posts, valid_analysis):
    claude = FakeClaude(valid_analysis)
    summary = make_pipeline(settings, FakeReddit(sample_raw_posts), claude).process_posts()

    expected_candidates = sum(p["expected_decision"] == "CANDIDATE_FOR_AI" for p in sample_raw_posts)
    assert summary.total_reddit_posts == len(sample_raw_posts)
    assert summary.legal_candidates == expected_candidates
    assert summary.filtered_out == len(sample_raw_posts) - expected_candidates
    assert summary.sent_to_claude == expected_candidates
    assert summary.successful_analysis == expected_candidates
    assert summary.failed_analysis == 0
    assert "t1non04" not in claude.seen  # non-legal post never reaches Claude

    files = sorted(settings.results_dir.glob("*_post_*.json"))
    assert len(files) == expected_candidates
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert set(saved) >= {"reddit_post", "filter", "ai_analysis", "processed_at", "processing_status", "disclaimer"}
    assert saved["processing_status"] == "COMPLETED"
    assert saved["filter"]["legal_related"] is True
    assert saved["ai_analysis"]["requires_manual_review"] is True  # unknown jurisdiction
    assert saved["ai_analysis"]["disclaimer"] == "AI-generated preliminary analysis – human review required."


def test_pipeline_continues_when_one_post_fails(settings, sample_raw_posts, valid_analysis):
    claude = FakeClaude(valid_analysis, fail_ids={"t1crm03"})
    summary = make_pipeline(settings, FakeReddit(sample_raw_posts), claude).process_posts()

    assert summary.failed_analysis == 1
    assert summary.successful_analysis == summary.sent_to_claude - 1
    failed = ProcessingResult.model_validate_json(
        next(settings.results_dir.glob("*_post_t1crm03.json")).read_text(encoding="utf-8")
    )
    assert failed.processing_status == "FAILED"
    assert failed.ai_analysis is None
    assert "invalid JSON" in failed.error


def test_pipeline_aborts_on_claude_auth_error(settings, sample_raw_posts, valid_analysis):
    claude = FakeClaude(valid_analysis, fail_ids={"*"}, error=ClaudeAuthError("Claude authentication failed."))
    summary = make_pipeline(settings, FakeReddit(sample_raw_posts), claude).process_posts()
    assert summary.sent_to_claude == 1
    assert summary.failed_analysis == 1
    assert summary.aborted_reason == "Claude authentication failed."


def test_pipeline_respects_max_claude_calls(settings, sample_raw_posts, valid_analysis):
    summary = make_pipeline(settings, FakeReddit(sample_raw_posts), FakeClaude(valid_analysis)).process_posts(
        max_claude_calls=2
    )
    assert summary.sent_to_claude == 2
    assert summary.skipped_over_limit == summary.legal_candidates - 2


def test_pipeline_skips_already_processed(settings, sample_raw_posts, valid_analysis):
    reddit = FakeReddit(sample_raw_posts)
    make_pipeline(settings, reddit, FakeClaude(valid_analysis)).process_posts()
    claude = FakeClaude(valid_analysis)
    summary = make_pipeline(settings, reddit, claude).process_posts()
    assert summary.sent_to_claude == 0
    assert summary.skipped_already_processed == summary.legal_candidates
    assert claude.seen == []


def test_pipeline_dry_run_never_calls_claude(settings, sample_raw_posts):
    summary = make_pipeline(settings, FakeReddit(sample_raw_posts), None).process_posts()
    assert summary.dry_run is True
    assert summary.sent_to_claude == 0
    assert summary.legal_candidates > 0
    assert not settings.results_dir.exists()


# ---------------------------------------------------------------------- API


def test_api_endpoints(settings, sample_raw_posts, valid_analysis, monkeypatch):
    from app import main

    reddit = FakeReddit(sample_raw_posts)
    reddit.test_connection = lambda: {"connected": True, "auth_mode": "application_only_read_only"}
    main.app.dependency_overrides[main.get_settings] = lambda: settings
    main.app.dependency_overrides[main.get_reddit_client] = lambda: reddit
    monkeypatch.setattr(main, "get_claude_client", lambda: FakeClaude(valid_analysis))
    try:
        api = TestClient(main.app)
        assert api.get("/health").json() == {"status": "ok"}
        assert api.get("/reddit/test").json()["connected"] is True

        posts = api.get("/reddit/posts").json()
        assert posts["total_reddit_posts"] == len(sample_raw_posts)
        assert posts["posts"][0]["filter"]["decision"] in {"CANDIDATE_FOR_AI", "FILTERED_OUT"}

        summary = api.post("/process").json()
        assert summary["sent_to_claude"] == summary["legal_candidates"] == summary["successful_analysis"]
        assert summary["failed_analysis"] == 0
    finally:
        main.app.dependency_overrides.clear()

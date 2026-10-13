import json
from pathlib import Path

import pytest

from app.config import Settings
from app.models.schemas import RedditPost

SAMPLE_FILE = Path(__file__).parent / "sample_reddit_posts.json"


@pytest.fixture
def sample_raw_posts() -> list[dict]:
    return json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))


@pytest.fixture
def sample_posts(sample_raw_posts) -> dict[str, RedditPost]:
    return {raw["id"]: RedditPost.model_validate(raw) for raw in sample_raw_posts}


@pytest.fixture
def settings(tmp_path) -> Settings:
    # Built directly - never reads the developer's real .env.
    return Settings(
        reddit_client_id="test-id",
        reddit_client_secret="test-secret-value",
        reddit_user_agent="test-agent",
        anthropic_api_key="sk-ant-test-key-value",
        subreddits=["legaladvice", "LegalAdviceUK", "law"],
        results_dir=tmp_path / "results",
        max_claude_calls_per_run=50,
    )


@pytest.fixture
def valid_analysis() -> dict:
    return {
        "relevant": True,
        "confidence": 0.94,
        "legal_category": "Landlord and Tenant",
        "sub_category": "Security Deposit",
        "question": "Can a landlord legally withhold my security deposit?",
        "summary": "The user is asking whether their landlord may keep the full deposit.",
        "key_issues": ["Security deposit", "Landlord obligations", "Possible deductions"],
        "jurisdiction": "Unknown",
        "jurisdiction_known": False,
        "preliminary_answer": "The answer may depend on local deposit rules...",
        "uncertainty_notes": "Jurisdiction not stated.",
        "risk_level": "Medium",
        "requires_manual_review": True,
        "review_flags": ["unclear_facts"],
        "reason": "The post asks a specific legal question.",
    }

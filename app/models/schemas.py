"""Pydantic models shared across the application.

These models are the contract between modules (Reddit -> filter -> Claude ->
storage). Keeping them in one place means a future PostgreSQL repository or
review UI can reuse exactly the same shapes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

AI_DISCLAIMER = "AI-generated preliminary analysis – human review required."

UNKNOWN_JURISDICTION = "Unknown"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------


class RedditPost(BaseModel):
    """The minimal subset of a Reddit submission the platform needs."""

    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_]+$")
    subreddit: str = Field(min_length=1)
    title: str = Field(min_length=1)
    body: str = ""
    author: str | None = None
    created_at: datetime
    score: int = 0
    num_comments: int = Field(0, ge=0)
    permalink: str
    url: str
    flair: str | None = None
    nsfw: bool = False

    @field_validator("body", mode="before")
    @classmethod
    def _none_body_to_empty(cls, value: object) -> object:
        return "" if value is None else value

    @property
    def text(self) -> str:
        """Title and body combined, as used by the keyword filter."""
        return f"{self.title}\n\n{self.body}".strip()


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class FilterDecision(str, Enum):
    FILTERED_OUT = "FILTERED_OUT"
    CANDIDATE_FOR_AI = "CANDIDATE_FOR_AI"


class FilterResult(BaseModel):
    legal_related: bool
    keyword_score: int
    matched_keywords: list[str] = Field(default_factory=list)
    decision: FilterDecision


# ---------------------------------------------------------------------------
# Claude analysis
# ---------------------------------------------------------------------------

RiskLevel = Literal["Low", "Medium", "High"]

REVIEW_FLAGS = (
    "criminal_matter",
    "immigration_matter",
    "serious_injury",
    "high_financial_impact",
    "active_litigation",
    "threats_or_safety_risk",
    "unclear_facts",
    "sensitive_personal_information",
)


class ClaudeAnalysisResponse(BaseModel):
    """Exactly what Claude is asked to return (see app/claude/prompts.py)."""

    relevant: bool
    confidence: float = Field(ge=0.0, le=1.0)
    legal_category: str
    sub_category: str
    question: str
    summary: str
    key_issues: list[str] = Field(default_factory=list)
    jurisdiction: str = UNKNOWN_JURISDICTION
    jurisdiction_known: bool = False
    preliminary_answer: str
    uncertainty_notes: str = ""
    risk_level: RiskLevel
    requires_manual_review: bool
    review_flags: list[str] = Field(default_factory=list)
    reason: str

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalise_confidence(cls, value: object) -> object:
        # Tolerate a percentage (e.g. 94) instead of a fraction (0.94).
        if isinstance(value, (int, float)) and 1 < value <= 100:
            return value / 100
        return value

    @field_validator("risk_level", mode="before")
    @classmethod
    def _normalise_risk(cls, value: object) -> object:
        return value.strip().capitalize() if isinstance(value, str) else value

    @field_validator("jurisdiction", mode="before")
    @classmethod
    def _normalise_jurisdiction(cls, value: object) -> object:
        if value is None:
            return UNKNOWN_JURISDICTION
        if isinstance(value, str) and value.strip().lower() in {
            "", "unknown", "none", "n/a", "na", "not specified", "unspecified", "not stated",
        }:
            return UNKNOWN_JURISDICTION
        return value

    @field_validator("review_flags", mode="before")
    @classmethod
    def _keep_known_flags(cls, value: object) -> object:
        if isinstance(value, list):
            return [flag for flag in value if flag in REVIEW_FLAGS]
        return value


class AIAnalysis(ClaudeAnalysisResponse):
    """Claude's response after the local human-review policy is applied."""

    manual_review_reasons: list[str] = Field(default_factory=list)
    model: str | None = None
    disclaimer: str = AI_DISCLAIMER


# ---------------------------------------------------------------------------
# Pipeline results
# ---------------------------------------------------------------------------


class ProcessingStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FilterSummary(BaseModel):
    legal_related: bool
    keyword_score: int
    matched_keywords: list[str] = Field(default_factory=list)


class ProcessingResult(BaseModel):
    """The record persisted to data/results/*.json."""

    reddit_post: RedditPost
    filter: FilterSummary
    ai_analysis: AIAnalysis | None = None
    disclaimer: str = AI_DISCLAIMER
    processed_at: datetime = Field(default_factory=utc_now)
    processing_status: ProcessingStatus
    error: str | None = None


class ProcessSummary(BaseModel):
    total_reddit_posts: int = 0
    legal_candidates: int = 0
    filtered_out: int = 0
    sent_to_claude: int = 0
    successful_analysis: int = 0
    failed_analysis: int = 0
    skipped_already_processed: int = 0
    skipped_over_limit: int = 0
    dry_run: bool = False
    aborted_reason: str | None = None
    subreddit_errors: dict[str, str] = Field(default_factory=dict)
    result_files: list[str] = Field(default_factory=list)

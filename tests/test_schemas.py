from datetime import timezone

import pytest
from pydantic import ValidationError

from app.models.schemas import ClaudeAnalysisResponse, RedditPost
from app.services.review_policy import apply_review_policy


# --------------------------------------------------------------- RedditPost


def test_reddit_post_valid(sample_raw_posts):
    post = RedditPost.model_validate(sample_raw_posts[0])
    assert post.id == "t1dep01"
    assert post.created_at.tzinfo is not None
    assert "security deposit" in post.text.lower()


def test_reddit_post_accepts_unix_timestamp_and_null_body():
    post = RedditPost(
        id="x1", subreddit="law", title="t", body=None, created_at=1_790_000_000,
        permalink="https://www.reddit.com/x", url="https://www.reddit.com/x",
    )
    assert post.body == ""
    assert post.created_at.tzinfo == timezone.utc


@pytest.mark.parametrize("field, value", [("id", ""), ("id", "bad id!"), ("title", ""), ("num_comments", -1)])
def test_reddit_post_rejects_invalid(sample_raw_posts, field, value):
    raw = {**sample_raw_posts[0], field: value}
    with pytest.raises(ValidationError):
        RedditPost.model_validate(raw)


def test_reddit_post_requires_core_fields():
    with pytest.raises(ValidationError):
        RedditPost.model_validate({"id": "x1", "title": "hello"})


# ------------------------------------------------- Claude response validation


def test_claude_response_valid(valid_analysis):
    analysis = ClaudeAnalysisResponse.model_validate(valid_analysis)
    assert analysis.relevant is True
    assert analysis.confidence == 0.94
    assert analysis.risk_level == "Medium"


def test_claude_response_normalises_values(valid_analysis):
    data = {**valid_analysis, "confidence": 87, "risk_level": "high", "jurisdiction": "not specified",
            "review_flags": ["unclear_facts", "made_up_flag"]}
    analysis = ClaudeAnalysisResponse.model_validate(data)
    assert analysis.confidence == pytest.approx(0.87)
    assert analysis.risk_level == "High"
    assert analysis.jurisdiction == "Unknown"
    assert analysis.review_flags == ["unclear_facts"]


@pytest.mark.parametrize(
    "mutation",
    [
        {"confidence": -0.1},
        {"confidence": 150},
        {"risk_level": "Extreme"},
        {"relevant": "maybe"},
        {"key_issues": "not a list"},
    ],
)
def test_claude_response_rejects_invalid(valid_analysis, mutation):
    with pytest.raises(ValidationError):
        ClaudeAnalysisResponse.model_validate({**valid_analysis, **mutation})


def test_claude_response_missing_required_field(valid_analysis):
    data = dict(valid_analysis)
    del data["preliminary_answer"]
    with pytest.raises(ValidationError):
        ClaudeAnalysisResponse.model_validate(data)


# ---------------------------------------------------------- review policy


def _analysis(valid_analysis, **overrides) -> ClaudeAnalysisResponse:
    return ClaudeAnalysisResponse.model_validate({**valid_analysis, **overrides})


def test_policy_forces_review_for_unknown_jurisdiction(valid_analysis):
    result = apply_review_policy(
        _analysis(valid_analysis, requires_manual_review=False, review_flags=[]), confidence_threshold=0.75
    )
    assert result.requires_manual_review is True
    assert "Unknown jurisdiction" in result.manual_review_reasons
    assert result.disclaimer.startswith("AI-generated preliminary analysis")


def test_policy_detects_criminal_and_immigration_from_category(valid_analysis):
    base = dict(requires_manual_review=False, review_flags=[], jurisdiction="England", jurisdiction_known=True)
    crim = apply_review_policy(_analysis(valid_analysis, legal_category="Criminal Law", **base), 0.75)
    imm = apply_review_policy(_analysis(valid_analysis, legal_category="Immigration", **base), 0.75)
    assert "Criminal matter" in crim.manual_review_reasons and crim.requires_manual_review
    assert "Immigration matter" in imm.manual_review_reasons and imm.requires_manual_review


def test_policy_flags_low_confidence_and_high_risk(valid_analysis):
    result = apply_review_policy(
        _analysis(valid_analysis, confidence=0.5, risk_level="High", requires_manual_review=False,
                  review_flags=[], jurisdiction="Texas", jurisdiction_known=True),
        confidence_threshold=0.75,
    )
    assert any("Low AI confidence" in r for r in result.manual_review_reasons)
    assert "High risk level" in result.manual_review_reasons


@pytest.mark.parametrize(
    "flag, label",
    [
        ("serious_injury", "Serious injury"),
        ("high_financial_impact", "High financial impact"),
        ("active_litigation", "Active litigation"),
        ("threats_or_safety_risk", "Threats or safety risk"),
        ("unclear_facts", "Unclear facts"),
        ("sensitive_personal_information", "Potentially sensitive personal information"),
    ],
)
def test_policy_maps_ai_flags_to_review(valid_analysis, flag, label):
    result = apply_review_policy(
        _analysis(valid_analysis, requires_manual_review=False, review_flags=[flag],
                  jurisdiction="Ontario", jurisdiction_known=True),
        0.75,
    )
    assert result.requires_manual_review is True
    assert label in result.manual_review_reasons


def test_policy_allows_no_review_only_when_nothing_triggers(valid_analysis):
    result = apply_review_policy(
        _analysis(valid_analysis, relevant=False, legal_category="Not Legal", sub_category="Not Legal",
                  confidence=0.97, risk_level="Low", requires_manual_review=False, review_flags=[]),
        0.75,
    )
    assert result.requires_manual_review is False
    assert result.manual_review_reasons == []

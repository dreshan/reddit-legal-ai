import pytest

from app.filtering.legal_filter import (
    DEFAULT_LEGAL_KEYWORDS,
    LegalFilter,
    calculate_legal_keyword_score,
    is_legal_related,
)
from app.models.schemas import FilterDecision, RedditPost


def make_post(title: str, body: str = "") -> RedditPost:
    return RedditPost(
        id="abc123", subreddit="test", title=title, body=body,
        created_at="2026-10-07T00:00:00Z", permalink="https://www.reddit.com/x", url="https://www.reddit.com/x",
    )


def test_sample_posts_match_expected_decisions(sample_raw_posts, sample_posts):
    f = LegalFilter()
    for raw in sample_raw_posts:
        assert f.evaluate(sample_posts[raw["id"]]).decision.value == raw["expected_decision"], raw["id"]


@pytest.mark.parametrize(
    "title, body",
    [
        ("My landlord won't return my deposit", ""),
        ("Question", "I was in a car accident and the other driver's insurance won't call back"),
        ("Need a solicitor?", ""),
        ("Ex is ignoring the custody order", "What can the court do?"),
        ("Help", "Is my non-compete AGREEMENT enforceable?"),
        ("Data protection question", "My employer shared my medical info"),
    ],
)
def test_legal_keywords_detected(title, body):
    assert is_legal_related(make_post(title, body))


@pytest.mark.parametrize(
    "title, body",
    [
        ("Best pizza in town?", "Looking for recommendations"),
        ("My lawn is dying", "Brown patches everywhere"),          # 'lawn' is not 'law'
        ("Weird issue with my laptop", "It keeps rebooting"),        # 'issue' is not 'sue'
        ("Lawful good characters in D&D", ""),                       # 'lawful' is not 'law'
    ],
)
def test_non_legal_posts_filtered_out(title, body):
    post = make_post(title, body)
    assert not is_legal_related(post)
    assert LegalFilter().evaluate(post).decision == FilterDecision.FILTERED_OUT


def test_keyword_score_counts_distinct_keywords_in_title_and_body():
    post = make_post("Tenant vs landlord", "Landlord started an eviction; tenant wants a lawyer. Landlord again.")
    f = LegalFilter()
    assert f.calculate_legal_keyword_score(post) == 4
    assert set(f.matched_keywords(post)) == {"tenant", "landlord", "eviction", "lawyer"}
    assert calculate_legal_keyword_score(post) == 4


def test_score_matches_plurals_and_multiword_phrases_case_insensitive():
    post = make_post("LAWYERS and Child   Support", "charges pending")
    assert set(LegalFilter().matched_keywords(post)) >= {"lawyer", "child support", "charge"}


def test_keyword_score_zero_for_non_legal():
    assert calculate_legal_keyword_score(make_post("Cats are great", "So fluffy")) == 0


def test_custom_keywords_and_min_score():
    post = make_post("Parking dispute with neighbour", "")
    assert not is_legal_related(post)
    assert is_legal_related(post, keywords=["parking", "neighbour"])
    assert not is_legal_related(post, keywords=["parking", "neighbour"], min_score=3)


def test_from_settings_supports_extra_keywords(settings):
    settings.extra_legal_keywords = ["driveway"]
    f = LegalFilter.from_settings(settings)
    assert "driveway" in f.keywords
    assert len(f.keywords) == len(DEFAULT_LEGAL_KEYWORDS) + 1


def test_evaluate_returns_full_filter_result():
    result = LegalFilter().evaluate(make_post("I want to sue my contractor", ""))
    assert result.legal_related is True
    assert result.keyword_score == 1
    assert result.matched_keywords == ["sue"]
    assert result.decision == FilterDecision.CANDIDATE_FOR_AI


def test_empty_keyword_list_rejected():
    with pytest.raises(ValueError):
        LegalFilter(keywords=["  "])

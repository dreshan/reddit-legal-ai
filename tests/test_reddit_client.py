"""RedditClient tests with a mocked PRAW instance - no network calls."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from prawcore import exceptions as pce

from app.config import ConfigurationError
from app.reddit.client import (
    RedditAuthError,
    RedditClient,
    RedditRateLimitError,
    RedditUnavailableError,
    SubredditNotFoundError,
)


def _submission(**overrides):
    data = dict(
        id="abc1", subreddit="legaladvice", title="Title", selftext="Body", author=SimpleNamespace(name="someone"),
        created_utc=1_790_000_000, score=5, num_comments=2, permalink="/r/legaladvice/comments/abc1/title/",
        url="https://www.reddit.com/r/legaladvice/comments/abc1/title/", link_flair_text="Housing", over_18=False,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _http_response(status):
    return MagicMock(status_code=status, headers={})


def _client_raising(settings, exc):
    reddit = MagicMock()
    reddit.subreddit.return_value.new.side_effect = exc
    reddit.auth.scopes.side_effect = exc
    return RedditClient(settings, reddit=reddit)


def test_get_posts_returns_minimal_dicts(settings):
    reddit = MagicMock()
    reddit.subreddit.return_value.new.return_value = [_submission(), _submission(id="abc2", author=None, selftext="")]
    posts = RedditClient(settings, reddit=reddit).get_posts("legaladvice", limit=2)

    reddit.subreddit.return_value.new.assert_called_once_with(limit=2)
    assert posts[0]["permalink"] == "https://www.reddit.com/r/legaladvice/comments/abc1/title/"
    assert posts[0]["author"] == "someone"
    assert posts[0]["created_at"].year == 2026
    assert posts[1]["author"] is None and posts[1]["body"] == ""
    assert set(posts[0]) == {
        "id", "subreddit", "title", "body", "author", "created_at", "score",
        "num_comments", "permalink", "url", "flair", "nsfw",
    }


@pytest.mark.parametrize(
    "exc, expected",
    [
        (pce.NotFound(_http_response(404)), SubredditNotFoundError),
        (pce.Forbidden(_http_response(403)), SubredditNotFoundError),
        (pce.ResponseException(_http_response(401)), RedditAuthError),
        (pce.TooManyRequests(_http_response(429)), RedditRateLimitError),
        (pce.ServerError(_http_response(503)), RedditUnavailableError),
        (pce.RequestException(ConnectionError("down"), (), {}), RedditUnavailableError),
    ],
)
def test_get_posts_maps_errors(settings, exc, expected):
    with pytest.raises(expected):
        _client_raising(settings, exc).get_posts("legaladvice")


def test_test_connection_maps_oauth_error(settings):
    exc = pce.OAuthException(_http_response(401), "invalid_client", None)
    with pytest.raises(RedditAuthError) as info:
        _client_raising(settings, exc).test_connection()
    assert "test-secret-value" not in str(info.value)


def test_invalid_subreddit_name_rejected_without_api_call(settings):
    reddit = MagicMock()
    with pytest.raises(SubredditNotFoundError):
        RedditClient(settings, reddit=reddit).get_posts("not a valid name!")
    reddit.subreddit.assert_not_called()


def test_missing_credentials_is_configuration_error(settings):
    settings.reddit_client_secret = None
    with pytest.raises(ConfigurationError, match="REDDIT_CLIENT_SECRET"):
        RedditClient(settings)


def test_username_without_password_rejected(settings):
    settings.reddit_username = "someone"
    with pytest.raises(ConfigurationError, match="together"):
        RedditClient(settings)


def test_real_praw_instance_is_read_only_by_default(settings):
    client = RedditClient(settings)  # builds praw.Reddit but makes no request
    assert client.auth_mode == "application_only_read_only"
    assert client._reddit.read_only is True

# tests/test_groq_client.py
"""
Tests for Groq client retry logic.

No real Groq API calls are made. The groq.AsyncGroq client is patched
at the module level using pytest-mock before the module-level client
is initialised.

Tests cover:
  - Success on first attempt → returns content string
  - 429 on first attempt, success on second → retries and returns content
  - 429 on all MAX_RETRIES attempts → raises GroqRateLimitError
  - Non-retryable exception → raises GroqResponseError immediately (no retry)
  - Empty content in response → raises GroqResponseError
  - Retry sleep is called with correct backoff values
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from groq import RateLimitError

from app.utils.groq_client import (
    GroqRateLimitError,
    GroqResponseError,
    call_groq_with_retry,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

MESSAGES = [{"role": "user", "content": "Review this code."}]


def make_response(content: str) -> MagicMock:
    """Build a mock Groq response with the given content."""
    response = MagicMock()
    response.choices[0].message.content = content
    return response


def make_rate_limit_error() -> RateLimitError:
    """
    RateLimitError requires specific constructor args in groq SDK.
    We use MagicMock to avoid needing the real HTTP response object.
    """
    err = MagicMock(spec=RateLimitError)
    err.__class__ = RateLimitError
    return err


# ---------------------------------------------------------------------------
# Patch strategy:
# The groq client is a module-level singleton in groq_client.py.
# We patch _get_client() to return a mock AsyncGroq instance per test.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_client():
    """Reset the module-level _client singleton before each test."""
    import app.utils.groq_client as mod
    original = mod._client
    mod._client = None
    yield
    mod._client = original


@pytest.fixture
def mock_groq_client():
    """Returns a mock AsyncGroq client with a pre-configured create method."""
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock()
    with patch("app.utils.groq_client._get_client", return_value=client):
        yield client


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_groq_success_on_first_attempt_returns_content(mock_groq_client):
    """Happy path: first call succeeds → content string returned."""
    mock_groq_client.chat.completions.create.return_value = make_response('{"issues": []}')

    result = await call_groq_with_retry(MESSAGES)

    assert result == '{"issues": []}'
    assert mock_groq_client.chat.completions.create.call_count == 1


@pytest.mark.asyncio
async def test_groq_calls_with_correct_parameters(mock_groq_client):
    """Verify response_format, model, temperature, timeout are all set correctly."""
    mock_groq_client.chat.completions.create.return_value = make_response('{"issues": []}')

    await call_groq_with_retry(MESSAGES, max_tokens=1500, temperature=0.2)

    call_kwargs = mock_groq_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "llama-3.3-70b-versatile"
    assert call_kwargs["response_format"] == {"type": "json_object"}
    assert call_kwargs["max_tokens"] == 1500
    assert call_kwargs["temperature"] == 0.2
    assert call_kwargs["timeout"] == 60.0


# ---------------------------------------------------------------------------
# Retry on 429
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_groq_retries_on_429_and_succeeds(mock_groq_client):
    """429 on attempt 1, success on attempt 2 → returns content, called twice."""
    rate_limit_err = RateLimitError(
        message="Rate limit exceeded",
        response=MagicMock(),
        body={"error": {"message": "Rate limit exceeded"}},
    )

    mock_groq_client.chat.completions.create.side_effect = [
        rate_limit_err,
        make_response('{"issues": []}'),
    ]

    with patch("app.utils.groq_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await call_groq_with_retry(MESSAGES)

    assert result == '{"issues": []}'
    assert mock_groq_client.chat.completions.create.call_count == 2
    # Should have slept once after the first 429 (BASE_BACKOFF * 2^0 = 5s)
    mock_sleep.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_groq_429_backoff_doubles_each_retry(mock_groq_client):
    """Backoff: 5s → 10s → raise. Verify asyncio.sleep called with correct values."""
    rate_limit_err = RateLimitError(
        message="Rate limit exceeded",
        response=MagicMock(),
        body={"error": {"message": "Rate limit exceeded"}},
    )

    # All 3 attempts return 429
    mock_groq_client.chat.completions.create.side_effect = [
        rate_limit_err,
        rate_limit_err,
        rate_limit_err,
    ]

    with patch("app.utils.groq_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(GroqRateLimitError):
            await call_groq_with_retry(MESSAGES)

    sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
    assert sleep_calls == [5, 10]  # 5*2^0=5, 5*2^1=10; no sleep before final raise


@pytest.mark.asyncio
async def test_groq_raises_rate_limit_error_after_max_retries(mock_groq_client):
    """3 consecutive 429s → raises GroqRateLimitError."""
    rate_limit_err = RateLimitError(
        message="Rate limit exceeded",
        response=MagicMock(),
        body={"error": {"message": "Rate limit exceeded"}},
    )

    mock_groq_client.chat.completions.create.side_effect = [
        rate_limit_err,
        rate_limit_err,
        rate_limit_err,
    ]

    with patch("app.utils.groq_client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(GroqRateLimitError) as exc_info:
            await call_groq_with_retry(MESSAGES)

    assert "max retries" in str(exc_info.value).lower()
    assert mock_groq_client.chat.completions.create.call_count == 3


# ---------------------------------------------------------------------------
# Non-retryable errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_groq_non_retryable_exception_raises_immediately(mock_groq_client):
    """A non-429 exception → raises GroqResponseError on first attempt, no retry."""
    mock_groq_client.chat.completions.create.side_effect = ConnectionError("Network failure")

    with patch("app.utils.groq_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(GroqResponseError) as exc_info:
            await call_groq_with_retry(MESSAGES)

    assert "Groq call failed" in str(exc_info.value)
    assert mock_groq_client.chat.completions.create.call_count == 1
    mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Empty / bad response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_groq_empty_content_raises_response_error(mock_groq_client):
    """Response with empty content string → raises GroqResponseError."""
    mock_groq_client.chat.completions.create.return_value = make_response("")

    with pytest.raises(GroqResponseError) as exc_info:
        await call_groq_with_retry(MESSAGES)

    assert "Empty response" in str(exc_info.value)


@pytest.mark.asyncio
async def test_groq_none_content_raises_response_error(mock_groq_client):
    """Response with None content → raises GroqResponseError."""
    mock_groq_client.chat.completions.create.return_value = make_response(None)

    with pytest.raises(GroqResponseError):
        await call_groq_with_retry(MESSAGES)


# ---------------------------------------------------------------------------
# Exception hierarchy — our exceptions are not accidentally caught
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_groq_rate_limit_error_is_not_wrapped_as_response_error(mock_groq_client):
    """GroqRateLimitError must propagate as-is, not be wrapped in GroqResponseError."""
    rate_limit_err = RateLimitError(
        message="Rate limit exceeded",
        response=MagicMock(),
        body={"error": {"message": "Rate limit exceeded"}},
    )
    mock_groq_client.chat.completions.create.side_effect = [
        rate_limit_err,
        rate_limit_err,
        rate_limit_err,
    ]

    with patch("app.utils.groq_client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(GroqRateLimitError):
            await call_groq_with_retry(MESSAGES)

    # Must NOT raise GroqResponseError instead
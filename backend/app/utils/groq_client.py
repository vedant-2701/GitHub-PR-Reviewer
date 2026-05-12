"""
Groq API client with retry logic for 429 rate limit errors.

Model is locked to llama-3.3-70b-versatile. Do not substitute without
documenting the reason in CLAUDE.md decisions log.

Usage:
    raw_json = await call_groq_with_retry(messages)
    # caller is responsible for json.loads() and Pydantic validation

Rate limit context (free tier):
    30 RPM / 6000 TPM
    One file review ≈ 1500-2000 tokens input + 2000 tokens output
    → ~3 reviews per minute max before hitting TPM
    → review_task.py adds 2s delay between file reviews
"""
from __future__ import annotations

import asyncio
import logging

from groq import AsyncGroq, RateLimitError

from app.config import get_settings

logger = logging.getLogger(__name__)

MODEL = "llama-3.3-70b-versatile"
MAX_RETRIES = 3
BASE_BACKOFF = 5  # seconds; doubles each retry: 5s → 10s → 20s

# Module-level client — one instance, reused across calls.
# Initialised lazily so tests can patch get_settings() before this runs.
_client: AsyncGroq | None = None


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    return _client


async def call_groq_with_retry(
    messages: list[dict],
    max_tokens: int = 2000,
    temperature: float = 0.1,
) -> str:
    """
    Call Groq API with retry logic on 429 RateLimitError.

    Always sets response_format={"type": "json_object"} — the LLM must return JSON.
    The system prompt must instruct the model to respond only with JSON (see prompt
    design rules in groq-agent-skill.md).

    Returns:
        Raw JSON string. Caller is responsible for json.loads() and Pydantic validation.

    Raises:
        GroqRateLimitError: 429 persisted after MAX_RETRIES attempts.
        GroqResponseError: Non-retryable error, or empty response content.
    """
    client = _get_client()

    for attempt in range(MAX_RETRIES):
        try:
            response = await client.chat.completions.create(
                model=MODEL,
                messages=messages,
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=60.0,
            )
            content = response.choices[0].message.content
            if not content:
                raise GroqResponseError("Empty response from Groq")
            return content

        except RateLimitError as e:
            wait = BASE_BACKOFF * (2**attempt)
            logger.warning(
                "Groq 429 on attempt %d/%d. Waiting %ds. Error: %s",
                attempt + 1,
                MAX_RETRIES,
                wait,
                str(e),
            )
            if attempt == MAX_RETRIES - 1:
                raise GroqRateLimitError(
                    "Groq rate limit exceeded after max retries"
                ) from e
            await asyncio.sleep(wait)

        except (GroqRateLimitError, GroqResponseError):
            # Re-raise our own exceptions without wrapping
            raise

        except Exception as e:
            logger.exception("Unexpected Groq error on attempt %d", attempt + 1)
            raise GroqResponseError(f"Groq call failed: {e}") from e

    # Unreachable — loop always raises or returns — but satisfies type checker
    raise GroqResponseError("call_groq_with_retry exhausted without result")


class GroqRateLimitError(Exception):
    """Raised when Groq returns 429 and all retry attempts are exhausted."""


class GroqResponseError(Exception):
    """Raised on non-retryable Groq errors or empty/malformed responses."""

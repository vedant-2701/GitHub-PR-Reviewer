---
name: groq-agent
description: Use when writing any code that calls the Groq API, handles LLM responses, sets up LangChain with Groq, or deals with structured output from the LLM. Enforces correct Groq SDK usage, retry patterns, and JSON mode.
---

# Groq API Agent Skill

## Use this skill when
- Writing or modifying `app/utils/groq_client.py`
- Setting up LangChain with Groq backend
- Writing any function that calls `client.chat.completions.create()`
- Handling LLM response parsing and Pydantic validation
- Dealing with 429 rate limit errors

## Do not use this skill when
- Working on static analysis tools (bandit/radon/ast)
- Working on GitHub integration
- Working on the frontend

---

## Locked Model

```python
MODEL = "llama-3.3-70b-versatile"
```

Never substitute another model without documenting the reason in CLAUDE.md decisions log.

---

## Correct Groq Client Setup

```python
# app/utils/groq_client.py
import asyncio
import logging
from groq import AsyncGroq, RateLimitError
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

client = AsyncGroq(api_key=settings.GROQ_API_KEY)

MODEL = "llama-3.3-70b-versatile"
MAX_RETRIES = 3
BASE_BACKOFF = 5  # seconds

async def call_groq_with_retry(
    messages: list[dict],
    max_tokens: int = 2000,
    temperature: float = 0.1,
) -> str:
    """
    Call Groq API with retry logic on 429.
    Returns raw JSON string — caller is responsible for Pydantic validation.
    Raises GroqRateLimitError after max retries.
    Raises GroqResponseError on non-retryable failures.
    """
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
            wait = BASE_BACKOFF * (2 ** attempt)
            logger.warning(
                "Groq 429 on attempt %d/%d. Waiting %ds. Error: %s",
                attempt + 1, MAX_RETRIES, wait, str(e)
            )
            if attempt == MAX_RETRIES - 1:
                raise GroqRateLimitError("Groq rate limit exceeded after max retries") from e
            await asyncio.sleep(wait)

        except Exception as e:
            logger.exception("Unexpected Groq error on attempt %d", attempt + 1)
            raise GroqResponseError(f"Groq call failed: {e}") from e


class GroqRateLimitError(Exception):
    pass

class GroqResponseError(Exception):
    pass
```

---

## Correct Pydantic Validation After Groq Call

```python
import json
from pydantic import ValidationError
from app.schemas.review import ReviewResult
from app.utils.groq_client import call_groq_with_retry, GroqResponseError

async def parse_groq_response(messages: list[dict]) -> ReviewResult:
    """
    Calls Groq and validates against ReviewResult schema.
    Retries up to 3 times if JSON is invalid or schema validation fails.
    Raises ReviewAgentError if all retries fail.
    """
    last_error = None
    for attempt in range(3):
        try:
            raw = await call_groq_with_retry(messages)
            data = json.loads(raw)
            return ReviewResult(**data)
        except json.JSONDecodeError as e:
            logger.warning("JSON parse failed attempt %d: %s | raw: %s", attempt + 1, e, raw[:200])
            last_error = e
        except ValidationError as e:
            logger.warning("Pydantic validation failed attempt %d: %s", attempt + 1, e)
            logger.debug("Raw response that failed validation: %s", raw)
            last_error = e

    raise ReviewAgentError(f"All Groq parse attempts failed. Last error: {last_error}")
```

---

## LangChain + Groq Setup

```python
from langchain_groq import ChatGroq
from app.config import get_settings

settings = get_settings()

def get_llm():
    return ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=settings.GROQ_API_KEY,
        temperature=0.1,
        max_tokens=2000,
    )
```

**Install**: `pip install langchain-groq`

Do not use `langchain_community.chat_models.ChatOpenAI` with a Groq base URL — use `langchain_groq` directly. It handles Groq-specific quirks.

---

## Prompt Design Rules

1. **System prompt must instruct JSON output explicitly**:
   ```
   You must respond ONLY with a valid JSON object matching this exact schema: ...
   Do not include any explanation, markdown, or text outside the JSON.
   ```

2. **Include the schema in the prompt** — do not rely on the model knowing it from training.

3. **Temperature = 0.1** for structured output tasks. Lower temperature = more consistent JSON.

4. **Max tokens**: 2000 is sufficient for a single file review. Do not increase without testing.

5. **Context budget**: A full file diff + context can be 1500-2000 tokens input. With 6000 TPM limit, you have ~3 file reviews per minute before hitting rate limits.

---

## Rate Limit Handling in Pipeline

```python
# In review_task.py — sequential processing with delay
INTER_FILE_DELAY = 2.0  # seconds between file reviews

async def review_pr(pr_diff: List[FileDiff]) -> List[ReviewResult]:
    results = []
    for i, file_diff in enumerate(pr_diff):
        if i > 0:
            await asyncio.sleep(INTER_FILE_DELAY)
        result = await review_file(file_diff)
        results.append(result)
    return results
```

Do not parallelize Groq calls. The TPM limit makes parallel calls counterproductive and risks cascading 429s.

---

## What NOT To Do

```python
# WRONG — no retry
response = client.chat.completions.create(...)

# WRONG — no response_format
response = client.chat.completions.create(model=MODEL, messages=messages)

# WRONG — trusting LLM output without validation
data = json.loads(response.choices[0].message.content)
# ^ json.loads can succeed but data might not match schema

# WRONG — hardcoded API key
client = AsyncGroq(api_key="gsk_abc123...")

# WRONG — synchronous call inside async function
response = groq.chat.completions.create(...)  # blocking
```
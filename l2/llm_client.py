"""Thin OpenAI wrapper with retries + JSON-mode parsing."""
import json
import os
import time
from typing import Optional

from openai import OpenAI
from openai import APIError, RateLimitError, APITimeoutError

MODEL_DEFAULT = "gpt-4o"
MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0

_client: Optional[OpenAI] = None


def get_openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def call_json(
    system: str,
    user: str,
    model: str = MODEL_DEFAULT,
    temperature: float = 0.1,
    max_tokens: int = 1200,
) -> dict:
    client = get_openai()
    backoff = INITIAL_BACKOFF
    last_err: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            content = resp.choices[0].message.content or "{}"
            return json.loads(content)
        except (RateLimitError, APITimeoutError, APIError) as e:
            last_err = e
            if attempt == MAX_RETRIES - 1:
                break
            time.sleep(backoff)
            backoff *= 2
        except json.JSONDecodeError as e:
            last_err = e
            break
    raise RuntimeError(f"OpenAI call failed after {MAX_RETRIES} attempts: {last_err}")

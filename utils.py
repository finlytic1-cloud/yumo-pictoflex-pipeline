"""
Shared helpers: logging, retries, JSON extraction, and the Anthropic call.
"""
import functools
import json
import logging
import re
import sys
import time

import requests

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("pipeline")


def retry(times: int = 3, base_delay: float = 2.0, exceptions=(Exception,)):
    """Simple exponential-backoff retry decorator for transient failures
    (rate limits, timeouts, flaky network). Does NOT swallow the final
    failure - it re-raises so the caller can decide what to do (e.g. mark
    a single post as failed rather than crashing the whole run)."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, times + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt == times:
                        break
                    delay = base_delay * (2 ** (attempt - 1))
                    log.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                        fn.__name__, attempt, times, exc, delay,
                    )
                    time.sleep(delay)
            raise last_exc

        return wrapper

    return decorator


def extract_json(text: str) -> dict:
    """Claude sometimes wraps JSON output in ```json ... ``` fences.
    Strip those before parsing. Raises ValueError with the raw text
    attached (via args) if parsing still fails, so callers can log it
    instead of silently losing the response."""
    if text is None:
        raise ValueError("empty response")
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fallback: the model sometimes writes reasoning prose before/after the
    # JSON object instead of returning pure JSON (seen in QC scoring calls
    # that "think out loud" first). Grab the outermost {...} span and try
    # that instead of giving up.
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = cleaned[first_brace:last_brace + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError(f"could not parse JSON from model output: {exc}\nraw: {text[:2000]}") from exc

    raise ValueError(f"could not parse JSON from model output: no JSON object found\nraw: {text[:2000]}")


class TransientHTTPError(Exception):
    pass


@retry(times=3, base_delay=3.0, exceptions=(TransientHTTPError, requests.exceptions.RequestException))
def anthropic_call(system: str, user: str, max_tokens: int = 4000, temperature: float = 1.0) -> str:
    """Calls Claude and returns the text of the first content block."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": config.CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=120,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientHTTPError(f"Anthropic {resp.status_code}: {resp.text[:500]}")
    resp.raise_for_status()
    data = resp.json()
    blocks = data.get("content", [])
    text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    return "\n".join(text_parts)

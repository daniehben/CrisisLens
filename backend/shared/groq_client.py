"""Shared Groq client wrapper.

Two model tiers:
  FAST_MODEL  — high volume, used per-article (summaries)
  SMART_MODEL — lower volume, used per-conflict (bias analysis)

Both are within Groq's free tier limits at our expected ingestion rate
(~30 articles/day → ~30 fast calls; ~5-10 conflicts/day → ~10 smart calls).
"""
import json
import logging
import os
import time
import threading
from typing import Optional

log = logging.getLogger(__name__)

FAST_MODEL  = "llama-3.1-8b-instant"        # 30 RPM / 14,400 req/day on free tier
SMART_MODEL = "llama-3.3-70b-versatile"     # 30 RPM / 1,000 req/day on free tier

# Both models share a 30 RPM cap on free tier → minimum 2.1s between calls.
# Track last call timestamp per-model so we can stay under the limit without
# burning 4s waiting on each 429 retry.
_MIN_INTERVAL_S = 2.1
_last_call_at = {}      # model -> epoch seconds
_lock = threading.Lock()

_client = None

def get_client():
    """Lazy-init Groq client; returns None if no API key configured."""
    global _client
    if _client is not None:
        return _client
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        log.warning("[groq] GROQ_API_KEY not set — LLM tasks will be skipped")
        return None
    try:
        from groq import Groq
        _client = Groq(api_key=api_key)
        return _client
    except Exception as e:
        log.error(f"[groq] Failed to init client: {e}")
        return None


def _throttle(model: str) -> None:
    """Sleep just long enough to keep this model under the 30 RPM cap."""
    with _lock:
        last = _last_call_at.get(model, 0.0)
        wait = _MIN_INTERVAL_S - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        _last_call_at[model] = time.time()


def chat(prompt: str, model: str = FAST_MODEL, max_tokens: int = 400,
         json_mode: bool = False) -> Optional[str]:
    """Single-turn completion. Returns raw text, or None on failure."""
    client = get_client()
    if client is None:
        return None
    _throttle(model)
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content
    except Exception as e:
        log.warning(f"[groq] chat failed for model={model}: {type(e).__name__}: {e}")
        return None


def chat_json(prompt: str, model: str = SMART_MODEL,
              max_tokens: int = 600) -> Optional[dict]:
    """Single-turn completion with JSON output. Returns parsed dict or None."""
    raw = chat(prompt, model=model, max_tokens=max_tokens, json_mode=True)
    if not raw:
        log.warning(f"[groq] chat_json got no response from model={model}")
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning(f"[groq] JSON parse failed for model={model}: {e}; raw: {raw[:200]}")
        return None

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
from typing import Optional

log = logging.getLogger(__name__)

FAST_MODEL  = "llama-3.1-8b-instant"        # 14,400 req/day on free tier
SMART_MODEL = "llama-3.3-70b-versatile"     # 1,000 req/day on free tier

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


def chat(prompt: str, model: str = FAST_MODEL, max_tokens: int = 400,
         json_mode: bool = False) -> Optional[str]:
    """Single-turn completion. Returns raw text, or None on failure."""
    client = get_client()
    if client is None:
        return None
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
        log.warning(f"[groq] chat failed: {e}")
        return None


def chat_json(prompt: str, model: str = SMART_MODEL,
              max_tokens: int = 600) -> Optional[dict]:
    """Single-turn completion with JSON output. Returns parsed dict or None."""
    raw = chat(prompt, model=model, max_tokens=max_tokens, json_mode=True)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning(f"[groq] JSON parse failed: {e}; raw: {raw[:200]}")
        return None

"""Shared Groq client wrapper.

Two model tiers:
  FAST_MODEL  — high volume, used per-article (summaries, translation)
  SMART_MODEL — lower volume, used per-conflict (bias analysis)

Developer-plan rate limits (as of 2026-08):
  FAST_MODEL  (openai/gpt-oss-20b)   — 1,000 RPM / 250K TPM (paid, ~$0.075/$0.30 per 1M)
  SMART_MODEL (openai/gpt-oss-120b)  — 1,000 RPM / 250K TPM (paid, ~$0.15/$0.60 per 1M)

Note: llama-3.1-8b-instant and llama-3.3-70b-versatile were deprecated 2026-08-16.
These GPT-OSS models are the official replacements per console.groq.com/docs/deprecations.

Two guards are applied before every call:
  1. RPM throttle  — sleeps enough to stay under 1000 RPM per model
  2. Daily cap     — soft safety cap, set high (effectively uncapped at our usage level)

The daily counter resets at midnight UTC. Counts survive across ingestion
cycles within the same worker process (module-level state). On worker
restart the counter resets.
"""
import json
import logging
import os
import time
import threading
from datetime import datetime, timezone
from typing import Optional

from backend.shared.circuit_breaker import CircuitBreaker

log = logging.getLogger(__name__)

# Circuit breaker: OPEN after 5 consecutive Groq failures, resets after 5 min.
# Prevents hammering a failing API every 15 minutes and cluttering logs.
_groq_cb = CircuitBreaker('groq', failure_threshold=5, cooldown_s=300)

FAST_MODEL  = "openai/gpt-oss-20b"          # 1,000 RPM / 250K TPM (replaces llama-3.1-8b-instant)
SMART_MODEL = "openai/gpt-oss-120b"         # 1,000 RPM / 250K TPM (replaces llama-3.3-70b-versatile)

# Soft daily caps — set high since these are paid models with no strict daily req limit.
# Acts as a runaway-cost guard only. At our usage level (~500 calls/day) this won't trigger.
_DAILY_CAPS: dict[str, int] = {
    FAST_MODEL:  50_000,
    SMART_MODEL: 10_000,
}

# RPM guard — minimum seconds between calls to stay under 1000 RPM
_MIN_INTERVAL_S = 0.1

# ---- thread-safe shared state ------------------------------------------- #
_lock = threading.Lock()
_last_call_at: dict[str, float] = {}       # model -> epoch seconds

# Daily counter: model -> {"date": "YYYY-MM-DD", "count": int}
# "date" is UTC date string. When it no longer matches today, counter resets.
_daily: dict[str, dict] = {}

# Set to True (per model) the first time the cap is logged, so we don't spam
# the log with one WARNING per skipped call.
_cap_logged: dict[str, bool] = {}

_client = None
# --------------------------------------------------------------------------- #


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_daily_count(model: str) -> int:
    """Return today's call count for this model, resetting if the date rolled over."""
    today = _today_utc()
    entry = _daily.get(model)
    if entry is None or entry["date"] != today:
        _daily[model] = {"date": today, "count": 0}
        _cap_logged[model] = False          # reset cap warning flag for new day
    return _daily[model]["count"]


def _increment_daily(model: str) -> None:
    today = _today_utc()
    entry = _daily.get(model)
    if entry is None or entry["date"] != today:
        _daily[model] = {"date": today, "count": 1}
        _cap_logged[model] = False
    else:
        entry["count"] += 1


def _check_daily_cap(model: str) -> bool:
    """
    Returns True if the call should be allowed (under cap).
    Returns False if the daily cap is exhausted — logs once per day.
    """
    cap = _DAILY_CAPS.get(model)
    if cap is None:
        return True                          # unknown model — no cap enforced

    count = _get_daily_count(model)
    if count >= cap:
        if not _cap_logged.get(model, False):
            log.warning(
                f"[groq] Daily cap reached for {model}: {count}/{cap} requests used. "
                f"LLM tasks using this model will be skipped until midnight UTC. "
                f"Cap resets at 00:00 UTC."
            )
            _cap_logged[model] = True
        return False
    return True


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
    """Sleep just long enough to keep this model under the 1000 RPM cap."""
    with _lock:
        last = _last_call_at.get(model, 0.0)
        wait = _MIN_INTERVAL_S - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        _last_call_at[model] = time.time()


def chat(prompt: str, model: str = FAST_MODEL, max_tokens: int = 400,
         json_mode: bool = False) -> Optional[str]:
    """Single-turn completion. Returns raw text, or None on failure or cap exhausted."""
    client = get_client()
    if client is None:
        return None

    with _lock:
        allowed = _check_daily_cap(model)

    if not allowed:
        return None

    if not _groq_cb.allow():
        log.debug(f"[groq] circuit breaker OPEN — skipping call to {model}")
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
        with _lock:
            _increment_daily(model)
        _groq_cb.record_success()
        return resp.choices[0].message.content
    except Exception as e:
        log.warning(f"[groq] chat failed for model={model}: {type(e).__name__}: {e}")
        _groq_cb.record_failure()
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


def get_groq_cb_status() -> dict:
    """Return the Groq circuit breaker's current status snapshot."""
    return _groq_cb.status()


def get_daily_usage() -> dict[str, dict]:
    """
    Returns today's usage stats for all models that have been called.
    Useful for health checks or admin logging.

    Example return value:
      {
        "llama-3.1-8b-instant":    {"date": "2026-06-03", "count": 87,  "cap": 14400},
        "llama-3.3-70b-versatile": {"date": "2026-06-03", "count": 12,  "cap": 1000},
      }
    """
    with _lock:
        result = {}
        for model, entry in _daily.items():
            result[model] = {
                "date":  entry["date"],
                "count": entry["count"],
                "cap":   _DAILY_CAPS.get(model, None),
            }
        return result

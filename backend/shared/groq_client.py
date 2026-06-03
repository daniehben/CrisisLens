"""Shared Groq client wrapper.

Two model tiers:
  FAST_MODEL  — high volume, used per-article (summaries, translation)
  SMART_MODEL — lower volume, used per-conflict (bias analysis)

Free-tier limits (as of 2026):
  FAST_MODEL  (llama-3.1-8b-instant)     — 30 RPM / 14,400 req/day
  SMART_MODEL (llama-3.3-70b-versatile)  — 30 RPM /  1,000 req/day

Two guards are applied before every call:
  1. RPM throttle  — sleeps enough to stay under 30 RPM per model
  2. Daily cap     — refuses the call and returns None if today's quota is
                     exhausted, logs a single WARNING (not per-call noise)

The daily counter resets at midnight UTC. Counts survive across ingestion
cycles within the same worker process (module-level state). On worker
restart the counter resets — acceptable because free-tier quotas also
reset daily and restarts are rare.
"""
import json
import logging
import os
import time
import threading
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

FAST_MODEL  = "llama-3.1-8b-instant"        # 30 RPM / 14,400 req/day
SMART_MODEL = "llama-3.3-70b-versatile"     # 30 RPM /  1,000 req/day

# Daily request caps per model (free tier)
_DAILY_CAPS: dict[str, int] = {
    FAST_MODEL:  14_400,
    SMART_MODEL: 1_000,
}

# RPM guard — minimum seconds between calls to stay under 30 RPM
_MIN_INTERVAL_S = 2.1

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
    """Sleep just long enough to keep this model under the 30 RPM cap."""
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

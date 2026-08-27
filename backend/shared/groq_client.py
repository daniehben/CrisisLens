"""Shared Groq client wrapper.

Two model tiers:
  FAST_MODEL  — high volume, used per-article (summaries, translation)
  SMART_MODEL — lower volume, used per-conflict (bias analysis)

Developer-plan rate limits (as of 2026-08):
  FAST_MODEL  (openai/gpt-oss-20b)   — 1,000 RPM / 250K TPM (paid, ~$0.075/$0.30 per 1M)
  SMART_MODEL (openai/gpt-oss-120b)  — 1,000 RPM / 250K TPM (paid, ~$0.15/$0.60 per 1M)

Note: llama-3.1-8b-instant and llama-3.3-70b-versatile were deprecated 2026-08-16.
These GPT-OSS models are the official replacements per console.groq.com/docs/deprecations.

Four guards are applied before every call:
  1. RPM throttle     — sleeps enough to stay under 1000 RPM per model
  2. Daily cap        — soft safety cap (runaway-cost guard; effectively uncapped at current usage)
  3. TPD freeze       — when Groq reports "tokens per day" exhausted, halts calls until
                        the exact retry time from the error (or midnight UTC). Deterministic:
                        retrying before reset is guaranteed to fail and wastes quota.
  4. Proactive budget — reads x-ratelimit-remaining-tokens-day from every response header
                        and skips the next call if remaining tokens can't cover max_tokens +
                        prompt overhead. Prevents the 429 entirely rather than reacting to it.

The real remaining-token count comes from Groq's own response headers, not from our local
estimates. After each successful call, _tpd_remaining[model] is updated to what Groq says is
left. That value is exposed in the health endpoint and logged whenever it drops below 20%.

The daily counter resets at midnight UTC. Module-level state survives across ingestion
cycles in the same worker process; resets on worker restart.
"""
import json
import logging
import os
import re
import time
import threading
from datetime import datetime, timezone
from typing import Optional

from backend.shared.circuit_breaker import CircuitBreaker

log = logging.getLogger(__name__)

# Per-model circuit breakers: OPEN after 5 consecutive failures, reset after 5 min.
# One CB per model so a SMART_MODEL rate-limit storm (task13) doesn't block
# FAST_MODEL calls (task7.5, task8b) in the next ingestion cycle.
_groq_cbs: dict[str, CircuitBreaker] = {}

def _get_cb(model: str) -> CircuitBreaker:
    """Return the circuit breaker for this model, creating it on first use."""
    if model not in _groq_cbs:
        _groq_cbs[model] = CircuitBreaker(f'groq-{model}', failure_threshold=5, cooldown_s=300)
    return _groq_cbs[model]

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

# TPD (tokens-per-day) freeze: when Groq says "daily token limit exhausted",
# we freeze that model until the exact retry time Groq gives us.
# The circuit breaker (OPEN → HALF_OPEN every 5 min) is wrong for this case
# because every probe attempt also fails and wastes quota. A TPD freeze is
# deterministic — we know it won't work until the specified time.
_tpd_frozen_until: dict[str, float] = {}   # model -> epoch seconds

# Live token budget from Groq's response headers.
# Updated after every successful call. None = no data yet (worker just started).
# Groq sends x-ratelimit-remaining-tokens-day and x-ratelimit-limit-tokens-day
# with every response — these are the authoritative numbers, not our estimates.
_tpd_remaining: dict[str, int | None] = {}  # model -> tokens remaining today
_tpd_limit:     dict[str, int | None] = {}  # model -> total daily token ceiling

# If remaining tokens are below this threshold, skip the call rather than
# let it hit a 429. Covers max_tokens + typical prompt overhead (~500 tokens).
# Callers can override per-call by passing a larger max_tokens value — the check
# uses max_tokens * 2 so short prompts don't burn the last few thousand tokens.
_MIN_TOKENS_RESERVE = 2_000

# Log a warning when remaining tokens drop below this fraction of the daily limit.
_LOW_TOKEN_WARN_PCT = 0.20

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


def _is_tpd_frozen(model: str) -> bool:
    """Return True if this model is frozen due to daily token exhaustion."""
    until = _tpd_frozen_until.get(model, 0.0)
    return time.time() < until


def _set_tpd_freeze(model: str, error_message: str) -> None:
    """Parse Groq's 'try again in Xh Ym Zs' from a TPD error and freeze until then.
    Falls back to end-of-day UTC if the message can't be parsed."""
    # Groq error format: "...try again in 3h17m8.64s..."
    match = re.search(r'try again in (\d+)h(\d+)m([\d.]+)s', error_message, re.IGNORECASE)
    if match:
        h, m, s = int(match.group(1)), int(match.group(2)), float(match.group(3))
        freeze_for_s = h * 3600 + m * 60 + s
    else:
        # Can't parse exact time — freeze until 23:59:59 UTC today
        now = datetime.now(timezone.utc)
        midnight = now.replace(hour=23, minute=59, second=59, microsecond=0)
        freeze_for_s = max(0.0, (midnight - now).total_seconds())

    _tpd_frozen_until[model] = time.time() + freeze_for_s
    hours = int(freeze_for_s // 3600)
    mins  = int((freeze_for_s % 3600) // 60)
    log.warning(
        f"[groq] TPD exhausted for {model} — freezing calls for "
        f"~{hours}h {mins}m (until Groq resets). "
        f"Tasks using this model will be skipped until then."
    )


def _update_tpd_from_headers(model: str, headers) -> None:
    """Read actual remaining/limit token counts from a Groq response's HTTP headers.

    Called after every successful completion. Groq returns:
      x-ratelimit-remaining-tokens-day  — tokens left in today's budget
      x-ratelimit-limit-tokens-day      — total daily ceiling for this org/key

    These are the real numbers, not our local estimates. We store them and use
    them to make proactive skip decisions on the next call.
    """
    try:
        remaining_str = headers.get("x-ratelimit-remaining-tokens-day")
        limit_str     = headers.get("x-ratelimit-limit-tokens-day")

        if remaining_str is not None:
            remaining = int(remaining_str)
            _tpd_remaining[model] = remaining

            # Log a warning the first time we drop below the low-water mark.
            limit = _tpd_limit.get(model)
            if limit and limit > 0:
                pct = remaining / limit
                if pct < _LOW_TOKEN_WARN_PCT:
                    log.warning(
                        f"[groq] {model}: {remaining:,} / {limit:,} tokens remaining today "
                        f"({pct:.0%}) — LLM tasks will be skipped once budget is exhausted."
                    )
                else:
                    log.debug(
                        f"[groq] {model}: {remaining:,} / {limit:,} tokens remaining ({pct:.0%})"
                    )

        if limit_str is not None:
            _tpd_limit[model] = int(limit_str)

    except Exception as e:
        log.debug(f"[groq] Could not parse TPD headers for {model}: {e}")


def _has_token_budget(model: str, max_tokens: int) -> bool:
    """Return False if we know there isn't enough token budget left for this call.

    Uses the last-known remaining-tokens count from Groq's response headers.
    Skips the call proactively rather than letting it hit a 429.

    The check is: remaining >= max_tokens * 2
    The *2 accounts for the prompt (input) tokens we can't know exactly in advance.
    For a 400-token output with a 300-token prompt, total ≈ 700 — well within 2×.
    This is conservative: if remaining is unknown (None) we always allow the call.
    """
    remaining = _tpd_remaining.get(model)
    if remaining is None:
        return True                             # no data yet — allow and learn from the response
    needed = max(max_tokens * 2, _MIN_TOKENS_RESERVE)
    if remaining < needed:
        log.warning(
            f"[groq] {model}: only {remaining:,} tokens remaining today, "
            f"need ~{needed:,} for this call — skipping to avoid 429. "
            f"Budget resets at midnight UTC."
        )
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

    # TPD freeze check: if today's token quota is exhausted, don't even try.
    # Unlike the circuit breaker, we know the exact time it'll clear — no point probing.
    if _is_tpd_frozen(model):
        log.debug(f"[groq] TPD freeze active for {model} — skipping call")
        return None

    # Proactive budget check: if the last response told us how many tokens are left
    # and it isn't enough to cover this call, skip it before hitting a 429.
    if not _has_token_budget(model, max_tokens):
        return None

    cb = _get_cb(model)
    if not cb.allow():
        log.debug(f"[groq] circuit breaker OPEN for {model} — skipping call")
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
        # Use with_raw_response so we get HTTP headers alongside the parsed response.
        # Groq sends x-ratelimit-remaining-tokens-day in every response — reading it
        # here means we always have an up-to-date picture of our daily token budget.
        raw  = client.with_raw_response.chat.completions.create(**kwargs)
        resp = raw.parse()
        _update_tpd_from_headers(model, raw.headers)
        with _lock:
            _increment_daily(model)
        _get_cb(model).record_success()
        return resp.choices[0].message.content
    except Exception as e:
        err_str = str(e)
        log.warning(f"[groq] chat failed for model={model}: {type(e).__name__}: {err_str}")
        # Detect daily token quota exhaustion — different from a transient outage.
        # "tokens per day" appears in Groq's 429 error message for TPD limit hits.
        # Don't count these as circuit-breaker failures: retrying before reset is
        # pointless, and the probe attempt itself wastes quota.
        if "tokens per day" in err_str.lower() or "tpd" in err_str.lower():
            _set_tpd_freeze(model, err_str)
        else:
            _get_cb(model).record_failure()
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
    """Return circuit breaker status for all models that have been called."""
    return {model: cb.status() for model, cb in _groq_cbs.items()}


def get_daily_usage() -> dict[str, dict]:
    """
    Returns today's usage stats for all models that have been called.
    Exposed by the health endpoint so you can see actual token budget without SSH.

    Example return value:
      {
        "openai/gpt-oss-20b": {
          "date": "2026-08-27", "calls_today": 87, "calls_cap": 50000,
          "tokens_remaining": 45231, "tokens_limit": 200000, "tokens_pct": 22.6,
          "tpd_frozen": False,
        },
        "openai/gpt-oss-120b": {
          "date": "2026-08-27", "calls_today": 12, "calls_cap": 10000,
          "tokens_remaining": None,   # no successful call yet this process lifetime
          "tokens_limit": None,
          "tokens_pct": None,
          "tpd_frozen": False,
        },
      }

    tokens_remaining / tokens_limit come directly from Groq's response headers
    (x-ratelimit-remaining-tokens-day / x-ratelimit-limit-tokens-day) and are
    updated after every successful call. None means the worker hasn't made a
    successful call to that model yet since its last restart.
    """
    with _lock:
        result = {}
        for model, entry in _daily.items():
            frozen    = _is_tpd_frozen(model)
            remaining = _tpd_remaining.get(model)
            limit     = _tpd_limit.get(model)
            pct       = round(remaining / limit * 100, 1) if (remaining is not None and limit) else None

            row = {
                "date":             entry["date"],
                "calls_today":      entry["count"],
                "calls_cap":        _DAILY_CAPS.get(model, None),
                "tokens_remaining": remaining,
                "tokens_limit":     limit,
                "tokens_pct":       pct,
                "tpd_frozen":       frozen,
            }
            if frozen:
                until_s = _tpd_frozen_until.get(model, 0.0)
                row["tpd_frozen_until_utc"] = datetime.fromtimestamp(
                    until_s, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            result[model] = row
        return result

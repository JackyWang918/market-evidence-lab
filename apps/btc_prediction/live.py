"""Forward-only BTC continuity experiment with durable prediction issuance times."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .experiment import candle_change_pct, candle_direction


LIVE_STATE_VERSION = "continuity_live_v1"
FIVE_MINUTES_MS = 300_000


def fetch_closed_candles(*, proxy_url: str | None, now: datetime) -> list[dict[str, str]]:
    with httpx.Client(timeout=15, proxy=proxy_url or None, trust_env=False) as client:
        response = client.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": "BTCUSDT", "interval": "5m", "limit": 500},
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Unexpected Binance kline response")
    now_ms = int(now.timestamp() * 1000)
    candles = []
    for item in payload:
        if not isinstance(item, list) or len(item) < 11:
            raise ValueError("Invalid Binance kline row")
        opening = int(item[0])
        closing = int(item[6])
        if closing >= now_ms or closing != opening + FIVE_MINUTES_MS - 1:
            continue
        candles.append({
            "open_time": str(opening), "open": str(item[1]), "high": str(item[2]),
            "low": str(item[3]), "close": str(item[4]), "close_time": str(closing),
        })
    for left, right in zip(candles, candles[1:]):
        if int(right["open_time"]) - int(left["open_time"]) != FIVE_MINUTES_MS:
            raise ValueError("Binance returned discontinuous 5m candles")
    return candles


def new_state(now: datetime, candles: list[dict[str, str]]) -> dict:
    if not candles:
        raise ValueError("No closed BTCUSDT candles available")
    state = {
        "version": LIVE_STATE_VERSION,
        "mode": "live_forward",
        "started_at_utc": now.astimezone(UTC).isoformat(),
        "last_processed_open_time": int(candles[-1]["open_time"]),
        "streak_direction": None,
        "streak_candles": [],
        "chain_id": 0,
        "pending_sequence": None,
        "late_skips": 0,
        "data_gaps": 0,
        "records": [],
    }
    for candle in candles:
        _extend_streak(state, candle)
    _maybe_issue_from_streak(state, candles[-1], now)
    return state


def _extend_streak(state: dict, candle: dict[str, str]) -> None:
    direction = candle_direction(candle)
    if direction == "flat":
        if state["streak_direction"] is not None:
            state["streak_candles"] = (state["streak_candles"] + [candle])[-3:]
    elif direction == state["streak_direction"]:
        state["streak_candles"] = (state["streak_candles"] + [candle])[-3:]
    else:
        state["streak_direction"] = direction
        state["streak_candles"] = [candle]


def _can_issue(target_open_time: int, now: datetime) -> bool:
    lag_ms = int(now.timestamp() * 1000) - target_open_time
    return 0 <= lag_ms <= 60_000


def _issue(state: dict, *, direction: str, chain_id: int, chain_step: int,
           phase: str, triggers: list[dict[str, str]], signal: dict[str, str],
           now: datetime) -> None:
    target = int(signal["open_time"]) + FIVE_MINUTES_MS
    record = {
        "sequence": len(state["records"]) + 1,
        "chain_id": chain_id,
        "chain_step": chain_step,
        "phase": phase,
        "direction": direction,
        "trigger_candles": triggers,
        "signal_candle": signal,
        "prediction_open_time": target,
        "issued_at_utc": now.astimezone(UTC).isoformat(),
        "actual_candle": None,
        "actual_change_pct": None,
        "actual_direction": None,
        "outcome": "pending",
        "settled_at_utc": None,
    }
    state["records"].append(record)
    state["pending_sequence"] = record["sequence"]


def _maybe_issue_from_streak(state: dict, candle: dict[str, str], now: datetime) -> None:
    if len(state["streak_candles"]) < 3 or state["pending_sequence"] is not None:
        return
    target = int(candle["open_time"]) + FIVE_MINUTES_MS
    if not _can_issue(target, now):
        return
    state["chain_id"] += 1
    _issue(
        state, direction=state["streak_direction"], chain_id=state["chain_id"],
        chain_step=1, phase="three_candles", triggers=state["streak_candles"].copy(),
        signal=candle, now=now,
    )
    state["streak_direction"] = None
    state["streak_candles"] = []


def advance_state(state: dict, candles: list[dict[str, str]], now: datetime) -> bool:
    """Settle issued predictions, then issue only for an unfinished next candle."""
    changed = False
    unseen = [
        candle for candle in candles
        if int(candle["open_time"]) > state["last_processed_open_time"]
    ]
    if unseen and int(unseen[0]["open_time"]) != state["last_processed_open_time"] + FIVE_MINUTES_MS:
        state["data_gaps"] = state.get("data_gaps", 0) + 1
        if state["pending_sequence"] is not None:
            record = state["records"][state["pending_sequence"] - 1]
            record["outcome"] = "unresolved_gap"
            record["settled_at_utc"] = now.astimezone(UTC).isoformat()
            state["pending_sequence"] = None
        state["streak_direction"] = None
        state["streak_candles"] = []
        state["last_processed_open_time"] = int(unseen[0]["open_time"]) - FIVE_MINUTES_MS
        changed = True
    for candle in candles:
        opening = int(candle["open_time"])
        if opening <= state["last_processed_open_time"]:
            continue
        if opening != state["last_processed_open_time"] + FIVE_MINUTES_MS:
            raise ValueError("Binance returned discontinuous live candles")
        state["last_processed_open_time"] = opening
        changed = True
        sequence = state["pending_sequence"]
        if sequence is not None:
            record = state["records"][sequence - 1]
            if record["prediction_open_time"] != opening:
                raise ValueError("Pending live prediction does not match candle")
            actual_direction = candle_direction(candle)
            won = actual_direction in {record["direction"], "flat"}
            record["actual_candle"] = candle
            record["actual_change_pct"] = str(candle_change_pct(candle))
            record["actual_direction"] = actual_direction
            record["outcome"] = "win" if won else "loss"
            record["settled_at_utc"] = now.astimezone(UTC).isoformat()
            state["pending_sequence"] = None
            if won and _can_issue(opening + FIVE_MINUTES_MS, now):
                _issue(
                    state, direction=record["direction"], chain_id=record["chain_id"],
                    chain_step=record["chain_step"] + 1, phase="continuation",
                    triggers=record["trigger_candles"], signal=candle, now=now,
                )
            elif won:
                state["late_skips"] += 1
                state["streak_direction"] = None
                state["streak_candles"] = []
            else:
                state["streak_direction"] = None
                state["streak_candles"] = []
            continue
        _extend_streak(state, candle)
        _maybe_issue_from_streak(state, candle, now)
    return changed


def load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("version") != LIVE_STATE_VERSION:
        raise ValueError("Unsupported live experiment state version")
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)

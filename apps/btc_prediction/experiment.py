"""Deterministic, candle-by-candle replay of the first continuity rule."""

import csv
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


RULE_VERSION = "continuity_v1"
PREDICTION_FIELDS = (
    "sequence", "chain_id", "chain_step", "phase", "direction",
    "trigger_1_open_time", "trigger_2_open_time", "trigger_3_open_time",
    "signal_open_time", "prediction_open_time", "signal_close", "prediction_open",
    "prediction_high", "prediction_low", "prediction_close", "actual_change_pct",
    "actual_direction", "outcome",
)


def candle_change_pct(row: dict[str, str]) -> Decimal:
    opening = Decimal(row["open"])
    if opening <= 0:
        raise ValueError("Candle open price must be positive")
    change = ((Decimal(row["close"]) / opening - 1) * 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return Decimal("0.00") if change == 0 else change


def candle_direction(row: dict[str, str]) -> str:
    change = candle_change_pct(row)
    return "up" if change > 0 else "down" if change < 0 else "flat"


def read_candles(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        candles = list(csv.DictReader(stream))
    if len(candles) < 4:
        raise ValueError("At least four candles are needed")
    previous = None
    for row in candles:
        opening = int(row["open_time"])
        if previous is not None and opening - previous != 300_000:
            raise ValueError(f"Candle gap or duplicate after {previous}")
        if int(row["close_time"]) != opening + 299_999:
            raise ValueError(f"Invalid close time at {opening}")
        candle_change_pct(row)
        previous = opening
    return candles


def replay(candles: list[dict[str, str]]) -> list[dict[str, str]]:
    """Only a closed candle can create the prediction for the next candle."""
    records = []
    pending = None
    streak_direction = None
    streak_indices: list[int] = []
    chain_id = 0

    for index, candle in enumerate(candles):
        observed = candle_direction(candle)
        if pending is not None:
            if int(candle["open_time"]) != pending["prediction_open_time"]:
                raise ValueError("Pending prediction does not match the next candle")
            won = observed == pending["direction"] or observed == "flat"
            record = _record(pending, candles, candle, observed, "win" if won else "loss")
            records.append(record)
            if won:
                pending = {
                    **pending,
                    "chain_step": pending["chain_step"] + 1,
                    "phase": "continuation",
                    "signal_index": index,
                    "prediction_open_time": int(candle["open_time"]) + 300_000,
                }
            else:
                pending = None
                streak_direction = None
                streak_indices = []
            # The losing candle starts no new streak; a winning one stays in this chain.
            continue

        if observed == "flat":
            if streak_direction is not None:
                streak_indices.append(index)
        elif observed == streak_direction:
            streak_indices.append(index)
        else:
            streak_direction = observed
            streak_indices = [index]

        if len(streak_indices) >= 3:
            chain_id += 1
            pending = {
                "chain_id": chain_id,
                "chain_step": 1,
                "phase": "three_candles",
                "direction": streak_direction,
                "trigger_indices": tuple(streak_indices[-3:]),
                "signal_index": index,
                "prediction_open_time": int(candle["open_time"]) + 300_000,
            }
            streak_direction = None
            streak_indices = []

    if pending is not None:
        records.append(_record(pending, candles, None, "", "pending"))
    for sequence, record in enumerate(records, start=1):
        record["sequence"] = str(sequence)
    return records


def _record(pending, candles, actual, observed, outcome):
    source = candles[pending["signal_index"]]
    triggers = [candles[i] for i in pending["trigger_indices"]]
    return {
        "sequence": "",
        "chain_id": str(pending["chain_id"]),
        "chain_step": str(pending["chain_step"]),
        "phase": pending["phase"],
        "direction": pending["direction"],
        "trigger_1_open_time": triggers[0]["open_time"],
        "trigger_2_open_time": triggers[1]["open_time"],
        "trigger_3_open_time": triggers[2]["open_time"],
        "signal_open_time": source["open_time"],
        "prediction_open_time": str(pending["prediction_open_time"]),
        "signal_close": source["close"],
        "prediction_open": actual["open"] if actual else "",
        "prediction_high": actual["high"] if actual else "",
        "prediction_low": actual["low"] if actual else "",
        "prediction_close": actual["close"] if actual else "",
        "actual_change_pct": str(candle_change_pct(actual)) if actual else "",
        "actual_direction": observed,
        "outcome": outcome,
    }


def save_replay(source: Path, output_dir: Path) -> tuple[Path, Path, dict]:
    candles = read_candles(source)
    records = replay(candles)
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    stem = (
        f"{RULE_VERSION}_{candles[0]['open_time']}_{candles[-1]['open_time']}"
        f"_{source_hash[:12]}"
    )
    records_path = output_dir / f"{stem}.csv"
    summary_path = output_dir / f"{stem}.json"
    source_snapshot = output_dir / f"{stem}_source.csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    if not source_snapshot.exists():
        snapshot_temp = source_snapshot.with_suffix(".tmp")
        snapshot_temp.write_bytes(source_bytes)
        snapshot_temp.replace(source_snapshot)
    temporary = records_path.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=PREDICTION_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    temporary.replace(records_path)
    outcome_counts = Counter(row["outcome"] for row in records)
    actual_counts = Counter(
        row["actual_direction"] for row in records if row["outcome"] != "pending"
    )
    decided_count = outcome_counts["win"] + outcome_counts["loss"]
    directional_count = actual_counts["up"] + actual_counts["down"]
    flat_wins = actual_counts["flat"]
    summary = {
        "rule_version": RULE_VERSION,
        "mode": "historical_replay",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "original_source_file": source.name,
        "source_file": source_snapshot.name,
        "source_sha256": source_hash,
        "first_candle_open_time": candles[0]["open_time"],
        "last_candle_open_time": candles[-1]["open_time"],
        "candle_count": len(candles),
        "chain_count": max((int(row["chain_id"]) for row in records), default=0),
        "prediction_count": decided_count,
        "wins": outcome_counts["win"],
        "losses": outcome_counts["loss"],
        "pending": outcome_counts["pending"],
        "flat_wins": flat_wins,
        "strict_direction_wins": outcome_counts["win"] - flat_wins,
        "strict_direction_accuracy_pct": round(
            100 * (outcome_counts["win"] - flat_wins) / directional_count, 2
        ) if directional_count else None,
        "always_up_accuracy_pct": round(
            100 * (actual_counts["up"] + flat_wins) / decided_count, 2
        ) if decided_count else None,
        "always_down_accuracy_pct": round(
            100 * (actual_counts["down"] + flat_wins) / decided_count, 2
        ) if decided_count else None,
        "accuracy_pct": round(
            100 * outcome_counts["win"] / decided_count, 2
        ) if decided_count else None,
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return records_path, summary_path, summary

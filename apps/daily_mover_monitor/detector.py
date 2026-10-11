from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence


@dataclass(frozen=True)
class Candle:
    open_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class Signal:
    window: int
    low_index: int
    high_index: int
    low: Decimal
    high: Decimal
    rise_pct: Decimal
    drawdown_pct: Decimal


def window_signal(candles: Sequence[Candle], days: int) -> Signal | None:
    if len(candles) < days:
        return None
    rows = candles[-days:]
    low_index = min(range(days), key=lambda index: rows[index].low)
    high_index = max(range(days), key=lambda index: rows[index].high)
    low = rows[low_index].low
    high = rows[high_index].high
    if low <= 0 or low_index >= high_index or high < low * 2:
        return None
    peak = max(row.high for row in rows[low_index:])
    drawdown = (peak - rows[-1].close) / peak * 100
    return Signal(
        window=days,
        low_index=len(candles) - days + low_index,
        high_index=len(candles) - days + high_index,
        low=low,
        high=high,
        rise_pct=(high - low) / low * 100,
        drawdown_pct=drawdown,
    )


def detect(candles: Sequence[Candle]) -> tuple[str, Signal, Decimal] | None:
    if len(candles) != 9 or candles[0].close <= 0:
        return None
    if any(candles[index + 1].open_at.date().toordinal() - candles[index].open_at.date().toordinal() != 1 for index in range(8)):
        return None
    change_8d = (candles[-1].close - candles[0].close) / candles[0].close * 100
    if change_8d < 30:
        return None
    five = window_signal(candles, 5)
    seven = window_signal(candles, 7)
    if five and seven:
        return "5D+7D", five, change_8d
    if five:
        return "5D 翻倍", five, change_8d
    if seven:
        return "7D 翻倍", seven, change_8d
    return None

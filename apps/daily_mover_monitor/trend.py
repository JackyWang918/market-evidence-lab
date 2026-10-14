"""Causal, daily-close research observations for early mover candidates."""

from datetime import timedelta
from decimal import Decimal
from statistics import median


VERSION = "trend-research-v1"
HISTORY_DAYS = 90
ARCHIVE_AFTER_DAYS = 120
MIN_QUOTE_VOLUME = Decimal("100000")


def _contiguous(rows):
    return all(
        rows[index].open_at.date() - rows[index - 1].open_at.date() == timedelta(days=1)
        for index in range(1, len(rows))
    )


def candidate_reason(rows):
    """Return a first-discovery reason using completed candles only."""
    if len(rows) < 21 or not _contiguous(rows[-21:]):
        return None
    recent = rows[-1]
    if recent.close <= 0 or any(row.quote_volume is None for row in rows[-5:]):
        return None
    if median(row.quote_volume for row in rows[-5:]) < MIN_QUOTE_VOLUME:
        return None
    prior_high = max(row.close for row in rows[-21:-1])
    if recent.close <= prior_high * Decimal("1.01"):
        return None
    if recent.close < rows[-5].close * Decimal("1.08"):
        return None
    return "20日收盘新高且近5日上涨至少8%"


def analyze(rows, *, cutoff, previous=None):
    rows = [row for row in rows if row.open_at.date() <= cutoff]
    if len(rows) < 21 or rows[-1].open_at.date() != cutoff or not _contiguous(rows[-min(30, len(rows)):]):
        raise ValueError("需要截至昨日的连续21根完整日K")
    close = rows[-1].close
    prev14 = max(row.close for row in rows[-15:-1])
    prev30 = max(row.close for row in rows[-31:-1]) if len(rows) >= 31 else None
    new14 = close > prev14
    new30 = close > prev30 if prev30 is not None else None
    center10 = Decimal(median(row.close for row in rows[-10:]))
    prior_center10 = Decimal(median(row.close for row in rows[-20:-10]))
    # A pivot at i is only usable after the two candles to its right close.
    pivots = [
        (rows[i].open_at.date().isoformat(), rows[i].low)
        for i in range(2, len(rows) - 2)
        if all(rows[i].low < rows[j].low for j in (i - 2, i - 1, i + 1, i + 2))
    ]
    prior_peak = max(row.close for row in rows[-30:-1])
    drawdown = (prior_peak - close) / prior_peak * 100 if prior_peak > close else Decimal(0)
    lows_rising = len(pivots) >= 2 and pivots[-1][1] > pivots[-2][1]
    lows_falling = len(pivots) >= 2 and pivots[-1][1] < pivots[-2][1]
    prior_stage = previous.get("stage") if previous else None
    if new30:
        stage = "再次扩张" if prior_stage and prior_stage not in ("初次扩张", "再次扩张") else (prior_stage or "初次扩张")
    elif prior_stage in ("走弱", "回升观察") and close > max(row.close for row in rows[-6:-1]) and center10 >= prior_center10:
        stage = "回升观察"
    elif drawdown >= 25 or (lows_falling and center10 < prior_center10 * Decimal("0.95")):
        stage = "走弱"
    elif new14:
        stage = "再次扩张" if prior_stage and prior_stage not in ("初次扩张", "再次扩张") else (prior_stage or "初次扩张")
    elif close >= prior_peak * Decimal("0.85") and center10 >= prior_center10 * Decimal("0.98"):
        stage = "高位整理"
    else:
        stage = "回撤观察"
    return {
        "cutoff": cutoff.isoformat(), "stage": stage,
        "stage_changed": bool(previous and previous.get("stage") != stage),
        "new_close_high_14d": new14, "new_close_high_30d": new30,
        "close": str(close), "close_high_30d": str(max(row.close for row in rows[-30:])),
        "close_high_14d_count": sum(row.close > max(r.close for r in rows[max(0, i-14):i])
                                      for i, row in enumerate(rows) if i >= 14 and row.open_at.date() >= cutoff - timedelta(days=29)),
        "center_10d": str(center10), "prior_center_10d": str(prior_center10),
        "center_rising": center10 > prior_center10,
        "confirmed_lows": [{"date": day, "price": str(price)} for day, price in pivots[-4:]],
        "lows_rising": lows_rising, "lows_falling": lows_falling,
        "drawdown_from_prior_close_high_pct": str(drawdown),
    }

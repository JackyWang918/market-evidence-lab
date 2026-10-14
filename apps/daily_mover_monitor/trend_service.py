"""Independent daily candidate sweep and long-lived trend observations."""

import hashlib
import json
import logging
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from .detector import Candle
from .models import DailyMoverEntry, TrendObservation, TrendWatch
from .source import FuturesMarketClient
from .trend import ARCHIVE_AFTER_DAYS, HISTORY_DAYS, VERSION, analyze, candidate_reason

logger = logging.getLogger(__name__)


def _packed(rows):
    return [{"date": row.open_at.date().isoformat(), "open": str(row.open),
             "high": str(row.high), "low": str(row.low), "close": str(row.close),
             "quote_volume": str(row.quote_volume) if row.quote_volume is not None else None}
            for row in rows]


def _unpacked(rows):
    return [Candle(datetime.combine(date.fromisoformat(row["date"]), time.min, UTC),
                   Decimal(row["open"]), Decimal(row["high"]), Decimal(row["low"]),
                   Decimal(row["close"]), Decimal(row["quote_volume"]) if row["quote_volume"] is not None else None)
            for row in rows]


def _merge(old, new):
    merged = {row.open_at.date(): row for row in new}
    merged.update({row.open_at.date(): row for row in old})
    return [merged[day] for day in sorted(merged)]


def update_official(day, *, client=None, histories=None):
    """Idempotent sweep. A watch is independent of rank and survives drawdowns."""
    source = client or FuturesMarketClient()
    owns_source = client is None
    errors = []
    count = 0
    try:
        symbols = source.symbols()
        for symbol, quote_asset in symbols:
            try:
                rows = (histories or {}).get(symbol)
                if rows is None:
                    rows = source.candles(symbol, day, days=HISTORY_DAYS)
                if not rows or rows[-1].open_at.date() != day - timedelta(days=1):
                    continue
                watch = TrendWatch.objects.filter(symbol=symbol, rule_version=VERSION, archived_on__isnull=True).first()
                if watch is None:
                    reason = candidate_reason(rows)
                    if reason is None:
                        continue
                    bid, ask, spread = source.depth(symbol)
                    if min(bid, ask) < Decimal(settings.DAILY_MOVER_MIN_SIDE_DEPTH) or spread > Decimal(settings.DAILY_MOVER_MAX_SPREAD_BPS):
                        continue
                    watch, _ = TrendWatch.objects.get_or_create(
                        symbol=symbol, discovered_on=day, rule_version=VERSION,
                        defaults={"quote_asset": quote_asset, "last_high_on": day,
                                  "discovery_reason": reason, "discovery_bid_depth": bid,
                                  "discovery_ask_depth": ask, "discovery_spread_bps": spread},
                    )
                latest = watch.observations.filter(rule_version=VERSION, as_of_date__lte=day).first()
                if latest and latest.as_of_date == day:
                    continue
                if latest:
                    rows = _merge(_unpacked(latest.result.get("candles", [])), rows)
                start = latest.as_of_date + timedelta(days=1) if latest else watch.discovered_on
                previous = latest.result if latest else None
                for offset in range((day - start).days + 1):
                    current = start + timedelta(days=offset)
                    cutoff = current - timedelta(days=1)
                    try:
                        result = analyze(rows, cutoff=cutoff, previous=previous)
                    except ValueError:
                        continue
                    subset = [row for row in rows if row.open_at.date() <= cutoff]
                    packed = _packed(subset)
                    result["candles"] = packed
                    result["ranked"] = DailyMoverEntry.objects.filter(symbol=symbol, snapshot__as_of_date=current).exists()
                    result["discovered_on"] = watch.discovered_on.isoformat()
                    digest = hashlib.sha256(json.dumps(packed, sort_keys=True).encode()).hexdigest()
                    with transaction.atomic():
                        observation, created = TrendObservation.objects.get_or_create(
                            watch=watch, as_of_date=current, rule_version=VERSION,
                            defaults={"data_cutoff_at": datetime.combine(current, time.min, UTC),
                                      "input_hash": digest, "result": result},
                        )
                        if result["new_close_high_30d"]:
                            watch.last_high_on = current
                            watch.save(update_fields=["last_high_on"])
                        if (current - watch.last_high_on).days >= ARCHIVE_AFTER_DAYS:
                            watch.archived_on = current
                            watch.save(update_fields=["archived_on"])
                    previous = observation.result
                    count += int(created)
                    if watch.archived_on:
                        break
            except Exception as exc:
                errors.append(f"{symbol}: {type(exc).__name__}")
                logger.warning("趋势观察失败 %s: %s", symbol, type(exc).__name__)
        # Missing contracts retain their last observation; a later re-listing can resume.
    finally:
        if owns_source:
            source.close()
    return count, errors

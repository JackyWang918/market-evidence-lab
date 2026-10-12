import hashlib
import json
import logging
from datetime import UTC, date, datetime, time, timedelta

from django.db import transaction

from .models import DailyMoverEntry, DailyMoverSnapshot, StructureObservation, StructureWatch
from .preview import preview_rows
from .source import FuturesMarketClient
from .structure import RULES, VERSION, analyze, pack_candles

logger = logging.getLogger(__name__)


def observe(watch, day, rows, *, ranked=False):
    existing = watch.observations.filter(as_of_date=day, rule_version=VERSION).first()
    if existing:
        return existing
    cutoff = day-timedelta(days=1)
    result = analyze(rows, cutoff=cutoff, anchor_date=watch.anchor_date, anchor_price=watch.anchor_price)
    if result["state"] == "数据不足":
        # Do not permanently occupy the immutable daily slot with incomplete input.
        # The watch remains pending and a later retry can publish a valid judgment.
        raise ValueError(result["reason"])
    result["ranked"] = ranked
    result["origin"] = watch.origin
    result["discovered_on"] = watch.discovered_on.isoformat()
    result["dropout_days"] = (day-watch.last_ranked_on).days if not ranked else 0
    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
    with transaction.atomic():
        observation, _ = StructureObservation.objects.get_or_create(
            watch=watch, as_of_date=day, rule_version=VERSION,
            defaults={"data_cutoff_at": datetime.combine(day, time.min, UTC),
                      "input_hash": hashlib.sha256(serialized.encode()).hexdigest(), "result": result},
        )
        failure = result.get("failure_date")
        if failure and (cutoff-date.fromisoformat(failure)).days >= RULES["failure_followup_days"]:
            watch.ended_on, watch.end_reason = day, "平台失效后已完成 10 日观察"
        elif (day-watch.last_ranked_on).days >= RULES["dropout_days"]:
            watch.ended_on, watch.end_reason = day, "掉榜后已完成 30 日观察"
        if watch.ended_on:
            watch.save(update_fields=["ended_on", "end_reason"])
    return observation


def seed(entry, origin):
    day = entry.snapshot.as_of_date
    from django.db.models import Q
    active = StructureWatch.objects.filter(symbol=entry.symbol, origin=origin, discovered_on__lte=day).filter(
        Q(ended_on__isnull=True) | Q(ended_on__gte=day)
    ).first()
    if active:
        return active
    return StructureWatch.objects.get_or_create(
        symbol=entry.symbol, origin=origin, discovered_on=day,
        defaults={"quote_asset": entry.quote_asset, "last_ranked_on": day,
                  "anchor_date": entry.low_at.date(), "anchor_price": entry.low_price},
    )[0]


def merge_rows(old, new):
    # Existing published input prices take precedence over later source revisions.
    merged = {r["time"]: r for r in new}
    merged.update({r["time"]: r for r in old})
    return [merged[k] for k in sorted(merged)]


def update_official(day, *, client=None):
    if not DailyMoverSnapshot.objects.filter(as_of_date=day).exists():
        return 0, ["当日正式榜单尚未完成，入榜状态未知"]
    source = client or FuturesMarketClient()
    entries = list(DailyMoverEntry.objects.filter(snapshot__as_of_date=day).select_related("snapshot"))
    for entry in entries:
        seed(entry, "official")
    ranked = {e.symbol for e in entries}
    failures = []
    count = 0
    try:
        watches = list(StructureWatch.objects.filter(origin="official", ended_on__isnull=True, discovered_on__lte=day))
        for watch in watches:
            latest = watch.observations.filter(rule_version=VERSION, as_of_date__lte=day).first()
            start = latest.as_of_date+timedelta(days=1) if latest else watch.discovered_on
            if start > day:
                continue
            try:
                rows = pack_candles(source.candles(watch.symbol, day, days=RULES["history_days"]))
                old = latest.result.get("candles", []) if latest else []
                # Keep the original discovery snapshot's OHLC wherever available.
                discovery = DailyMoverEntry.objects.filter(symbol=watch.symbol, snapshot__as_of_date=watch.discovered_on).first()
                old = merge_rows(old, discovery.candles if discovery else [])
                rows = merge_rows(old, rows)
                for offset in range((day-start).days+1):
                    current = start+timedelta(days=offset)
                    is_ranked = watch.symbol in ranked if current == day else DailyMoverEntry.objects.filter(symbol=watch.symbol, snapshot__as_of_date=current).exists()
                    # Missing board days are unknown, not evidence of dropout.
                    board_known = current == day or DailyMoverSnapshot.objects.filter(as_of_date=current).exists()
                    if is_ranked:
                        watch.last_ranked_on = current
                        watch.save(update_fields=["last_ranked_on"])
                    if not board_known:
                        continue
                    observe(watch, current, rows, ranked=is_ranked)
                    count += 1
                    if watch.ended_on:
                        break
            except Exception as exc:
                failures.append(f"{watch.symbol}: {type(exc).__name__}")
                logger.warning("结构采集失败 %s: %s", watch.symbol, type(exc).__name__)
    finally:
        if client is None:
            source.close()
    return count, failures


def replay(*, symbol=None, end=None, client=None):
    """Historical trials stay separate from official watches and rankings."""
    by_day, _ = preview_rows()
    official_days = DailyMoverSnapshot.objects.values_list("as_of_date", flat=True)
    for day in official_days:
        by_day[day] = list(DailyMoverEntry.objects.filter(snapshot__as_of_date=day).select_related("snapshot"))
    if not by_day:
        return 0, ["没有可供回放的榜单"]
    end = end or max(by_day)
    by_day = {day: entries for day, entries in by_day.items() if day <= end}
    seeds = {}
    for day in sorted(by_day):
        for entry in by_day[day]:
            if symbol is None or entry.symbol == symbol:
                seeds.setdefault(entry.symbol, entry)
    source = client or FuturesMarketClient()
    count, errors = 0, []
    try:
        for ticker, entry in seeds.items():
            try:
                first = entry.snapshot.as_of_date
                # Fetch in <= 90-day batches, including pre-discovery pressure history.
                rows = []
                cursor = first-timedelta(days=RULES["history_days"])
                while cursor < end:
                    batch_end = min(cursor+timedelta(days=90), end)
                    fetched = pack_candles(source.candles(ticker, batch_end, days=(batch_end-cursor).days))
                    rows = merge_rows(rows, fetched)
                    cursor = batch_end
                rows = merge_rows(entry.candles, rows)
                watch = None
                for offset in range((end-first).days+1):
                    day = first+timedelta(days=offset)
                    if day not in by_day:
                        continue
                    current_entry = next((e for e in by_day[day] if e.symbol == ticker), None)
                    if watch is None or (watch.ended_on and watch.ended_on < day):
                        if current_entry is None:
                            continue
                        watch = seed(current_entry, "replay")
                    if watch.observations.filter(as_of_date=day, rule_version=VERSION).exists():
                        count += 1
                        continue
                    if current_entry:
                        watch.last_ranked_on = day
                        watch.save(update_fields=["last_ranked_on"])
                    observe(watch, day, rows, ranked=current_entry is not None)
                    count += 1
            except Exception as exc:
                errors.append(f"{ticker}: {type(exc).__name__}")
                logger.warning("结构回放失败 %s: %s", ticker, type(exc).__name__)
    finally:
        if client is None:
            source.close()
    return count, errors

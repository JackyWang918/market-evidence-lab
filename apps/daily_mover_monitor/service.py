from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .detector import detect
from .models import DailyMoverEntry, DailyMoverRun, DailyMoverSnapshot
from .source import FuturesMarketClient


SHANGHAI = ZoneInfo("Asia/Shanghai")


def today_in_shanghai() -> date:
    return timezone.now().astimezone(SHANGHAI).date()


def scan(
    as_of_date: date | None = None,
    *,
    client: FuturesMarketClient | None = None,
    now: datetime | None = None,
) -> DailyMoverSnapshot:
    current = now or timezone.now()
    local = current.astimezone(SHANGHAI)
    day = as_of_date or local.date()
    if day != local.date():
        raise ValueError("盘口仅有实时数据；只允许生成今日快照")
    if local.hour < 8 or (local.hour == 8 and local.minute < 20):
        raise ValueError("须在北京时间 08:20 后扫描上一根完整日 K")
    existing = DailyMoverSnapshot.objects.filter(as_of_date=day).first()
    if existing:
        if _update_structures(day, client):
            _send_report(existing)
        return existing
    with transaction.atomic():
        run, created = DailyMoverRun.objects.select_for_update().get_or_create(
            as_of_date=day, defaults={"status": DailyMoverRun.Status.RUNNING}
        )
        if not created:
            if run.status == DailyMoverRun.Status.RUNNING and run.started_at >= timezone.now() - timedelta(minutes=30):
                raise RuntimeError("今日扫描正在运行")
            run.status = DailyMoverRun.Status.RUNNING
            run.started_at = timezone.now()
            run.finished_at = None
            run.error_summary = ""
            run.save(update_fields=["status", "started_at", "finished_at", "error_summary"])

    source = client or FuturesMarketClient()
    owns_source = client is None
    try:
        symbols = source.symbols()
        if not symbols:
            raise RuntimeError("币安未返回可扫描的永续合约")
        candidates = []
        latest_candles = 0
        for symbol, quote_asset in symbols:
            candles = source.candles(symbol, day)
            if candles:
                if candles[-1].open_at.date() != day - timedelta(days=1):
                    raise RuntimeError(f"{symbol} 缺少上一根完整日 K")
                latest_candles += 1
            result = detect(candles)
            if result:
                signal_name, signal, baseline_change = result
                candidates.append((symbol, quote_asset, candles, signal_name, signal, baseline_change))
        if latest_candles == 0:
            raise RuntimeError("没有取得上一根完整日 K")

        eligible = []
        excluded = 0
        min_depth = Decimal(settings.DAILY_MOVER_MIN_SIDE_DEPTH)
        max_spread = Decimal(settings.DAILY_MOVER_MAX_SPREAD_BPS)
        for symbol, quote_asset, candles, signal_name, signal, baseline_change in candidates:
            bid_depth, ask_depth, spread_bps = source.depth(symbol)
            if min(bid_depth, ask_depth) < min_depth or spread_bps > max_spread:
                excluded += 1
                continue
            eligible.append(
                (symbol, quote_asset, candles, signal_name, signal, baseline_change,
                 bid_depth, ask_depth, spread_bps)
            )

        eligible.sort(key=lambda row: (
            0 if "5D" in row[3] else 1,
            row[4].drawdown_pct,
            row[0],
        ))
        previous_day = day - timedelta(days=1)
        previous = {
            entry.symbol: entry
            for entry in DailyMoverEntry.objects.filter(snapshot__as_of_date=previous_day)
        }
        prior_streaks: dict[str, int] = {}
        for entry in eligible[:30]:
            symbol = entry[0]
            if symbol not in previous:
                continue
            # The previous entry already contains the length of its consecutive run.
            prior_streaks[symbol] = previous[symbol].consecutive_days

        with transaction.atomic():
            snapshot = DailyMoverSnapshot.objects.create(
                as_of_date=day,
                candle_open_at=datetime.combine(day - timedelta(days=1), datetime.min.time(), UTC),
                scanned_count=len(symbols),
                candidate_count=len(candidates),
                excluded_depth_count=excluded,
                rule_version="daily-mover-v2",
            )
            entries = []
            for rank, row in enumerate(eligible[:30], start=1):
                symbol, quote_asset, candles, signal_name, signal, baseline_change, bid_depth, ask_depth, spread_bps = row
                entries.append(DailyMoverEntry(
                    snapshot=snapshot,
                    rank=rank,
                    symbol=symbol,
                    quote_asset=quote_asset,
                    signal=signal_name,
                    close=candles[-1].close,
                    baseline_change_pct=baseline_change,
                    rise_pct=signal.rise_pct,
                    drawdown_pct=signal.drawdown_pct,
                    low_price=signal.low,
                    high_price=signal.high,
                    low_at=candles[signal.low_index].open_at,
                    high_at=candles[signal.high_index].open_at,
                    bid_depth=bid_depth,
                    ask_depth=ask_depth,
                    spread_bps=spread_bps,
                    consecutive_days=prior_streaks.get(symbol, 0) + 1,
                    previous_rank=previous[symbol].rank if symbol in previous else None,
                    candles=[{
                        "time": candle.open_at.date().isoformat(),
                        "open": str(candle.open),
                        "high": str(candle.high),
                        "low": str(candle.low),
                        "close": str(candle.close),
                    } for candle in candles],
                ))
            DailyMoverEntry.objects.bulk_create(entries)
            run.status = DailyMoverRun.Status.SUCCESS
            run.finished_at = timezone.now()
            run.scanned_count = len(symbols)
            run.candidate_count = len(candidates)
            run.excluded_depth_count = excluded
            run.save(update_fields=["status", "finished_at", "scanned_count", "candidate_count", "excluded_depth_count"])
        if _update_structures(day, source):
            _send_report(snapshot)
        return snapshot
    except Exception as exc:
        DailyMoverRun.objects.filter(as_of_date=day).update(
            status=DailyMoverRun.Status.FAILED,
            finished_at=timezone.now(),
            error_summary=f"{type(exc).__name__}: scan failed"[:300],
        )
        raise
    finally:
        if owns_source:
            source.close()


def _update_structures(day, client):
    # Structure collection is independent: it must never rewrite/fail a published rank.
    import logging
    from .structure_service import update_official
    try:
        _, errors = update_official(day, client=client)
        if errors:
            _retry_structures(day)
            return False
    except Exception as exc:
        logging.getLogger(__name__).warning("结构观察未完成: %s", type(exc).__name__)
        _retry_structures(day)
        return False
    return True


def _send_report(snapshot):
    import logging
    from .models import DailyMoverDigest
    from .report import MAX_ATTEMPTS, send_digest

    try:
        digest = send_digest(snapshot)
        if digest.status == DailyMoverDigest.Status.REJECTED and digest.attempts < MAX_ATTEMPTS:
            _retry_structures(snapshot.as_of_date)
    except Exception as exc:
        logging.getLogger(__name__).warning("异动简报未完成: %s", type(exc).__name__)
        _retry_structures(snapshot.as_of_date)


def _retry_structures(day):
    from .models import DailyMoverSchedule
    retry = timezone.now() + timedelta(minutes=5)
    if retry.astimezone(SHANGHAI).date() == day:
        DailyMoverSchedule.objects.filter(enabled=True).update(next_run_at=retry)

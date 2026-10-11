from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import json

from django.db import transaction

from .models import DailyMoverPreviewReport


def import_preview(path: Path) -> DailyMoverPreviewReport:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rankings = payload.get("provisional_rankings")
    candidates = payload.get("price_candidates")
    if not isinstance(rankings, dict) or not isinstance(candidates, dict) or not rankings:
        raise ValueError("预览文件缺少逐日排行和价格候选")
    days = sorted(date.fromisoformat(value) for value in rankings)
    if set(rankings) != set(candidates):
        raise ValueError("预览文件的日期不一致")
    checked_at = datetime.fromisoformat(payload["scan_completed_at_utc"])
    if checked_at.tzinfo is None:
        raise ValueError("盘口检查时间必须带时区")
    with transaction.atomic():
        report, _ = DailyMoverPreviewReport.objects.update_or_create(
            period_end=days[-1],
            defaults={
                "period_start": days[0],
                "depth_checked_at": checked_at,
                "payload": payload,
            },
        )
    return report


def preview_rows() -> tuple[dict[date, list[SimpleNamespace]], DailyMoverPreviewReport | None]:
    report = DailyMoverPreviewReport.objects.first()
    if report is None:
        return {}, None
    payload = report.payload
    ranked = payload.get("provisional_rankings", {})
    candidates = payload.get("price_candidates", {})
    chart_rows = payload.get("candles", {})
    by_day: dict[date, list[SimpleNamespace]] = {}
    for key in sorted(ranked):
        day = date.fromisoformat(key)
        snapshot = SimpleNamespace(
            preview=True,
            as_of_date=day,
            candle_open_at=datetime.combine(day - timedelta(days=1), time.min, UTC),
            created_at=report.depth_checked_at,
            scanned_count=payload.get("symbol_count", 0),
            candidate_count=len(candidates.get(key, [])),
            excluded_depth_count=sum(
                not row.get("current_depth_pass", False)
                for row in candidates.get(key, [])
            ),
        )
        rows = []
        for item in ranked[key]:
            symbol = str(item["symbol"])
            prior = next(
                (row for row in by_day.get(day - timedelta(days=1), []) if row.symbol == symbol),
                None,
            )
            previous_rank = prior.rank if prior else None
            rank = int(item["preview_rank"])
            change = previous_rank - rank if previous_rank is not None else None
            rows.append(SimpleNamespace(
                snapshot=snapshot,
                rank=rank,
                symbol=symbol,
                quote_asset=item["quote_asset"],
                signal=item["signal"],
                close=Decimal(item["close"]),
                rise_pct=Decimal(item["rise_pct"]),
                drawdown_pct=Decimal(item["drawdown_pct"]),
                change_8d_pct=Decimal(item["change_8d_pct"]),
                low_at=datetime.combine(date.fromisoformat(item["low_at"]), time.min, UTC),
                high_at=datetime.combine(date.fromisoformat(item["high_at"]), time.min, UTC),
                low_price=Decimal(item["low_price"]),
                high_price=Decimal(item["high_price"]),
                bid_depth=Decimal(item["current_bid_depth"]),
                ask_depth=Decimal(item["current_ask_depth"]),
                spread_bps=Decimal(item["current_spread_bps"]),
                previous_rank=previous_rank,
                rank_change=change,
                rank_change_magnitude=abs(change) if change is not None else None,
                consecutive_days=(prior.consecutive_days + 1) if prior else 1,
                candles=chart_rows.get(key, {}).get(symbol, []),
            ))
        by_day[day] = rows
    return by_day, report

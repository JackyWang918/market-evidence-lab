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
    reports = list(DailyMoverPreviewReport.objects.order_by("depth_checked_at", "pk"))
    if not reports:
        return {}, None
    daily_data = {}
    for report in reports:
        payload = report.payload
        for key, ranked_rows in payload.get("provisional_rankings", {}).items():
            daily_data[date.fromisoformat(key)] = (
                report,
                payload,
                ranked_rows,
                payload.get("price_candidates", {}).get(key, []),
                payload.get("candles", {}).get(key, {}),
            )
    by_day: dict[date, list[SimpleNamespace]] = {}
    for day in sorted(daily_data):
        report, payload, ranked_rows, candidates, chart_rows = daily_data[day]
        prior_rows = by_day.get(day - timedelta(days=1))
        prior_by_symbol = {row.symbol: row for row in prior_rows} if prior_rows is not None else {}
        snapshot = SimpleNamespace(
            preview=True,
            as_of_date=day,
            candle_open_at=datetime.combine(day - timedelta(days=1), time.min, UTC),
            created_at=report.depth_checked_at,
            scanned_count=payload.get("symbol_count", 0),
            candidate_count=len(candidates),
            rule_version=payload.get("rule_version", "daily-mover-v1"),
            excluded_depth_count=sum(
                not row.get("current_depth_pass", False)
                for row in candidates
            ),
        )
        rows = []
        for item in ranked_rows:
            symbol = str(item["symbol"])
            prior = prior_by_symbol.get(symbol)
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
                baseline_change_pct=Decimal(item.get("baseline_change_pct", item.get("change_8d_pct"))),
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
                streak_open_start=prior.streak_open_start if prior else prior_rows is None,
                previous_day_unknown=prior_rows is None,
                candles=chart_rows.get(symbol, []),
            ))
        by_day[day] = rows
    return by_day, reports[-1]

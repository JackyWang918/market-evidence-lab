from datetime import date, timedelta
from types import SimpleNamespace

from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .models import DailyMoverEntry, DailyMoverSnapshot
from .preview import preview_rows
from .service import today_in_shanghai


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _board_context(day: date, preview_data=None):
    preview_data = preview_data if preview_data is not None else preview_rows()
    preview_by_day, preview_report = preview_data
    snapshot = DailyMoverSnapshot.objects.filter(as_of_date=day).first()
    if snapshot:
        entries = list(snapshot.entries.all())
    else:
        entries = preview_by_day.get(day, [])
        snapshot = entries[0].snapshot if entries else None
    return {
        "selected_day": day,
        "snapshot": snapshot,
        "entries": entries,
        "preview_checked_at": preview_report.depth_checked_at if snapshot and getattr(snapshot, "preview", False) else None,
        "recent_days": [today_in_shanghai() - timedelta(days=index) for index in range(4, -1, -1)],
        "latest_snapshot": DailyMoverSnapshot.objects.first(),
        "min_depth": settings.DAILY_MOVER_MIN_SIDE_DEPTH,
        "max_spread": settings.DAILY_MOVER_MAX_SPREAD_BPS,
    }


@require_GET
def index(request):
    preview_data = preview_rows()
    requested = _parse_day(request.GET.get("date"))
    today = today_in_shanghai()
    latest_preview_day = max(preview_data[0], default=None)
    day = requested or (
        today if DailyMoverSnapshot.objects.filter(as_of_date=today).exists()
        else latest_preview_day or today
    )
    context = _board_context(day, preview_data)
    context["showing_latest_preview"] = requested is None and day != today
    return render(request, "daily_mover_monitor/index.html", context)


@require_GET
def history(request):
    day = _parse_day(request.GET.get("date"))
    preview_data = preview_rows()
    official_days = set(DailyMoverSnapshot.objects.values_list("as_of_date", flat=True)[:90])
    all_days = sorted(official_days | set(preview_data[0]), reverse=True)
    selected = day or (all_days[0] if all_days else today_in_shanghai())
    context = _board_context(selected, preview_data)
    context["available_dates"] = [SimpleNamespace(as_of_date=value) for value in all_days]
    return render(request, "daily_mover_monitor/history.html", context)


@require_GET
def detail(request, symbol: str):
    day = _parse_day(request.GET.get("date")) or today_in_shanghai()
    entry = DailyMoverEntry.objects.select_related("snapshot").filter(
        snapshot__as_of_date=day, symbol=symbol.upper()
    ).first()
    if entry:
        history_rows = list(
            DailyMoverEntry.objects.filter(symbol=entry.symbol, snapshot__as_of_date__lte=day)
            .select_related("snapshot")
            .order_by("-snapshot__as_of_date")[:30]
        )
    else:
        by_day, _ = preview_rows()
        entry = next((item for item in by_day.get(day, []) if item.symbol == symbol.upper()), None)
        if entry is None:
            raise Http404("没有这一天的合约记录")
        history_rows = [
            item
            for prior_day in sorted((value for value in by_day if value <= day), reverse=True)
            for item in by_day[prior_day]
            if item.symbol == entry.symbol
        ][:30]
    return render(request, "daily_mover_monitor/detail.html", {
        "entry": entry,
        "history_rows": history_rows,
    })

from datetime import date, timedelta

from django.conf import settings
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from .models import DailyMoverEntry, DailyMoverSnapshot
from .service import today_in_shanghai


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _board_context(day: date):
    snapshot = DailyMoverSnapshot.objects.filter(as_of_date=day).first()
    return {
        "selected_day": day,
        "snapshot": snapshot,
        "entries": list(snapshot.entries.all()) if snapshot else [],
        "recent_days": [today_in_shanghai() - timedelta(days=index) for index in range(4, -1, -1)],
        "latest_snapshot": DailyMoverSnapshot.objects.first(),
        "min_depth": settings.DAILY_MOVER_MIN_SIDE_DEPTH,
        "max_spread": settings.DAILY_MOVER_MAX_SPREAD_BPS,
    }


@require_GET
def index(request):
    day = _parse_day(request.GET.get("date")) or today_in_shanghai()
    return render(request, "daily_mover_monitor/index.html", _board_context(day))


@require_GET
def history(request):
    day = _parse_day(request.GET.get("date"))
    snapshots = DailyMoverSnapshot.objects.order_by("-as_of_date")[:90]
    selected = day or (snapshots[0].as_of_date if snapshots else today_in_shanghai())
    context = _board_context(selected)
    context["available_dates"] = snapshots
    return render(request, "daily_mover_monitor/history.html", context)


@require_GET
def detail(request, symbol: str):
    day = _parse_day(request.GET.get("date")) or today_in_shanghai()
    entry = get_object_or_404(
        DailyMoverEntry.objects.select_related("snapshot"),
        snapshot__as_of_date=day,
        symbol=symbol.upper(),
    )
    history_rows = list(
        DailyMoverEntry.objects.filter(symbol=entry.symbol, snapshot__as_of_date__lte=day)
        .select_related("snapshot")
        .order_by("-snapshot__as_of_date")[:30]
    )
    return render(request, "daily_mover_monitor/detail.html", {
        "entry": entry,
        "history_rows": history_rows,
    })

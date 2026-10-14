from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.db.models import OuterRef, Subquery
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .models import DailyMoverEntry, DailyMoverSnapshot, StructureObservation, StructureWatch, TrendObservation, TrendWatch
from .preview import preview_rows
from .service import today_in_shanghai
from .structure import VERSION, unavailable
from .structure_presentation import present_structure
from .trend import VERSION as TREND_VERSION
from .trend_presentation import chart_indicators


def _attach_structures(entries, day):
    observations = StructureObservation.objects.filter(
        as_of_date=day, rule_version=VERSION, watch__symbol__in=[e.symbol for e in entries]
    ).select_related("watch")
    by_key = {(o.watch.symbol, o.watch.origin): o for o in observations}
    for entry in entries:
        origin = "replay" if getattr(entry.snapshot, "preview", False) else "official"
        observation = by_key.get((entry.symbol, origin))
        entry.structure = present_structure(observation.result if observation else unavailable(
            "等待完整结构数据或历史回放", day-timedelta(days=1)))


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _mark_official_streak_boundaries(entries, day: date):
    if not entries:
        return
    needed_days = {
        day - timedelta(days=offset)
        for entry in entries
        for offset in (1, entry.consecutive_days)
    }
    known_days = set(DailyMoverSnapshot.objects.filter(
        as_of_date__in=needed_days
    ).values_list("as_of_date", flat=True))
    for entry in entries:
        entry.previous_day_unknown = day - timedelta(days=1) not in known_days
        entry.streak_open_start = day - timedelta(days=entry.consecutive_days) not in known_days


def _board_context(day: date, preview_data=None):
    preview_data = preview_data if preview_data is not None else preview_rows()
    preview_by_day, _ = preview_data
    snapshot = DailyMoverSnapshot.objects.filter(as_of_date=day).first()
    if snapshot:
        entries = list(snapshot.entries.all())
        _mark_official_streak_boundaries(entries, day)
    else:
        entries = preview_by_day.get(day, [])
        snapshot = entries[0].snapshot if entries else None
    is_current_rule = snapshot is None or snapshot.rule_version == "daily-mover-v2"
    _attach_structures(entries, day)
    today = today_in_shanghai()
    first_offset = 0 if DailyMoverSnapshot.objects.filter(as_of_date=today).exists() else 1
    return {
        "selected_day": day,
        "snapshot": snapshot,
        "entries": entries,
        "preview_checked_at": snapshot.created_at if snapshot and getattr(snapshot, "preview", False) else None,
        "recent_days": [today - timedelta(days=index) for index in range(first_offset+7, first_offset-1, -1)],
        "price_day": day - timedelta(days=1),
        "latest_snapshot": DailyMoverSnapshot.objects.first(),
        "min_depth": settings.DAILY_MOVER_MIN_SIDE_DEPTH,
        "max_spread": settings.DAILY_MOVER_MAX_SPREAD_BPS,
        "is_current_rule": is_current_rule,
        "comparison_days": 10 if is_current_rule else 8,
    }


@require_GET
def index(request):
    preview_data = preview_rows()
    requested = _parse_day(request.GET.get("date"))
    today = today_in_shanghai()
    yesterday = today - timedelta(days=1)
    available_days = {preview_day for preview_day in preview_data[0] if preview_day <= yesterday} | set(
        DailyMoverSnapshot.objects.filter(as_of_date__lte=today).values_list("as_of_date", flat=True)
    )
    day = requested or max(available_days, default=yesterday)
    context = _board_context(day, preview_data)
    return render(request, "daily_mover_monitor/index.html", context)


@require_GET
def history(request):
    day = _parse_day(request.GET.get("date"))
    preview_data = preview_rows()
    official_days = set(DailyMoverSnapshot.objects.values_list("as_of_date", flat=True)[:90])
    all_days = sorted(official_days | set(preview_data[0]), reverse=True)
    selected = day or (all_days[0] if all_days else today_in_shanghai())
    context = _board_context(selected, preview_data)
    quick_days = all_days[:5]
    if selected in all_days and selected not in quick_days:
        quick_days.append(selected)
    context["available_dates"] = [SimpleNamespace(as_of_date=value) for value in quick_days]
    return render(request, "daily_mover_monitor/history.html", context)


@require_GET
def detail(request, symbol: str):
    day = _parse_day(request.GET.get("date")) or today_in_shanghai()
    requested_origin = request.GET.get("origin")
    entry = DailyMoverEntry.objects.select_related("snapshot").filter(
        snapshot__as_of_date=day, symbol=symbol.upper()
    ).first()
    if entry:
        _mark_official_streak_boundaries([entry], day)
        history_rows = list(
            DailyMoverEntry.objects.filter(symbol=entry.symbol, snapshot__as_of_date__lte=day)
            .select_related("snapshot")
            .order_by("-snapshot__as_of_date")[:30]
        )
    else:
        by_day, _ = preview_rows()
        entry = next((item for item in by_day.get(day, []) if item.symbol == symbol.upper()), None)
        history_rows = [
            item
            for prior_day in sorted((value for value in by_day if value <= day), reverse=True)
            for item in by_day[prior_day]
            if item.symbol == symbol.upper()
        ][:30]
    origin = requested_origin or ("replay" if entry and getattr(entry.snapshot, "preview", False) else "official")
    observation = StructureObservation.objects.filter(
        watch__symbol=symbol.upper(), watch__origin=origin, as_of_date__lte=day, rule_version=VERSION,
    ).select_related("watch").first()
    if entry is None:
        if observation is None:
            watch = StructureWatch.objects.filter(symbol=symbol.upper(), origin=origin, discovered_on__lte=day).first()
            if watch is None:
                raise Http404("没有这一天的合约或结构观察记录")
            result = unavailable("等待完整结构数据", day-timedelta(days=1))
        else:
            watch, result = observation.watch, observation.result
        candles = result.get("candles", [])
        entry = SimpleNamespace(
            symbol=watch.symbol, quote_asset=watch.quote_asset, rank=None, signal="结构观察",
            snapshot=SimpleNamespace(as_of_date=day, preview=origin == "replay", rule_version="daily-mover-v2"),
            candles=candles, low_at=datetime.combine(watch.anchor_date, time.min, UTC),
            high_at=datetime.combine(date.fromisoformat(result.get("peak_date", watch.anchor_date.isoformat())), time.min, UTC),
            low_price=watch.anchor_price, high_price=result.get("peak"),
            close=candles[-1]["close"] if candles else None,
        )
    structure = present_structure(observation.result if observation else unavailable(
        "等待完整结构数据或历史回放", day-timedelta(days=1)))
    return render(request, "daily_mover_monitor/detail.html", {
        "entry": entry,
        "history_rows": history_rows,
        "comparison_days": 10 if entry.snapshot.rule_version == "daily-mover-v2" else 8,
        "candle_count": len(structure.get("candles", []) or entry.candles),
        "structure": structure,
        "observation": observation,
        "structure_stale": bool(observation and observation.as_of_date != day),
        "structure_candles": structure.get("candles", []),
        "structure_origin": origin,
        "latest_symbol_day": DailyMoverEntry.objects.filter(symbol=entry.symbol, snapshot__as_of_date__lte=today_in_shanghai()).order_by("-snapshot__as_of_date").values_list("snapshot__as_of_date", flat=True).first(),
        "structure_history": StructureObservation.objects.filter(watch=observation.watch, as_of_date__lte=day, rule_version=VERSION).select_related("watch").order_by("-as_of_date")[:40] if observation else [],
    })


@require_GET
def watchlist(request):
    origin = "replay" if request.GET.get("origin") == "replay" else "official"
    archive = request.GET.get("archive") == "1"
    latest = StructureObservation.objects.filter(watch__origin=origin, rule_version=VERSION).first()
    day = _parse_day(request.GET.get("date")) or (latest.as_of_date if latest else today_in_shanghai())
    query = request.GET.get("symbol", "").strip().upper()[:30]
    watches = StructureWatch.objects.filter(origin=origin, discovered_on__lte=day)
    if query:
        watches = watches.filter(symbol__icontains=query)
    latest_ids = watches.annotate(latest_observation=Subquery(
        StructureObservation.objects.filter(watch_id=OuterRef("pk"), as_of_date__lte=day, rule_version=VERSION).values("pk")[:1]
    )).values_list("latest_observation", flat=True)
    observations = {o.watch_id: o for o in StructureObservation.objects.filter(pk__in=latest_ids)}
    rows = []
    for watch in watches:
        observation = observations.get(watch.pk)
        ended = bool(watch.ended_on and watch.ended_on <= day)
        if ended != archive:
            continue
        if request.GET.get("dropped") == "1" and observation and observation.result.get("ranked"):
            continue
        rows.append({"watch": watch, "observation": observation, "ended": ended,
                     "stale": bool(observation and observation.as_of_date < day),
                     "structure": present_structure(observation.result if observation else unavailable("等待采集结构日 K", day-timedelta(days=1)))})
    return render(request, "daily_mover_monitor/watchlist.html", {
        "rows": rows, "origin": origin, "selected_day": day,
        "archive": archive,
        "symbol_query": query, "dropped": request.GET.get("dropped") == "1",
    })


@require_GET
def trend_list(request):
    day = _parse_day(request.GET.get("date")) or today_in_shanghai()
    archive = request.GET.get("archive") == "1"
    query = request.GET.get("symbol", "").strip().upper()[:30]
    watches = TrendWatch.objects.filter(rule_version=TREND_VERSION, discovered_on__lte=day)
    if query:
        watches = watches.filter(symbol__icontains=query)
    rows = []
    for watch in watches:
        ended = bool(watch.archived_on and watch.archived_on <= day)
        if ended != archive:
            continue
        observation = watch.observations.filter(as_of_date__lte=day, rule_version=TREND_VERSION).first()
        recent = observation.result.get("candles", [])[-30:] if observation else []
        first_close = Decimal(recent[0]["close"]) if recent else Decimal(0)
        last_close = Decimal(recent[-1]["close"]) if recent else Decimal(0)
        rows.append({"watch": watch, "observation": observation, "ended": ended,
                     "stale": bool(observation and observation.as_of_date < day),
                     "sparkline": ",".join(candle["close"] for candle in recent),
                     "sparkline_count": len(recent),
                     "change_30_pct": (last_close / first_close - 1) * 100 if first_close > 0 and len(recent) > 1 else None})
    rows.sort(key=lambda row: (row["observation"].as_of_date if row["observation"] else date.min,
                               row["watch"].discovered_on), reverse=True)
    return render(request, "daily_mover_monitor/trends.html", {
        "rows": rows, "selected_day": day, "archive": archive, "symbol_query": query,
    })


@require_GET
def trend_detail(request, symbol: str):
    day = _parse_day(request.GET.get("date")) or today_in_shanghai()
    watch = TrendWatch.objects.filter(symbol=symbol.upper(), rule_version=TREND_VERSION,
                                      discovered_on__lte=day).first()
    if watch is None:
        raise Http404("没有该合约的趋势观察记录")
    observations = list(watch.observations.filter(as_of_date__lte=day, rule_version=TREND_VERSION).order_by("-as_of_date")[:90])
    latest = observations[0] if observations else None
    chart_candles = latest.result.get("candles", [])[-180:] if latest else []
    chart_events = [{"date": item.result["cutoff"], "stage": item.result["stage"],
                     "stage_changed": item.result.get("stage_changed", False),
                     "new14": item.result.get("new_close_high_14d", False),
                     "new30": item.result.get("new_close_high_30d", False),
                     "center": item.result.get("center_10d"),
                     "ranked": item.result.get("ranked", False)}
                    for item in reversed(observations)]
    return render(request, "daily_mover_monitor/trend_detail.html", {
        "watch": watch, "observations": observations, "latest": latest,
        "selected_day": day, "chart_candles": chart_candles, "chart_events": chart_events,
        "chart_indicators": chart_indicators(chart_candles),
        "discovery_candle_date": watch.discovered_on - timedelta(days=1),
    })

"""Read-only chart payloads from the published four-asset research snapshot."""
from datetime import UTC, datetime, timedelta
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET
from .models import DailyCandle, ResearchEvent, ResearchRun
from .views import selected_asset


def latest_snapshot():
    return ResearchRun.objects.filter(status__in=['success', 'partial']).first()


def event_payload(event):
    return {
        'id': event.pk, 'event_id': event.event_id, 'asset': event.asset,
        'scale': event.scale, 'known_at': event.known_at.astimezone(UTC).isoformat(),
        'status': event.status, 'status_label': event.get_status_display(),
        'facts': event.payload, 'observations': event.observations, 'outcomes': event.outcomes,
    }


@require_GET
def charts(request):
    asset = selected_asset(request)
    period = request.GET.get('period', '365')
    scale = request.GET.get('scale', 'W' if asset == 'XAU' else 'D')
    if period not in ('90', '365', '730', 'all') or scale not in (('W', 'M') if asset == 'XAU' else ('D', 'W')):
        raise Http404('Invalid chart range or research scale')
    latest = latest_snapshot()
    coverage = next((row for row in latest.payload.get('coverage', []) if row['asset'] == asset), None) if latest else None
    candles = DailyCandle.objects.none()
    events = ResearchEvent.objects.none()
    if latest and coverage:
        candles = DailyCandle.objects.filter(asset=asset, date__lt=latest.cutoff_date).order_by('date')
        latest_date = candles.order_by('-date').values_list('date', flat=True).first()
        if latest_date and period != 'all':
            candles = candles.filter(date__gte=latest_date-timedelta(days=int(period)-1))
        first_date = candles.values_list('date', flat=True).first()
        events = ResearchEvent.objects.filter(run=latest, asset=asset, scale=scale)
        if first_date:
            events = events.filter(known_at__gte=datetime.combine(first_date, datetime.min.time(), UTC))
    events = list(events)
    selected = events[0] if events else None
    requested = request.GET.get('event')
    if requested:
        if not requested.isdigit(): raise Http404('Invalid event')
        selected = next((event for event in events if event.pk == int(requested)), None)
        if selected is None: raise Http404('Event outside this chart snapshot or asset')
    series = [{'date': row.date.isoformat(), 'open': row.open, 'high': row.high, 'low': row.low, 'close': row.close} for row in candles]
    payload = {
        'asset': asset, 'scale': scale, 'period': period, 'candles': series,
        # Mark the start neutrally; the final accepted state was not known at the start.
        'events': [{'id': event.pk, 'event_id': event.event_id, 'known_at': event.known_at.astimezone(UTC).isoformat(), 'level': event.payload.get('level')} for event in events],
        'selected': event_payload(selected) if selected else None,
        'summary': [row for row in latest.payload.get('summary', []) if row['asset'] == asset and row['scale'] == scale] if latest else [],
    }
    missing = next((row for row in latest.payload.get('manifest', {}).get('missing_assets', []) if row['asset'] == asset), None) if latest else None
    return render(request, 'breakout_acceptance/charts.html', {
        'asset': asset, 'assets': ('SOL', 'BNB', 'ZEC', 'XAU'), 'period': period, 'scale': scale,
        'latest': latest, 'coverage': coverage, 'missing': missing, 'events': events, 'selected_event': selected,
        'chart_payload': payload,
    })


@require_GET
def chart_event(request, pk):
    asset = selected_asset(request)
    latest = latest_snapshot()
    event = get_object_or_404(ResearchEvent, pk=pk, run=latest, asset=asset)
    return JsonResponse(event_payload(event))

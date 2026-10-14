from __future__ import annotations
from datetime import UTC, datetime, timedelta
import json, shutil
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import ASSETS, DailyCandle, ResearchEvent, ResearchRun, ResearchSchedule
from apps.scheduling.services import calculate_next_run_at

VERSION = 'breakout_acceptance_v1'

def storage_root():
    return Path(settings.BASE_DIR) / 'data' / 'breakout_acceptance'

def get_schedule():
    from datetime import time
    schedule, _ = ResearchSchedule.objects.get_or_create(pk=1, defaults={
        'next_run_at': calculate_next_run_at(time(8, 20)),
    })
    return schedule

def queue_run(*, trigger='manual', cutoff=None):
    get_schedule()
    with transaction.atomic():
        ResearchSchedule.objects.select_for_update().get(pk=1)
        current = ResearchRun.objects.filter(status__in=['queued', 'running']).first()
        if current is not None:
            return current, False
        return ResearchRun.objects.create(trigger=trigger, cutoff_date=cutoff or timezone.now().astimezone(UTC).date()), True

def claim_work(*, now=None):
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise ValueError('now must be timezone-aware')
    get_schedule()
    with transaction.atomic():
        schedule = ResearchSchedule.objects.select_for_update().get(pk=1)
        schedule.worker_heartbeat_at = now
        current = ResearchRun.objects.filter(status='running').first()
        if current is not None:
            schedule.save(update_fields=['worker_heartbeat_at'])
            return None
        run = ResearchRun.objects.filter(status='queued').order_by('pk').first()
        if schedule.enabled and schedule.next_run_at <= now:
            if run is None:
                run = ResearchRun.objects.create(trigger='scheduled', cutoff_date=now.astimezone(UTC).date())
            schedule.last_run_at = now
            schedule.next_run_at = calculate_next_run_at(schedule.run_time, after=now)
        schedule.save(update_fields=['worker_heartbeat_at', 'last_run_at', 'next_run_at'])
        if run is None:
            return None
        run.status = 'running'
        run.started_at = now
        run.save(update_fields=['status', 'started_at'])
        return run.pk

def _records(frame):
    return json.loads(frame.to_json(orient='records', date_format='iso'))

def execute_run(run_id, *, offline=False, heartbeat_callback=None):
    # Each run owns immutable outputs. Failed runs never replace published data.
    import pandas as pd
    from .engine import collect, research
    from .report import build
    run = ResearchRun.objects.get(pk=run_id)
    if run.status != 'running':
        raise ValueError('run must first be claimed')
    root = storage_root(); snapshot = root / 'runs' / str(run.pk)
    snapshot.mkdir(parents=True, exist_ok=True)
    if (root/'raw').exists():
        shutil.copytree(root/'raw', snapshot/'raw', dirs_exist_ok=True)
    def pulse():
        ResearchSchedule.objects.filter(pk=1).update(worker_heartbeat_at=timezone.now())
        if heartbeat_callback: heartbeat_callback()
    try:
        pulse()
        end = pd.Timestamp(run.cutoff_date, tz='UTC')
        datasets = collect(end, offline=offline, root=snapshot)
        if not datasets or any(asset not in ASSETS for asset in datasets):
            raise ValueError('Unexpected asset universe')
        pulse()
        research(datasets, end, root=snapshot)
        pulse()
        build(root=snapshot)
        pulse()
        manifest = json.loads((snapshot/'data'/'manifest.json').read_text())
        events = _records(pd.read_csv(snapshot/'reports'/'events.csv'))
        tracks = _records(pd.read_csv(snapshot/'reports'/'daily_observations.csv'))
        outcomes = _records(pd.read_csv(snapshot/'reports'/'outcomes.csv'))
        by_track = {}; by_outcome = {}
        for item in tracks: by_track.setdefault(item['event_id'], []).append(item)
        for item in outcomes: by_outcome.setdefault(item['event_id'], []).append(item)
        event_rows=[]; candles=[]
        for e in events:
            if e['asset'] not in ASSETS: raise ValueError('Unexpected event asset')
            event_rows.append(ResearchEvent(run=run, event_id=e['event_id'], asset=e['asset'], scale=e['scale'], known_at=datetime.fromisoformat(e['known_at_utc']), status=e['status'], payload=e, observations=by_track.get(e['event_id'], []), outcomes=by_outcome.get(e['event_id'], [])))
        coverage=[]
        for item in manifest['coverage']:
            item['lag_calendar_days']=(run.cutoff_date-datetime.fromisoformat(item['end']).date()).days-1
            coverage.append(item)
        for asset, frame in datasets.items():
            for date, row in frame.iterrows():
                candles.append(DailyCandle(asset=asset, date=date.date(), open=float(row.open), high=float(row.high), low=float(row.low), close=float(row.close), source='dukascopy_bid' if asset=='XAU' else 'binance_spot'))
        payload={'version':VERSION, 'coverage':coverage, 'manifest':manifest,
                 'summary':_records(pd.read_csv(snapshot/'reports'/'summary.csv')),
                 'weekly_review':_records(pd.read_csv(snapshot/'reports'/'weekly_review.csv')),
                 'cluster_intervals':_records(pd.read_csv(snapshot/'reports'/'cluster_intervals.csv')),
                 'event_count':len(events),'observation_count':len(tracks)}
        partial=bool(manifest['missing_assets']) or manifest['quarantined_rows']>0 or any(x['lag_calendar_days']>0 for x in coverage)
        shutil.copytree(snapshot/'raw', root/'raw', dirs_exist_ok=True)
        with transaction.atomic():
            DailyCandle.objects.bulk_create(candles, batch_size=500, update_conflicts=True, update_fields=['open','high','low','close','source'], unique_fields=['asset','date'])
            ResearchEvent.objects.bulk_create(event_rows, batch_size=50)
            run.payload=payload;run.snapshot_path=str(snapshot.relative_to(Path(settings.BASE_DIR)))
            run.status='partial' if partial else 'success';run.finished_at=timezone.now();run.safe_error=''
            run.save(update_fields=['payload','snapshot_path','status','finished_at','safe_error'])
            ResearchSchedule.objects.filter(pk=1).update(last_success_at=run.finished_at,last_error='')
        return run
    except Exception as exc:
        error=f'{type(exc).__name__}: 数据采集或研究计算失败；保留上一成功结果，请查看服务端日志。'
        ResearchRun.objects.filter(pk=run.pk).update(status='failed',finished_at=timezone.now(),safe_error=error)
        ResearchSchedule.objects.filter(pk=1).update(last_error=error)
        import logging
        logging.getLogger(__name__).exception('Breakout research run %s failed',run.pk)
        raise

def execute_due(*, heartbeat_callback=None):
    pk=claim_work()
    if pk is not None:
        return execute_run(pk,heartbeat_callback=heartbeat_callback)

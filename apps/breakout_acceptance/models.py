from datetime import time
from django.db import models

ASSETS = ('SOL', 'BNB', 'ZEC', 'XAU')
ASSET_CHOICES = [(asset, asset) for asset in ASSETS]

class ResearchSchedule(models.Model):
    enabled = models.BooleanField(default=True)
    run_time = models.TimeField(default=time(8, 20))
    timezone = models.CharField(max_length=64, default='Asia/Shanghai', editable=False)
    next_run_at = models.DateTimeField()
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    worker_heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)

class ResearchRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = 'queued', '等待执行'
        RUNNING = 'running', '采集与计算中'
        SUCCESS = 'success', '完成'
        PARTIAL = 'partial', '完成（数据滞后或质量问题）'
        FAILED = 'failed', '失败'
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    trigger = models.CharField(max_length=16, default='manual')
    active_slot = models.PositiveSmallIntegerField(default=1, editable=False)
    cutoff_date = models.DateField()
    requested_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    snapshot_path = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict)
    safe_error = models.CharField(max_length=500, blank=True)
    class Meta:
        ordering = ['-requested_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['active_slot'], condition=models.Q(status__in=['queued', 'running']), name='breakout_one_active_run')]

class DailyCandle(models.Model):
    asset = models.CharField(max_length=8, choices=ASSET_CHOICES)
    date = models.DateField()
    open = models.FloatField()
    high = models.FloatField()
    low = models.FloatField()
    close = models.FloatField()
    source = models.CharField(max_length=32)
    class Meta:
        ordering = ['-date']
        constraints = [
            models.UniqueConstraint(fields=['asset', 'date'], name='breakout_asset_day_unique'),
            models.CheckConstraint(condition=models.Q(asset__in=ASSETS), name='breakout_candle_asset_allowed'),
        ]

class ResearchEvent(models.Model):
    run = models.ForeignKey(ResearchRun, on_delete=models.CASCADE, related_name='events')
    event_id = models.CharField(max_length=64)
    asset = models.CharField(max_length=8, choices=ASSET_CHOICES)
    scale = models.CharField(max_length=1)
    known_at = models.DateTimeField()
    status = models.CharField(max_length=24, choices=[('accepted','接受'),('not_accepted','未确认接受'),('pending','待确认'),('data_gap','数据窗口缺陷')])
    payload = models.JSONField(default=dict)
    observations = models.JSONField(default=list)
    outcomes = models.JSONField(default=list)
    class Meta:
        ordering = ['-known_at', 'asset', 'scale']
        constraints = [
            models.UniqueConstraint(fields=['run', 'event_id'], name='breakout_event_run_unique'),
            models.CheckConstraint(condition=models.Q(asset__in=ASSETS), name='breakout_event_asset_allowed'),
        ]

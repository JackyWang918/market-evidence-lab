from datetime import time

from django.db import models


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise RuntimeError("已发布的日榜快照不可修改")

    def delete(self):
        raise RuntimeError("已发布的日榜快照不可删除")

    def bulk_update(self, objs, fields, batch_size=None):
        raise RuntimeError("已发布的日榜快照不可修改")


ImmutableManager = models.Manager.from_queryset(ImmutableQuerySet)


class DailyMoverSchedule(models.Model):
    enabled = models.BooleanField(default=True)
    run_time = models.TimeField(default=time(8, 20))
    next_run_at = models.DateTimeField()
    last_run_at = models.DateTimeField(null=True, blank=True)


class DailyMoverRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "运行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    as_of_date = models.DateField(unique=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    scanned_count = models.PositiveIntegerField(default=0)
    candidate_count = models.PositiveIntegerField(default=0)
    excluded_depth_count = models.PositiveIntegerField(default=0)
    error_summary = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-as_of_date"]


class DailyMoverPreviewReport(models.Model):
    """Labeled historical trial; deliberately separate from daily snapshots."""

    period_start = models.DateField()
    period_end = models.DateField(unique=True)
    depth_checked_at = models.DateTimeField()
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end"]


class DailyMoverSnapshot(models.Model):
    objects = ImmutableManager()
    as_of_date = models.DateField(unique=True)
    candle_open_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    scanned_count = models.PositiveIntegerField()
    candidate_count = models.PositiveIntegerField()
    excluded_depth_count = models.PositiveIntegerField()
    rule_version = models.CharField(max_length=30, default="daily-mover-v1")

    class Meta:
        ordering = ["-as_of_date"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("已发布的日榜快照不可修改")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("已发布的日榜快照不可删除")


class DailyMoverEntry(models.Model):
    objects = ImmutableManager()
    snapshot = models.ForeignKey(
        DailyMoverSnapshot, on_delete=models.PROTECT, related_name="entries"
    )
    rank = models.PositiveSmallIntegerField()
    symbol = models.CharField(max_length=30)
    quote_asset = models.CharField(max_length=10)
    signal = models.CharField(max_length=12)
    close = models.DecimalField(max_digits=40, decimal_places=18)
    baseline_change_pct = models.DecimalField(max_digits=18, decimal_places=6)
    rise_pct = models.DecimalField(max_digits=18, decimal_places=6)
    drawdown_pct = models.DecimalField(max_digits=18, decimal_places=6)
    low_price = models.DecimalField(max_digits=40, decimal_places=18)
    high_price = models.DecimalField(max_digits=40, decimal_places=18)
    low_at = models.DateTimeField()
    high_at = models.DateTimeField()
    bid_depth = models.DecimalField(max_digits=40, decimal_places=8)
    ask_depth = models.DecimalField(max_digits=40, decimal_places=8)
    spread_bps = models.DecimalField(max_digits=18, decimal_places=6)
    consecutive_days = models.PositiveIntegerField()
    previous_rank = models.PositiveSmallIntegerField(null=True)
    candles = models.JSONField()

    class Meta:
        ordering = ["rank"]
        constraints = [
            models.UniqueConstraint(fields=["snapshot", "symbol"], name="daily_mover_unique_symbol"),
            models.UniqueConstraint(fields=["snapshot", "rank"], name="daily_mover_unique_rank"),
        ]
        indexes = [models.Index(fields=["symbol", "-snapshot"], name="daily_mover_symbol_idx")]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("已发布的日榜快照不可修改")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("已发布的日榜快照不可删除")

    @property
    def rank_change(self) -> int | None:
        return self.previous_rank - self.rank if self.previous_rank is not None else None

    @property
    def rank_change_magnitude(self) -> int | None:
        change = self.rank_change
        return abs(change) if change is not None else None


class StructureWatch(models.Model):
    """Separate cycles and provenance; leaving the board does not end a watch."""
    symbol = models.CharField(max_length=30)
    quote_asset = models.CharField(max_length=10)
    origin = models.CharField(max_length=10, choices=[("official", "正式跟踪"), ("replay", "历史回放")])
    discovered_on = models.DateField()
    last_ranked_on = models.DateField()
    anchor_date = models.DateField()
    anchor_price = models.DecimalField(max_digits=40, decimal_places=18)
    ended_on = models.DateField(null=True, blank=True)
    end_reason = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["symbol", "origin", "discovered_on"], name="structure_watch_cycle_unique")]
        ordering = ["-discovered_on", "symbol"]


class StructureObservation(models.Model):
    objects = ImmutableManager()
    watch = models.ForeignKey(StructureWatch, on_delete=models.PROTECT, related_name="observations")
    as_of_date = models.DateField()
    rule_version = models.CharField(max_length=30)
    data_cutoff_at = models.DateTimeField()
    generated_at = models.DateTimeField(auto_now_add=True)
    input_hash = models.CharField(max_length=64)
    result = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["watch", "as_of_date", "rule_version"], name="structure_observation_unique")]
        ordering = ["-as_of_date", "-pk"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("已发布的结构判断不可修改")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("已发布的结构判断不可删除")


class DailyMoverDigest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "待发送"
        SENDING = "sending", "发送中"
        SENT = "sent", "已发送"
        REJECTED = "rejected", "被拒绝"
        UNKNOWN = "unknown", "发送结果待核对"
        NOT_CONFIGURED = "not_configured", "未配置"

    snapshot = models.OneToOneField(
        DailyMoverSnapshot, on_delete=models.PROTECT, related_name="digest"
    )
    content = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    error_summary = models.CharField(max_length=300, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

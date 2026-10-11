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
    change_8d_pct = models.DecimalField(max_digits=18, decimal_places=6)
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

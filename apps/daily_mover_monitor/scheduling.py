from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from .models import DailyMoverSchedule, DailyMoverSnapshot
from .service import scan, today_in_shanghai


ZONE = ZoneInfo("Asia/Shanghai")
RUN_TIME = time(8, 20)


def next_run(after: datetime) -> datetime:
    local = after.astimezone(ZONE)
    candidate = datetime.combine(local.date(), RUN_TIME, ZONE)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate


def get_schedule() -> DailyMoverSchedule:
    now = timezone.now()
    local = now.astimezone(ZONE)
    due_today = local.time() >= RUN_TIME and not DailyMoverSnapshot.objects.filter(
        as_of_date=local.date()
    ).exists()
    schedule, _ = DailyMoverSchedule.objects.get_or_create(
        pk=1,
        defaults={"enabled": True, "run_time": RUN_TIME, "next_run_at": now if due_today else next_run(now)},
    )
    return schedule


def claim_due() -> bool:
    now = timezone.now()
    with transaction.atomic():
        schedule = DailyMoverSchedule.objects.select_for_update().filter(
            pk=1, enabled=True, next_run_at__lte=now
        ).first()
        if schedule is None:
            return False
        schedule.last_run_at = now
        schedule.next_run_at = next_run(now)
        schedule.save(update_fields=["last_run_at", "next_run_at"])
    return True


def execute_due() -> None:
    if claim_due():
        try:
            scan()
        except Exception:
            now = timezone.now()
            retry_at = now + timedelta(minutes=5)
            if retry_at.astimezone(ZONE).date() == today_in_shanghai():
                DailyMoverSchedule.objects.filter(pk=1, enabled=True).update(next_run_at=retry_at)
            raise

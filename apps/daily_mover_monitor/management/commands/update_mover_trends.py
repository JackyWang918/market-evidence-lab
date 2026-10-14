from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from datetime import time
from zoneinfo import ZoneInfo

from apps.daily_mover_monitor.service import today_in_shanghai
from apps.daily_mover_monitor.trend_service import update_official


class Command(BaseCommand):
    help = "Sweep today's early candidates and update independent trend watches."

    def handle(self, *args, **options):
        if timezone.now().astimezone(ZoneInfo("Asia/Shanghai")).time() < time(8, 20):
            raise CommandError("须在北京时间 08:20 后采集上一根完整 UTC 日 K")
        count, errors = update_official(today_in_shanghai())
        self.stdout.write(f"新增趋势观察 {count} 条；失败 {len(errors)} 个")
        for message in errors:
            self.stderr.write(message)
        if errors:
            raise CommandError("部分趋势数据未取得；可重试，已有观察不会覆盖")

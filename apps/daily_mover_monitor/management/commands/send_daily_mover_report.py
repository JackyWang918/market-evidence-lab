from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.daily_mover_monitor.models import DailyMoverSnapshot
from apps.daily_mover_monitor.report import build_digest, send_digest


class Command(BaseCommand):
    help = "Preview or send one official daily mover attention digest."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=date.fromisoformat, help="榜单日期 YYYY-MM-DD；默认最新正式榜单")
        parser.add_argument("--dry-run", action="store_true", help="只打印报告，不发送")

    def handle(self, *args, **options):
        snapshot = (DailyMoverSnapshot.objects.filter(as_of_date=options["date"]).first()
                    if options["date"] else DailyMoverSnapshot.objects.first())
        if snapshot is None:
            raise CommandError("没有可用的正式榜单")
        try:
            if options["dry_run"]:
                self.stdout.write(build_digest(snapshot))
                return
            digest = send_digest(snapshot)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"{snapshot.as_of_date}: {digest.get_status_display()}，发送尝试 {digest.attempts} 次")
        if digest.status not in (digest.Status.SENT, digest.Status.NOT_CONFIGURED):
            raise CommandError(digest.error_summary or "报告未送达")

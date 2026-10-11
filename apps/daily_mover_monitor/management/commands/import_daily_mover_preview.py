from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.daily_mover_monitor.preview import import_preview


class Command(BaseCommand):
    help = "Import a clearly labeled historical trial into the preview area."

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path)

    def handle(self, *args, **options):
        path = options["path"]
        if not path.is_file():
            raise CommandError(f"预览文件不存在：{path}")
        try:
            report = import_preview(path)
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandError(f"预览文件无效：{type(exc).__name__}") from exc
        self.stdout.write(self.style.SUCCESS(
            f"已载入 {report.period_start} 至 {report.period_end} 的历史试算；正式快照未修改。"
        ))

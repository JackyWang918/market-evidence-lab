from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.daily_mover_monitor.service import today_in_shanghai
from apps.daily_mover_monitor.structure_service import replay, update_official


class Command(BaseCommand):
    help = "Update official daily structures or explicitly labeled causal historical replays."

    def add_arguments(self, parser):
        parser.add_argument("--replay", action="store_true")
        parser.add_argument("--symbol", help="Limit historical replay to one symbol")
        parser.add_argument("--end", type=date.fromisoformat, help="Replay through board date YYYY-MM-DD")

    def handle(self, *args, **options):
        if (options["symbol"] or options["end"]) and not options["replay"]:
            raise CommandError("--symbol / --end 仅用于 --replay")
        if options["replay"]:
            count, errors = replay(symbol=options["symbol"].upper() if options["symbol"] else None, end=options["end"])
        else:
            count, errors = update_official(today_in_shanghai())
        self.stdout.write(f"结构观察处理 {count} 条；失败 {len(errors)} 个")
        for message in errors:
            self.stderr.write(message)
        if errors:
            raise CommandError("部分结构数据未取得，可重试；现有榜单和已保存判断未修改")

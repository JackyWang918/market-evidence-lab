from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...experiment import save_replay


class Command(BaseCommand):
    help = "Replay continuity_v1 over the saved BTCUSDT 5m candles and retain every prediction."

    def add_arguments(self, parser):
        parser.add_argument("--source", type=Path, default=None)

    def handle(self, *args, **options):
        data_dir = Path(settings.BASE_DIR) / "data" / "btc_prediction"
        source = options["source"] or data_dir / "btcusdt_5m_10000.csv"
        if not source.is_file():
            raise CommandError(f"Source CSV not found: {source}")
        try:
            records_path, _, summary = save_replay(source, data_dir / "experiments")
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"{summary['prediction_count']} predictions: {summary['wins']} wins, "
            f"{summary['losses']} losses, {summary['pending']} pending; "
            f"accuracy={summary['accuracy_pct']}%; records={records_path}"
        ))

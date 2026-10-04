from pathlib import Path

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ...archive import download_latest_complete_days


class Command(BaseCommand):
    help = "Download checksum-verified BTCUSDT 5m public archives to a local CSV."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=10_000)

    def handle(self, *args, **options):
        count = options["count"]
        if count < 2 or count > 100_000:
            raise CommandError("--count must be between 2 and 100000")
        output = Path(settings.BASE_DIR) / "data" / "btc_prediction" / f"btcusdt_5m_{count}.csv"
        try:
            start, end = download_latest_complete_days(count, output, timezone.now())
        except (httpx.HTTPError, ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Saved {count} verified candles to {output} ({start.isoformat()} to {end.isoformat()} exclusive)"
        ))
